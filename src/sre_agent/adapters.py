"""Read adapters: Kubernetes, Datadog and Azure Monitor (KQL).

In plain words, this module is how the agent looks at three more
places an SRE already uses — still only looking, never changing:

- **Kubernetes.** List the pods (the running copies of a workload)
  in one namespace, with each pod's phase, how many of its
  containers are ready, and how many times they have restarted.
  This is the read a person does first with ``kubectl get pods``.
- **Datadog.** Ask a metrics question, in the same shape the
  existing PromQL tool already returns, so a Datadog series can
  become evidence exactly like a Prometheus one.
- **Azure Monitor.** Run a KQL query (Kusto Query Language, the
  query language of Azure Log Analytics) against a workspace and
  get rows back as evidence.

How safety is kept, the same rules as the tool layer:

- **Read-only by construction.** Every backend here only fetches.
  There is no method that creates, scales, restarts or deletes
  anything, in any of the three systems. The KQL check also
  refuses management commands (the ones that start with a dot,
  such as ``.drop``), because those change a workspace, they do
  not read it.
- **Inputs are checked and outputs are bounded.** Namespace
  names, query lengths and result counts all have hard limits,
  anything cut off is flagged, and every text value passes the
  tool layer's secret cleaning before it can be stored or shown.
- **Secrets are passed in, never written down.** The live
  backends take their API keys or token as arguments from the
  caller — nothing here reads a key from a file, logs one, or
  has one in its code.
- **Backend addresses are checked.** Every live backend's
  address must pass the rules in ``backend_url.py`` (finding
  F-05) before a credential can be sent to it: https, or plain
  http to this machine only, no credentials inside the address,
  and an optional host allow-list per backend.
- **Replies are parsed by pure functions.** Each system's reply
  shape is turned into this project's own models by a function
  that takes the decoded reply and nothing else, so the parsing
  is tested against recorded reply shapes with no live account,
  no cluster and no network — the same two-layer honesty the
  demo environment uses.

Scope, stated honestly: the demo backends and the parsers are
built and tested here. The live backends are thin standard-
library HTTP clients behind the same shapes, but no live
Kubernetes cluster (kind or otherwise), Datadog account or Azure
workspace was available in the build environment, so no live
call is claimed as tested; running against a real kind cluster
is a human check, the same status the Docker demo had at first.

Feeding a diagnosis (Phase 7): an operator may select any of
these adapters as extra evidence sources for the reasoning loop
by handing an :class:`AdapterSources` bundle to it (or to the
server, which passes it on). That selection is the only wiring:
the loop asks each selected adapter one fixed question, built
only from the incident's validated target names, and the answers
become evidence records on the incident like any other. An
adapter that is not selected is never called, and a selected
adapter that fails is skipped — it never takes the diagnosis
down with it.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from .backend_url import validate_backend_url
from .logging_config import get_logger
from .models import Evidence
from .tools import (
    MAX_EVIDENCE_SUMMARY_CHARS,
    MetricSample,
    MetricSeries,
    QueryMetricsRequest,
    QueryMetricsResult,
    metric_name_from_query,
    redact_secrets,
)

log = get_logger(__name__)

MAX_PODS = 50
MAX_AZURE_ROWS = 50
MAX_KQL_LENGTH = 1000
MAX_NAMESPACE_LENGTH = 63

_NAMESPACE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
# Azure management commands change a workspace; a read adapter
# must never send one. They all start with a dot.
_MANAGEMENT_RE = re.compile(r"(?i)(^|[;\s])\.(drop|delete|set|clear|ingest|create|alter)\b")


def _bound(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…[truncated]"


# ---------------------------------------------------------------------------
# Kubernetes: list pods (read-only)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Datadog: metrics query (read-only)
# ---------------------------------------------------------------------------


def parse_datadog_response(payload: dict[str, object]) -> list[MetricSeries]:
    """Turn a Datadog metrics query reply into series.

    Pure function, tested against a recorded reply shape. Datadog
    answers with a ``series`` list; each entry names its metric,
    carries its tags as ``"key:value"`` strings, and holds its
    points as ``[timestamp-milliseconds, value]`` pairs. A reply
    whose status is not ``ok`` gives back no series.
    """
    if payload.get("status") != "ok":
        return []
    raw_series = payload.get("series")
    if not isinstance(raw_series, list):
        return []
    series: list[MetricSeries] = []
    for item in raw_series:
        if not isinstance(item, dict):
            continue
        labels: dict[str, str] = {}
        tags = item.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str) and ":" in tag:
                    key, value = tag.split(":", 1)
                    labels[key] = value
        scope = item.get("scope")
        if isinstance(scope, str) and ":" in scope:
            key, value = scope.split(":", 1)
            labels.setdefault(key, value)
        samples: list[MetricSample] = []
        pointlist = item.get("pointlist")
        if isinstance(pointlist, list):
            for point in pointlist:
                if not (isinstance(point, list) and len(point) == 2):
                    continue
                try:
                    ts = datetime.fromtimestamp(float(point[0]) / 1000.0, tz=UTC)
                    samples.append(MetricSample(timestamp=ts, value=float(point[1])))
                except (TypeError, ValueError):
                    continue
        series.append(
            MetricSeries(
                metric=str(item.get("metric", "unknown")), labels=labels, samples=samples
            )
        )
    return series


class InMemoryDatadogBackend:
    """Demo Datadog backend: series held in memory, seeded by tests."""

    def __init__(self) -> None:
        self._series: list[MetricSeries] = []

    def add_series(
        self, metric: str, labels: dict[str, str], samples: list[MetricSample]
    ) -> None:
        self._series.append(
            MetricSeries(metric=metric, labels=dict(labels), samples=list(samples))
        )

    def fetch(self, query: str) -> list[MetricSeries]:
        # Datadog queries open with an aggregator — "sum:metric{...}".
        # The metric name follows it; without this step the whole
        # "sum:metric" string would be mistaken for the name.
        head = query.split("{", 1)[0]
        prefix, _, rest = head.partition(":")
        candidate = rest if rest and prefix in {"sum", "avg", "max", "min", "count"} else query
        name = metric_name_from_query(candidate)
        if name is None:
            return []
        return [s for s in self._series if s.metric == name]


class DatadogMetricsBackend:
    """Datadog backend that asks the real Datadog API.

    The API key and application key are passed in by the caller
    and sent only as request headers — never logged, never put
    on a result. The query window is the last hour by default.
    """

    def __init__(
        self,
        api_key: str,
        app_key: str,
        base_url: str = "https://api.datadoghq.com",
        window_seconds: int = 3600,
        timeout_seconds: float = 5.0,
        allowed_hosts: list[str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._app_key = app_key
        self._base_url = validate_backend_url(
            base_url, allowed_hosts=allowed_hosts, what="Datadog address"
        )
        self._window_seconds = window_seconds
        self._timeout = timeout_seconds

    def fetch(self, query: str) -> list[MetricSeries]:
        now = int(datetime.now(UTC).timestamp())
        qs = urllib.parse.urlencode(
            {"query": query, "from": now - self._window_seconds, "to": now}
        )
        request = urllib.request.Request(
            f"{self._base_url}/api/v1/query?{qs}",
            headers={
                "Accept": "application/json",
                "DD-API-KEY": self._api_key,
                "DD-APPLICATION-KEY": self._app_key,
            },
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            return []
        return parse_datadog_response(payload)


class DatadogAdapter:
    """The Datadog metrics read, returning the tool layer's shape.

    The result is a plain ``QueryMetricsResult``, so a Datadog
    series becomes evidence through the same ``to_evidence`` the
    Prometheus tool uses — the query on the record says it came
    from Datadog, so the source is never blurred.
    """

    def __init__(
        self, backend: InMemoryDatadogBackend | DatadogMetricsBackend | None = None
    ) -> None:
        self._backend = backend if backend is not None else InMemoryDatadogBackend()

    def query_metrics(self, request: QueryMetricsRequest) -> QueryMetricsResult:
        found = self._backend.fetch(request.query)
        truncated = len(found) > request.limit_series
        kept: list[MetricSeries] = []
        for series in found[: request.limit_series]:
            samples = series.samples
            if len(samples) > request.limit_points_per_series:
                truncated = True
                samples = samples[-request.limit_points_per_series :]
            kept.append(
                MetricSeries(
                    metric=series.metric,
                    labels={k: redact_secrets(v) for k, v in series.labels.items()},
                    samples=samples,
                )
            )
        log.info("datadog query_metrics %r -> %d series", request.query, len(kept))
        return QueryMetricsResult(
            query=f"datadog: {request.query}",
            series=kept,
            total_series_found=len(found),
            truncated=truncated,
        )


# ---------------------------------------------------------------------------
# Azure Monitor: KQL query against Log Analytics (read-only)
# ---------------------------------------------------------------------------


class KqlQueryRequest(BaseModel):
    """A KQL question. Checked before it is allowed to run.

    KQL itself is a read language, but a Log Analytics workspace
    also accepts management commands — those start with a dot and
    change things, so any query carrying one is refused here.
    """

    query: str
    limit_rows: int = Field(default=20, ge=1, le=MAX_AZURE_ROWS)

    @field_validator("query")
    @classmethod
    def _query_must_be_a_safe_read(cls, v: str) -> str:
        q = v.strip()
        if not q:
            raise ValueError("KQL query must not be empty")
        if len(q) > MAX_KQL_LENGTH:
            raise ValueError(f"KQL query is too long (limit {MAX_KQL_LENGTH})")
        if q.startswith(".") or _MANAGEMENT_RE.search(q):
            raise ValueError("KQL management commands are not reads and are refused")
        return q


def parse_azure_response(
    payload: dict[str, object],
) -> tuple[list[str], list[dict[str, str]]]:
    """Turn an Azure Log Analytics query reply into columns and rows.

    Pure function, tested against a recorded reply shape. Azure
    answers with ``tables``; the first table names its ``columns``
    and holds each result as a ``rows`` list in the same order.
    Values are returned as text — that is all an evidence summary
    needs, and it avoids guessing at a type the reply's column
    types only loosely pin down. A reply with no tables gives
    back no columns and no rows.
    """
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables or not isinstance(tables[0], dict):
        return [], []
    table = tables[0]
    columns: list[str] = []
    raw_columns = table.get("columns")
    if isinstance(raw_columns, list):
        for column in raw_columns:
            if isinstance(column, dict) and "name" in column:
                columns.append(str(column["name"]))
    rows: list[dict[str, str]] = []
    raw_rows = table.get("rows")
    if isinstance(raw_rows, list):
        for raw_row in raw_rows:
            if not isinstance(raw_row, list):
                continue
            row: dict[str, str] = {}
            for idx, column in enumerate(columns):
                value = raw_row[idx] if idx < len(raw_row) else ""
                row[column] = "" if value is None else str(value)
            rows.append(row)
    return columns, rows


class AzureQueryResult(BaseModel):
    query: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, str]] = Field(default_factory=list)
    total_rows_found: int = 0
    truncated: bool = False

    def to_evidence(self) -> Evidence:
        if not self.rows:
            summary = f"No rows matched KQL query: {self.query}"
        else:
            first = self.rows[0]
            shown = ", ".join(f"{k}={v}" for k, v in list(first.items())[:4])
            summary = f"{self.total_rows_found} row(s) matched; first: {shown}"
            if self.truncated:
                summary += " [truncated]"
        return Evidence(
            kind="azure-log",
            query=f"kql: {self.query}",
            summary=_bound(redact_secrets(summary), MAX_EVIDENCE_SUMMARY_CHARS),
        )


class InMemoryAzureBackend:
    """Demo Azure backend: rows held in memory, seeded by tests."""

    def __init__(self, rows: list[dict[str, str]] | None = None) -> None:
        self._rows: list[dict[str, str]] = [dict(r) for r in rows or []]

    def fetch(self, query: str) -> tuple[list[str], list[dict[str, str]]]:
        columns = list(self._rows[0]) if self._rows else []
        return columns, [dict(r) for r in self._rows]


class AzureLogAnalyticsBackend:
    """Azure backend that asks a real Log Analytics workspace.

    The bearer token is passed in by the caller (in real use it
    comes from the caller's own Azure sign-in) and is only ever
    put in the request header. The query endpoint is a read.
    """

    def __init__(
        self,
        workspace_id: str,
        token: str,
        base_url: str = "https://api.loganalytics.azure.com",
        timespan: str = "PT1H",
        timeout_seconds: float = 5.0,
        allowed_hosts: list[str] | None = None,
    ) -> None:
        self._workspace_id = workspace_id
        self._token = token
        self._base_url = validate_backend_url(
            base_url, allowed_hosts=allowed_hosts, what="Azure Monitor address"
        )
        self._timespan = timespan
        self._timeout = timeout_seconds

    def fetch(self, query: str) -> tuple[list[str], list[dict[str, str]]]:
        url = (
            f"{self._base_url}/v1/workspaces/"
            f"{urllib.parse.quote(self._workspace_id)}/query"
        )
        body = json.dumps({"query": query, "timespan": self._timespan}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            return [], []
        return parse_azure_response(payload)


class AzureAdapter:
    """The Azure Monitor KQL read, with bounds and cleaning applied."""

    def __init__(
        self, backend: InMemoryAzureBackend | AzureLogAnalyticsBackend | None = None
    ) -> None:
        self._backend = backend if backend is not None else InMemoryAzureBackend()

    def query(self, request: KqlQueryRequest) -> AzureQueryResult:
        columns, rows = self._backend.fetch(request.query)
        truncated = len(rows) > request.limit_rows
        kept = [
            {k: redact_secrets(v) for k, v in row.items()}
            for row in rows[: request.limit_rows]
        ]
        log.info("azure kql query -> %d row(s)", len(kept))
        return AzureQueryResult(
            query=request.query,
            columns=columns,
            rows=kept,
            total_rows_found=len(rows),
            truncated=truncated,
        )


# ---------------------------------------------------------------------------
# Selecting adapters as evidence sources for a diagnosis
# ---------------------------------------------------------------------------


@dataclass
class AdapterSources:
    """The read adapters selected to feed a diagnosis.

    Every field is optional: a source left as ``None`` is never
    called. This bundle is how an operator selects evidence
    sources — for the reasoning loop directly, or for a server,
    which passes the bundle to its loop. The live adapters inside
    a bundle were built by the operator's own code, so their
    addresses already passed the backend address rules
    (``backend_url.py``) when they were constructed; nothing in
    an alert, a log line or any other telemetry can add a source
    or change an address.
    """

    kubernetes: KubernetesReader | None = None
    datadog: DatadogAdapter | None = None
    azure: AzureAdapter | None = None


def _demo_kubernetes_reader() -> KubernetesReader:
    return KubernetesReader(
        InMemoryKubernetesBackend(
            [
                PodStatus(
                    name="checkout-7d9f-a", namespace="shop", phase="Running",
                    ready_containers=1, total_containers=1, restarts=2,
                    service="checkout",
                ),
                PodStatus(
                    name="checkout-7d9f-b", namespace="shop", phase="Running",
                    ready_containers=1, total_containers=1, restarts=0,
                    service="checkout",
                ),
                PodStatus(
                    name="checkout-7d9f-c", namespace="shop", phase="Pending",
                    ready_containers=0, total_containers=1, restarts=5,
                    service="checkout",
                ),
                PodStatus(
                    name="search-5c1-a", namespace="shop", phase="Running",
                    ready_containers=1, total_containers=1, restarts=0,
                    service="search",
                ),
            ]
        )
    )


def _demo_datadog_adapter() -> DatadogAdapter:
    now = datetime.now(UTC)
    backend = InMemoryDatadogBackend()
    backend.add_series(
        "http_request_errors_total",
        {"service": "checkout", "namespace": "shop"},
        [MetricSample(timestamp=now, value=162.0)],
    )
    return DatadogAdapter(backend)


def _demo_azure_adapter() -> AzureAdapter:
    now = datetime.now(UTC)
    return AzureAdapter(
        InMemoryAzureBackend(
            [
                {
                    "TimeGenerated": now.isoformat(),
                    "Service": "checkout",
                    "Level": "Error",
                    "Message": "payment gateway timeout after 2000ms",
                },
                {
                    "TimeGenerated": now.isoformat(),
                    "Service": "checkout",
                    "Level": "Error",
                    "Message": "checkout request failed with status 502",
                },
            ]
        )
    )


def demo_adapter_sources() -> AdapterSources:
    """The three demo adapters as one selectable bundle.

    The seeded picture is the same one ``demo_adapters`` reports:
    checkout in the shop namespace, degraded. It claims nothing
    about any real system.
    """
    return AdapterSources(
        kubernetes=_demo_kubernetes_reader(),
        datadog=_demo_datadog_adapter(),
        azure=_demo_azure_adapter(),
    )


# ---------------------------------------------------------------------------
# Seeded demo: all three adapters, no live system needed
# ---------------------------------------------------------------------------


class AdaptersDemoReport(BaseModel):
    """What the three read adapters say about the demo, in one report."""

    pods: KubernetesPodsResult
    datadog: QueryMetricsResult
    azure: AzureQueryResult

    def render_text(self) -> str:
        lines = [
            (
                "Read adapters demo (seeded demo data, not a real system; "
                "every adapter here only looks, nothing is changed)"
            ),
            "Kubernetes — pods for checkout in namespace shop:",
        ]
        if not self.pods.pods:
            lines.append("- None found.")
        for pod in self.pods.pods:
            lines.append(
                f"- {pod.name}: {pod.phase}, {pod.ready_containers}/"
                f"{pod.total_containers} containers ready, {pod.restarts} restart(s)"
            )
        lines.append("Datadog — metrics query result:")
        if not self.datadog.series:
            lines.append("- No series matched.")
        for series in self.datadog.series:
            last = series.samples[-1].value if series.samples else 0.0
            lines.append(f"- {series.metric}{series.labels} last value {last:g}")
        lines.append("Azure Monitor — KQL query result:")
        if not self.azure.rows:
            lines.append("- No rows matched.")
        for row in self.azure.rows:
            shown = ", ".join(f"{k}={v}" for k, v in list(row.items())[:4])
            lines.append(f"- {shown}")
        lines.append(
            "Note: these are the demo backends and the reply parsers, "
            "tested with recorded reply shapes. No live cluster, Datadog "
            "account or Azure workspace was called."
        )
        return "\n".join(lines)


def demo_adapters() -> AdaptersDemoReport:
    """A seeded report from all three adapters, with no setup.

    The demo picture agrees with the rest of the demo on purpose:
    checkout in the shop namespace is degraded — two pods ready,
    one still pending after several restarts — its Datadog error
    series is high, and its Azure log rows say why.
    """
    sources = demo_adapter_sources()
    assert sources.kubernetes is not None
    assert sources.datadog is not None
    assert sources.azure is not None
    return AdaptersDemoReport(
        pods=sources.kubernetes.list_pods(
            ListPodsRequest(namespace="shop", service="checkout")
        ),
        datadog=sources.datadog.query_metrics(
            QueryMetricsRequest(query="sum:http_request_errors_total{service:checkout}")
        ),
        azure=sources.azure.query(
            KqlQueryRequest(
                query="AppTraces | where Service == 'checkout' and Level == 'Error'"
            )
        ),
    )
