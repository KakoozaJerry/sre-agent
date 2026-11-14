"""Selecting the read adapters as evidence sources, and the demo.

Part of the read adapters package. This module holds the pieces
that span all three backends: the :class:`AdapterSources` bundle
an operator hands to the reasoning loop (or to the server) to
select which adapters feed a diagnosis, the seeded demo builders
for each backend, and the one-call demo report that runs all
three adapters over the seeded demo data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from ..logging_config import get_logger
from ..tools import MetricSample, QueryMetricsRequest, QueryMetricsResult
from .azure import AzureAdapter, AzureQueryResult, InMemoryAzureBackend, KqlQueryRequest
from .datadog import DatadogAdapter, InMemoryDatadogBackend
from .kubernetes import (
    InMemoryKubernetesBackend,
    KubernetesPodsResult,
    KubernetesReader,
    ListPodsRequest,
    PodStatus,
)

log = get_logger(__name__)


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
