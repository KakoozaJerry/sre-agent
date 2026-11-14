"""Kubernetes read adapter: list pods in one namespace (read-only).

Part of the read adapters package (see the package docstring for
the shared safety rules). This module is the Kubernetes piece:
the pure parser for a ``PodList`` reply, the checked request and
result shapes, an in-memory demo backend, the live API backend,
and the reader that turns a pod listing into evidence.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field, field_validator

from ..backend_url import validate_backend_url
from ..logging_config import get_logger
from ..models import Evidence
from ..tools import MAX_EVIDENCE_SUMMARY_CHARS, redact_secrets
from ._common import _bound

log = get_logger(__name__)

MAX_PODS = 50
MAX_NAMESPACE_LENGTH = 63

_NAMESPACE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


class PodStatus(BaseModel):
    """One pod, as a person scanning ``kubectl get pods`` sees it."""

    name: str
    namespace: str
    phase: str  # Running, Pending, Failed, Succeeded, Unknown, …
    ready_containers: int = Field(default=0, ge=0)
    total_containers: int = Field(default=0, ge=0)
    restarts: int = Field(default=0, ge=0)
    service: str = ""  # the app label, if the pod carries one


def parse_kubernetes_pods(payload: dict[str, object]) -> list[PodStatus]:
    """Turn a Kubernetes ``PodList`` reply into pod records.

    Pure function, tested against a recorded reply shape. A reply
    that is not a list of items gives back no pods. Container
    readiness and restarts come from ``status.containerStatuses``,
    exactly where the API puts them; a pod with no container
    statuses yet (still being created) reads as 0 of however many
    containers its spec asks for.
    """
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    pods: list[PodStatus] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        labels = metadata.get("labels")
        service = ""
        if isinstance(labels, dict):
            service = str(
                labels.get("app") or labels.get("app.kubernetes.io/name") or ""
            )
        spec = item.get("spec")
        total = 0
        if isinstance(spec, dict) and isinstance(spec.get("containers"), list):
            total = len(spec["containers"])
        status = item.get("status")
        phase = "Unknown"
        ready = 0
        restarts = 0
        if isinstance(status, dict):
            phase = str(status.get("phase", "Unknown"))
            container_statuses = status.get("containerStatuses")
            if isinstance(container_statuses, list):
                total = total or len(container_statuses)
                for entry in container_statuses:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("ready") is True:
                        ready += 1
                    try:
                        restarts += int(entry.get("restartCount", 0))
                    except (TypeError, ValueError):
                        continue
        pods.append(
            PodStatus(
                name=str(metadata.get("name", "unknown")),
                namespace=str(metadata.get("namespace", "default")),
                phase=phase,
                ready_containers=ready,
                total_containers=total,
                restarts=restarts,
                service=service,
            )
        )
    return pods


class ListPodsRequest(BaseModel):
    """A pod listing. Checked before it is allowed to run."""

    namespace: str
    service: str | None = None
    limit: int = Field(default=20, ge=1, le=MAX_PODS)

    @field_validator("namespace")
    @classmethod
    def _namespace_must_be_a_kubernetes_name(cls, v: str) -> str:
        name = v.strip()
        if not name or len(name) > MAX_NAMESPACE_LENGTH or not _NAMESPACE_RE.match(name):
            raise ValueError(f"not a valid Kubernetes namespace name: {v!r}")
        return name


class KubernetesPodsResult(BaseModel):
    namespace: str
    service: str | None = None
    pods: list[PodStatus] = Field(default_factory=list)
    total_found: int = 0
    truncated: bool = False

    def to_evidence(self) -> Evidence:
        """Turn this listing into an evidence record for an incident."""
        query = f"kubernetes pods: namespace={self.namespace}"
        if self.service:
            query += f" app={self.service}"
        if not self.pods:
            summary = f"No pods found in namespace {self.namespace}"
            if self.service:
                summary += f" for app {self.service}"
        else:
            parts = [
                f"{p.name} {p.phase} {p.ready_containers}/{p.total_containers} ready, "
                f"{p.restarts} restart(s)"
                for p in self.pods[:3]
            ]
            summary = "; ".join(parts)
            if self.total_found > len(self.pods):
                summary += f"; +{self.total_found - len(self.pods)} more pod(s)"
            if self.truncated:
                summary += " [truncated]"
        return Evidence(
            kind="kubernetes",
            query=query,
            summary=_bound(redact_secrets(summary), MAX_EVIDENCE_SUMMARY_CHARS),
        )


class InMemoryKubernetesBackend:
    """Demo Kubernetes backend: pods held in memory.

    ``add_pod`` exists only so a demo or a test can seed data.
    Listing is read-only.
    """

    def __init__(self, pods: list[PodStatus] | None = None) -> None:
        self._pods: list[PodStatus] = list(pods or [])

    def add_pod(self, pod: PodStatus) -> None:
        self._pods.append(pod)

    def fetch(self, namespace: str, service: str | None) -> list[PodStatus]:
        return [
            p
            for p in self._pods
            if p.namespace == namespace and (service is None or p.service == service)
        ]


class KubernetesApiBackend:
    """Kubernetes backend that asks a real cluster's API server.

    Uses only the standard library. The bearer token, when the
    cluster needs one, is passed in by the caller and is only
    ever put in the request header — never logged, never stored
    on a result. Listing pods is a read; this backend builds no
    other kind of request.
    """

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout_seconds: float = 5.0,
        allowed_hosts: list[str] | None = None,
    ) -> None:
        self._base_url = validate_backend_url(
            base_url, allowed_hosts=allowed_hosts, what="Kubernetes API address"
        )
        self._token = token
        self._timeout = timeout_seconds

    def fetch(self, namespace: str, service: str | None) -> list[PodStatus]:
        path = f"/api/v1/namespaces/{urllib.parse.quote(namespace)}/pods"
        if service:
            selector = urllib.parse.urlencode({"labelSelector": f"app={service}"})
            path += f"?{selector}"
        request = urllib.request.Request(
            f"{self._base_url}{path}", headers={"Accept": "application/json"}
        )
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        with urllib.request.urlopen(request, timeout=self._timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            return []
        return parse_kubernetes_pods(payload)


class KubernetesReader:
    """The Kubernetes read tool, with bounds applied in one place."""

    def __init__(
        self, backend: InMemoryKubernetesBackend | KubernetesApiBackend | None = None
    ) -> None:
        self._backend = backend if backend is not None else InMemoryKubernetesBackend()

    def list_pods(self, request: ListPodsRequest) -> KubernetesPodsResult:
        found = self._backend.fetch(request.namespace, request.service)
        truncated = len(found) > request.limit
        kept = found[: request.limit]
        log.info(
            "list_pods namespace=%s service=%s -> %d pod(s)",
            request.namespace,
            request.service,
            len(kept),
        )
        return KubernetesPodsResult(
            namespace=request.namespace,
            service=request.service,
            pods=kept,
            total_found=len(found),
            truncated=truncated,
        )
