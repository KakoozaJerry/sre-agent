"""Live demo wiring (Phase 12): diagnosis reads live Prometheus.

The point of these tests is the difference between live and
seeded. A fake Prometheus and the real sample service are
started on spare local ports:

- the fake Prometheus answers ``/api/v1/query`` with values the
  test chooses, in the real Prometheus reply shape;
- the sample service is the demo's own code, driven the way
  ``demo/inject_fault.sh`` drives it (chaos on, then requests).

With the failure on, the live tool layer must diagnose the
fault that was actually injected — the live error count in its
evidence, not the seeded demo layer's 162 — and with no failure
recorded, the same layer must say "I don't know", where the
seeded layer would always diagnose its made-up picture.
"""

from __future__ import annotations

import importlib.util
import json
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Self

import pytest

from sre_agent.demo_live import create_live_demo_app, live_demo_tool_layer
from sre_agent.models import Incident, IncidentStatus, Target
from sre_agent.reasoning import ERROR_METRIC, REQUEST_METRIC, ReasoningLoop
from sre_agent.tools import QueryMetricsRequest, SearchLogsRequest

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = Target(environment="demo", namespace="shop", service="checkout")


def _load_sample_module():
    spec = importlib.util.spec_from_file_location(
        "demo_sample_app_live", REPO_ROOT / "demo" / "sample_service" / "app.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakePrometheus:
    """A Prometheus-shaped HTTP answer with test-chosen values."""

    def __init__(self, requests_value: float, errors_value: float) -> None:
        self.values = {
            REQUEST_METRIC: requests_value,
            ERROR_METRIC: errors_value,
        }
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/api/v1/query":
                    self.send_response(404)
                    self.end_headers()
                    return
                query = urllib.parse.parse_qs(parsed.query).get("query", [""])[0]
                metric = "unknown"
                for name in outer.values:
                    if name in query:
                        metric = name
                        break
                value = outer.values.get(metric, 0.0)
                payload = {
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [
                            {
                                "metric": {
                                    "__name__": metric,
                                    "service": "checkout",
                                    "namespace": "shop",
                                },
                                "value": [1762632000, str(value)],
                            }
                        ],
                    },
                }
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


class _LiveStack:
    """The fake Prometheus plus the real sample service."""

    def __init__(self, failing: bool, work_calls: int) -> None:
        self.prometheus = _FakePrometheus(
            requests_value=float(work_calls),
            errors_value=float(work_calls if failing else 0),
        )
        app = _load_sample_module()
        self.sample_service = app.SampleService(failing=failing)
        for _ in range(work_calls):
            self.sample_service.work()
        self.sample_server = ThreadingHTTPServer(
            ("127.0.0.1", 0), app.make_handler(self.sample_service)
        )
        self.sample_thread = Thread(
            target=self.sample_server.serve_forever, daemon=True
        )

    @property
    def sample_url(self) -> str:
        return f"http://127.0.0.1:{self.sample_server.server_address[1]}"

    def __enter__(self) -> Self:
        self.prometheus.__enter__()
        self.sample_thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.sample_server.shutdown()
        self.sample_server.server_close()
        self.prometheus.__exit__()


def _incident() -> Incident:
    incident = Incident(title="HighErrorRate on checkout", target=TARGET)
    incident.transition(IncidentStatus.TRIAGING)
    return incident


def test_live_metrics_come_from_prometheus_not_the_seed() -> None:
    with _LiveStack(failing=True, work_calls=3) as stack:
        layer = live_demo_tool_layer(stack.prometheus.url, stack.sample_url)
        result = layer.query_metrics(QueryMetricsRequest(query=ERROR_METRIC))
        assert result.series[0].samples[-1].value == 3.0
        # The seeded demo layer's error value is 162; live is 3.
        assert "162" not in result.to_evidence().summary


