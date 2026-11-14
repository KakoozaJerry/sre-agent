"""Tests for the read adapters: Kubernetes pod listing, the
Datadog metrics adapter, and the Azure Monitor (KQL) adapter.

All three adapters only look, so these tests pin both directions:
the parsers read recorded reply shapes correctly and the results
become evidence — and bad input is refused, management commands
never pass the KQL check, secrets are cleaned, and results are
bounded. No live cluster, account or workspace is used.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sre_agent.adapters import (
    AzureAdapter,
    DatadogAdapter,
    InMemoryAzureBackend,
    InMemoryDatadogBackend,
    InMemoryKubernetesBackend,
    KqlQueryRequest,
    KubernetesReader,
    ListPodsRequest,
    PodStatus,
    demo_adapters,
    parse_azure_response,
    parse_datadog_response,
    parse_kubernetes_pods,
)
from sre_agent.cli import main
from sre_agent.tools import MetricSample, QueryMetricsRequest

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)

POD_LIST_PAYLOAD: dict[str, object] = {
    "kind": "PodList",
    "items": [
        {
            "metadata": {
                "name": "checkout-a",
                "namespace": "shop",
                "labels": {"app": "checkout"},
            },
            "spec": {"containers": [{"name": "main"}]},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"name": "main", "ready": True, "restartCount": 2}
                ],
            },
        },
        {
            "metadata": {
                "name": "checkout-b",
                "namespace": "shop",
                "labels": {"app.kubernetes.io/name": "checkout"},
            },
            "spec": {"containers": [{"name": "main"}, {"name": "sidecar"}]},
            "status": {"phase": "Pending"},
        },
        {"not": "a pod"},
    ],
}


def test_parse_kubernetes_pods_reads_phase_ready_and_restarts() -> None:
    pods = parse_kubernetes_pods(POD_LIST_PAYLOAD)
    assert [p.name for p in pods] == ["checkout-a", "checkout-b"]
    first, second = pods
    assert first.phase == "Running"
    assert first.ready_containers == 1 and first.total_containers == 1
    assert first.restarts == 2 and first.service == "checkout"
    # A pod with no container statuses yet: 0 ready, total from spec,
    # service read from the standard app.kubernetes.io/name label.
    assert second.phase == "Pending"
    assert second.ready_containers == 0 and second.total_containers == 2
    assert second.service == "checkout"


def test_parse_kubernetes_pods_handles_a_non_list_reply() -> None:
    assert parse_kubernetes_pods({}) == []
    assert parse_kubernetes_pods({"items": "nope"}) == []


def test_list_pods_filters_by_namespace_and_service_and_bounds() -> None:
    backend = InMemoryKubernetesBackend(
        [
            PodStatus(name=f"checkout-{i}", namespace="shop", phase="Running",
                      ready_containers=1, total_containers=1, service="checkout")
            for i in range(3)
        ]
        + [PodStatus(name="search-a", namespace="shop", phase="Running",
                     ready_containers=1, total_containers=1, service="search")]
        + [PodStatus(name="checkout-x", namespace="billing", phase="Running",
                     ready_containers=1, total_containers=1, service="checkout")]
    )
    reader = KubernetesReader(backend)
    result = reader.list_pods(ListPodsRequest(namespace="shop", service="checkout", limit=2))
    assert [p.name for p in result.pods] == ["checkout-0", "checkout-1"]
    assert result.total_found == 3 and result.truncated is True
    evidence = result.to_evidence()
    assert evidence.kind == "kubernetes"
    assert "namespace=shop" in evidence.query and "app=checkout" in evidence.query


def test_list_pods_rejects_a_bad_namespace_name() -> None:
    with pytest.raises(ValidationError):
        ListPodsRequest(namespace="Shop Namespace!")
    with pytest.raises(ValidationError):
        ListPodsRequest(namespace="shop", limit=0)


def test_parse_datadog_response_reads_tags_and_millisecond_points() -> None:
    payload: dict[str, object] = {
        "status": "ok",
        "series": [
            {
                "metric": "http_request_errors_total",
                "tags": ["service:checkout", "namespace:shop"],
                "pointlist": [[1762704000000, 162.0], [1762704060000, 170.0]],
            }
        ],
    }
    series = parse_datadog_response(payload)
    assert len(series) == 1
    assert series[0].metric == "http_request_errors_total"
    assert series[0].labels == {"service": "checkout", "namespace": "shop"}
    assert [s.value for s in series[0].samples] == [162.0, 170.0]
    assert series[0].samples[0].timestamp.tzinfo is not None
    assert parse_datadog_response({"status": "error", "series": []}) == []


def test_datadog_adapter_returns_tool_shaped_evidence_and_bounds_points() -> None:
    backend = InMemoryDatadogBackend()
    backend.add_series(
        "http_request_errors_total",
        {"service": "checkout"},
        [MetricSample(timestamp=NOW, value=float(i)) for i in range(5)],
    )
    result = DatadogAdapter(backend).query_metrics(
        QueryMetricsRequest(
            query="sum:http_request_errors_total{service:checkout}",
            limit_points_per_series=2,
        )
    )
    assert result.total_series_found == 1 and result.truncated is True
    assert [s.value for s in result.series[0].samples] == [3.0, 4.0]
    assert result.query.startswith("datadog: ")
    evidence = result.to_evidence()
    assert evidence.kind == "metric" and "datadog:" in evidence.query


def test_kql_request_refuses_empty_overlong_and_management_commands() -> None:
    with pytest.raises(ValidationError):
        KqlQueryRequest(query="   ")
    with pytest.raises(ValidationError):
        KqlQueryRequest(query="x" * 1001)
    with pytest.raises(ValidationError):
        KqlQueryRequest(query=".drop table Traces")
    with pytest.raises(ValidationError):
        KqlQueryRequest(query="Traces; .delete from Traces")
    # A plain read, including a let statement, is fine.
    assert KqlQueryRequest(query="AppTraces | take 5").query == "AppTraces | take 5"


def test_parse_azure_response_reads_columns_and_rows_in_order() -> None:
    payload: dict[str, object] = {
        "tables": [
            {
                "name": "PrimaryResult",
                "columns": [
                    {"name": "TimeGenerated", "type": "datetime"},
                    {"name": "Service", "type": "string"},
                    {"name": "Count", "type": "long"},
                ],
                "rows": [["2026-11-09T12:00:00Z", "checkout", 3], ["t2", "search", None]],
            }
        ]
    }
    columns, rows = parse_azure_response(payload)
    assert columns == ["TimeGenerated", "Service", "Count"]
    assert rows[0] == {"TimeGenerated": "2026-11-09T12:00:00Z", "Service": "checkout",
                       "Count": "3"}
    assert rows[1]["Count"] == ""
    assert parse_azure_response({}) == ([], [])


def test_azure_adapter_bounds_rows_redacts_secrets_and_makes_evidence() -> None:
    backend = InMemoryAzureBackend(
        [
            {"Service": "checkout", "Message": "failed password=hunter2"},
            {"Service": "checkout", "Message": "second"},
            {"Service": "checkout", "Message": "third"},
        ]
    )
    result = AzureAdapter(backend).query(
        KqlQueryRequest(query="AppTraces | where Service == 'checkout'", limit_rows=2)
    )
    assert result.total_rows_found == 3 and result.truncated is True
    assert len(result.rows) == 2
    assert "hunter2" not in result.rows[0]["Message"]
    assert "[redacted]" in result.rows[0]["Message"]
    evidence = result.to_evidence()
    assert evidence.kind == "azure-log" and evidence.query.startswith("kql: ")
    assert "hunter2" not in evidence.summary


def test_demo_adapters_report_agrees_across_all_three() -> None:
    report = demo_adapters()
    assert [p.name for p in report.pods.pods] == [
        "checkout-7d9f-a",
        "checkout-7d9f-b",
        "checkout-7d9f-c",
    ]
    assert report.pods.pods[2].phase == "Pending"
    assert report.pods.pods[2].restarts == 5
    assert report.datadog.series[0].samples[-1].value == 162.0
    assert len(report.azure.rows) == 2
    text = report.render_text()
    assert "only looks" in text
    assert "No live cluster" in text


def test_cli_adapters_demo_prints_report_and_exits_zero(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["adapters-demo"]) == 0
    out = capsys.readouterr().out
    assert "Read adapters demo" in out
    assert "checkout-7d9f-c" in out


def test_cli_adapters_demo_rejects_extra_arguments(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["adapters-demo", "extra"]) == 1
    assert "no extra arguments" in capsys.readouterr().err


# --- Phase 12 restructure: one module per backend, same import path ---------


def test_adapters_is_a_package_split_per_backend() -> None:
    import sre_agent.adapters as adapters_pkg

    assert hasattr(adapters_pkg, "__path__")  # a package, not one module


def test_each_backend_lives_in_its_own_module() -> None:
    from sre_agent.adapters import azure, datadog, kubernetes, sources

    assert kubernetes.KubernetesReader is KubernetesReader
    assert datadog.DatadogAdapter is DatadogAdapter
    assert azure.AzureAdapter is AzureAdapter
    assert sources.demo_adapters is demo_adapters


def test_package_imports_are_the_same_objects_as_the_backend_modules() -> None:
    import sre_agent.adapters as adapters_pkg
    from sre_agent.adapters import azure, datadog, kubernetes

    assert adapters_pkg.parse_kubernetes_pods is kubernetes.parse_kubernetes_pods
    assert adapters_pkg.parse_datadog_response is datadog.parse_datadog_response
    assert adapters_pkg.parse_azure_response is azure.parse_azure_response
