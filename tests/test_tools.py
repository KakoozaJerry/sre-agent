"""Tool layer v1: read-only, bounded, cleaned evidence gathering."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sre_agent.models import Target
from sre_agent.tools import (
    FileLogsBackend,
    InMemoryLogsBackend,
    InMemoryMetricsBackend,
    InMemoryServiceBackend,
    LogEntry,
    MetricSample,
    QueryMetricsRequest,
    SearchLogsRequest,
    ServiceStatus,
    ToolLayer,
    demo_tool_layer,
    metric_name_from_query,
    parse_prometheus_response,
    redact_secrets,
)

NOW = datetime(2026, 11, 8, 22, 0, tzinfo=UTC)
TARGET = Target(environment="demo", namespace="shop", service="checkout")


def _layer() -> ToolLayer:
    metrics = InMemoryMetricsBackend()
    metrics.add_series(
        "http_request_errors_total",
        {"service": "checkout", "namespace": "shop"},
        [
            MetricSample(timestamp=NOW, value=10.0),
            MetricSample(timestamp=NOW, value=42.0),
        ],
    )
    metrics.add_series(
        "http_requests_total",
        {"service": "checkout", "namespace": "shop"},
        [MetricSample(timestamp=NOW, value=1000.0)],
    )
    logs = InMemoryLogsBackend(
        [
            LogEntry(
                timestamp=NOW,
                service="checkout",
                namespace="shop",
                level="ERROR",
                message="payment gateway timeout",
            ),
            LogEntry(
                timestamp=NOW,
                service="checkout",
                namespace="shop",
                level="INFO",
                message="request completed ok",
            ),
            LogEntry(
                timestamp=NOW,
                service="payments",
                namespace="shop",
                level="ERROR",
                message="card declined",
            ),
        ]
    )
    services = InMemoryServiceBackend()
    services.set_status(
        ServiceStatus(
            service="checkout",
            namespace="shop",
            environment="demo",
            status="degraded",
            ready_replicas=2,
            desired_replicas=3,
            version="1.4.2",
            message="one replica is not ready",
        )
    )
    return ToolLayer(metrics=metrics, logs=logs, services=services)


# --- redaction -------------------------------------------------------------


def test_redact_secrets_hides_passwords_tokens_and_keys():
    assert redact_secrets("password=hunter2") == "password=[redacted]"
    assert "hunter2" not in redact_secrets("api_key: abc123 password=hunter2")
    assert redact_secrets("Authorization: Bearer abc.def") == (
        "Authorization: [redacted]"
    )
    assert "[redacted]" in redact_secrets("key AKIAABCDEFGHIJKLMNOP seen")
    assert redact_secrets("nothing secret here") == "nothing secret here"


def test_log_search_redacts_secrets_in_returned_lines():
    logs = InMemoryLogsBackend(
        [
            LogEntry(
                timestamp=NOW,
                service="checkout",
                message="login failed password=hunter2 for user bob",
            )
        ]
    )
    layer = ToolLayer(logs=logs)
    result = layer.search_logs(SearchLogsRequest(text="login"))
    assert len(result.entries) == 1
    assert "hunter2" not in result.entries[0].message
    assert "[redacted]" in result.entries[0].message


# --- metrics ---------------------------------------------------------------


def test_metric_name_is_found_inside_a_promql_function():
    assert metric_name_from_query("rate(http_request_errors_total[5m])") == (
        "http_request_errors_total"
    )
    assert metric_name_from_query('up{service="checkout"}') == "up"
    assert metric_name_from_query("sum by (service) (http_requests_total)") == (
        "http_requests_total"
    )


def test_query_metrics_returns_the_matching_series():
    result = _layer().query_metrics(
        QueryMetricsRequest(query="rate(http_request_errors_total[5m])")
    )
    assert result.total_series_found == 1
    assert len(result.series) == 1
    assert result.series[0].metric == "http_request_errors_total"
    assert result.series[0].samples[-1].value == 42.0
    assert result.truncated is False


def test_query_metrics_with_no_match_is_empty_not_an_error():
    result = _layer().query_metrics(QueryMetricsRequest(query="no_such_metric"))
    assert result.series == []
    assert result.total_series_found == 0


def test_query_metrics_rejects_empty_long_and_malformed_queries():
    with pytest.raises(ValidationError):
        QueryMetricsRequest(query="   ")
    with pytest.raises(ValidationError):
        QueryMetricsRequest(query="x" * 501)
    with pytest.raises(ValidationError):
        QueryMetricsRequest(query="rate(http_requests_total[5m]")
    with pytest.raises(ValidationError):
        QueryMetricsRequest(query="up; drop table")
    with pytest.raises(ValidationError):
        QueryMetricsRequest(query="up", limit_series=0)
    with pytest.raises(ValidationError):
        QueryMetricsRequest(query="up", limit_series=999)


def test_query_metrics_bounds_points_and_flags_truncation():
    metrics = InMemoryMetricsBackend()
    metrics.add_series(
        "big_metric",
        {},
        [MetricSample(timestamp=NOW, value=float(i)) for i in range(10)],
    )
    layer = ToolLayer(metrics=metrics)
    result = layer.query_metrics(
        QueryMetricsRequest(query="big_metric", limit_points_per_series=3)
    )
    assert result.truncated is True
    assert len(result.series[0].samples) == 3
    # The newest points are kept, not the oldest.
    assert result.series[0].samples[-1].value == 9.0


def test_query_metrics_evidence_cites_the_query():
    result = _layer().query_metrics(
        QueryMetricsRequest(query="http_request_errors_total")
    )
    ev = result.to_evidence()
    assert ev.kind == "metric"
    assert ev.query == "promql: http_request_errors_total"
    assert "http_request_errors_total" in ev.summary
    assert "42" in ev.summary


def test_parse_prometheus_instant_and_range_replies():
    instant = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"__name__": "up", "service": "checkout"},
                    "value": [1762632000, "1"],
                }
            ],
        },
    }
    series = parse_prometheus_response(instant)
    assert len(series) == 1
    assert series[0].metric == "up"
    assert series[0].labels == {"service": "checkout"}
    assert series[0].samples[0].value == 1.0

    ranged = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"__name__": "up"},
                    "values": [[1762632000, "1"], [1762632060, "0"]],
                }
            ],
        },
    }
    assert len(parse_prometheus_response(ranged)[0].samples) == 2
    assert parse_prometheus_response({"status": "error", "data": {}}) == []


# --- logs ------------------------------------------------------------------


def test_search_logs_filters_by_text_service_and_level():
    layer = _layer()
    by_text = layer.search_logs(SearchLogsRequest(text="timeout"))
    assert by_text.total_found == 1
    assert by_text.entries[0].service == "checkout"

    by_service = layer.search_logs(SearchLogsRequest(service="payments"))
    assert by_service.total_found == 1

    errors = layer.search_logs(SearchLogsRequest(level="error"))
    assert errors.total_found == 2
    assert all(e.level == "ERROR" for e in errors.entries)


def test_search_logs_is_case_insensitive_and_bounds_results():
    logs = InMemoryLogsBackend(
        [
            LogEntry(timestamp=NOW, service="s", message=f"Timeout number {i}")
            for i in range(5)
        ]
    )
    result = ToolLayer(logs=logs).search_logs(
        SearchLogsRequest(text="timeout", limit=2)
    )
    assert result.total_found == 5
    assert len(result.entries) == 2
    assert result.truncated is True


def test_search_logs_rejects_bad_level_and_overlong_text():
    with pytest.raises(ValidationError):
        SearchLogsRequest(level="chatty")
    with pytest.raises(ValidationError):
        SearchLogsRequest(text="x" * 201)
    with pytest.raises(ValidationError):
        SearchLogsRequest(limit=0)


def test_search_logs_evidence_is_a_log_with_the_search_text():
    result = _layer().search_logs(SearchLogsRequest(text="timeout"))
    ev = result.to_evidence()
    assert ev.kind == "log"
    assert "timeout" in ev.query
    assert "1 log line" in ev.summary


def test_file_logs_backend_reads_plain_and_json_lines(tmp_path):
    path = tmp_path / "demo.log"
    path.write_text(
        "plain line about a timeout\n"
        '{"timestamp": "2026-11-08T22:00:00Z", "service": "checkout", '
        '"namespace": "shop", "level": "ERROR", '
        '"message": "json line about a timeout"}\n',
        encoding="utf-8",
    )
    layer = ToolLayer(logs=FileLogsBackend(path, default_service="checkout"))
    result = layer.search_logs(SearchLogsRequest(text="timeout"))
    assert result.total_found == 2
    assert {e.service for e in result.entries} == {"checkout"}


def test_file_logs_backend_missing_file_is_empty_not_an_error(tmp_path):
    layer = ToolLayer(logs=FileLogsBackend(tmp_path / "nope.log"))
    assert layer.search_logs(SearchLogsRequest(text="x")).total_found == 0


# --- service status ----------------------------------------------------------


def test_get_service_status_for_a_known_service():
    result = _layer().get_service_status(TARGET)
    assert result.found is True
    assert result.status is not None
    assert result.status.status == "degraded"
    assert result.status.ready_replicas == 2
    ev = result.to_evidence()
    assert ev.kind == "service"
    assert "degraded" in ev.summary
    assert "2/3" in ev.summary


def test_get_service_status_for_an_unknown_service_is_not_an_error():
    missing = Target(environment="demo", namespace="shop", service="nope")
    result = _layer().get_service_status(missing)
    assert result.found is False
    assert result.status is None
    assert "No status known" in result.to_evidence().summary


# --- the seeded demo ---------------------------------------------------------


def test_demo_tool_layer_answers_all_three_tools():
    layer = demo_tool_layer()
    metrics = layer.query_metrics(QueryMetricsRequest(query="http_requests_total"))
    assert metrics.total_series_found == 1
    logs = layer.search_logs(SearchLogsRequest(text="timeout"))
    assert logs.total_found == 1
    status = layer.get_service_status(TARGET)
    assert status.found is True