def test_live_status_and_logs_follow_the_injected_fault() -> None:
    with _LiveStack(failing=True, work_calls=3) as stack:
        layer = live_demo_tool_layer(stack.prometheus.url, stack.sample_url)
        status = layer.get_service_status(TARGET)
        assert status.found is True
        assert status.status is not None
        assert status.status.status == "degraded"
        assert "3 error(s)" in status.status.message
        logs = layer.search_logs(
            SearchLogsRequest(service="checkout", level="ERROR")
        )
        assert logs.total_found == 3
        assert "timed out" in logs.entries[0].message


def test_live_diagnosis_names_the_live_fault() -> None:
    with _LiveStack(failing=True, work_calls=3) as stack:
        layer = live_demo_tool_layer(stack.prometheus.url, stack.sample_url)
        report = ReasoningLoop(layer).diagnose(_incident())
        assert report.outcome.value == "diagnosed"
        assert "timing out" in report.summary
        metric_summaries = [
            ev.summary for ev in report.gathered_evidence if ev.kind == "metric"
        ]
        assert any("last value 3" in s for s in metric_summaries)
        assert any("latest value 3" in c.statement for c in report.claims)


def test_live_diagnosis_with_no_fault_says_i_dont_know() -> None:
    with _LiveStack(failing=False, work_calls=2) as stack:
        layer = live_demo_tool_layer(stack.prometheus.url, stack.sample_url)
        report = ReasoningLoop(layer).diagnose(_incident())
        assert report.outcome.value == "insufficient_evidence"
        assert report.claims == []


def test_live_app_factory_builds_and_answers_health() -> None:
    with _LiveStack(failing=True, work_calls=1) as stack:
        import os

        old_prom = os.environ.get("SRE_AGENT_PROMETHEUS_URL")
        old_sample = os.environ.get("SRE_AGENT_SAMPLE_SERVICE_URL")
        os.environ["SRE_AGENT_PROMETHEUS_URL"] = stack.prometheus.url
        os.environ["SRE_AGENT_SAMPLE_SERVICE_URL"] = stack.sample_url
        try:
            from fastapi.testclient import TestClient

            client = TestClient(create_live_demo_app())
            assert client.get("/health").json() == {"status": "ok"}
        finally:
            for key, old in (
                ("SRE_AGENT_PROMETHEUS_URL", old_prom),
                ("SRE_AGENT_SAMPLE_SERVICE_URL", old_sample),
            ):
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old


def test_sample_service_logs_are_recorded_and_bounded() -> None:
    app = _load_sample_module()
    svc = app.SampleService()
    svc.work()
    svc.set_failing(True)
    svc.work()
    assert len(svc.log_entries) == 2
    assert svc.log_entries[0]["level"] == "INFO"
    assert svc.log_entries[1]["level"] == "ERROR"
    assert "timed out" in svc.log_entries[1]["message"]


def test_live_layer_metric_names_match_the_reasoning_loop() -> None:
    # The live backends repeat the metric names as strings; they
    # must be the loop's own constants, not a drifted copy.
    from sre_agent import tools

    assert tools._LIVE_ERROR_METRIC == ERROR_METRIC
    assert tools._LIVE_REQUEST_METRIC == REQUEST_METRIC


@pytest.mark.parametrize("host", ["prometheus", "sample-service"])
def test_compose_hosts_are_accepted_only_when_named(host: str) -> None:
    from sre_agent.backend_url import validate_backend_url

    with pytest.raises(ValueError, match="plain http"):
        validate_backend_url(f"http://{host}:9090")
    assert (
        validate_backend_url(
            f"http://{host}:9090", allow_http_hosts=[host]
        )
        == f"http://{host}:9090"
    )
    # Naming one host does not accept any other.
    other = "other" if host != "other" else "prometheus"
    with pytest.raises(ValueError, match="plain http"):
        validate_backend_url(
            f"http://{other}:9090", allow_http_hosts=[host]
        )
