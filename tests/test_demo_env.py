"""Demo environment v1: the sample service and the wiring.

Two kinds of checks live here:

- **Behaviour.** The sample service is plain Python, so its real
  HTTP routes are started on a spare local port and exercised:
  work counts, error counts, the chaos switch, and the metrics
  text Prometheus will scrape.
- **Wiring.** Docker is not available in every place these tests
  run, so the Compose, Prometheus, and Alertmanager files are
  checked as text: every service, port, config path, metric name,
  and webhook address must line up with the code that receives
  them. A full ``docker compose up`` run stays a human-run check;
  see docs/demo.md.
"""

from __future__ import annotations

import importlib.util
import json
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from sre_agent.reasoning import ERROR_METRIC, REQUEST_METRIC

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO = REPO_ROOT / "demo"


def _load_sample_module():
    spec = importlib.util.spec_from_file_location(
        "demo_sample_app", DEMO / "sample_service" / "app.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _post(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


# ---------------------------------------------------------------------------
# Sample service behaviour
# ---------------------------------------------------------------------------


def test_work_counts_requests_and_only_errors_when_failing() -> None:
    app = _load_sample_module()
    svc = app.SampleService()
    assert svc.work()[0] == 200
    assert svc.work()[0] == 200
    svc.set_failing(True)
    assert svc.work()[0] == 500
    assert svc.requests_total == 3
    assert svc.errors_total == 1


def test_health_reflects_the_chaos_switch() -> None:
    app = _load_sample_module()
    svc = app.SampleService()
    assert svc.health()[0] == 200
    svc.set_failing(True)
    assert svc.health()[0] == 503


def test_metrics_use_the_reasoning_loops_metric_names() -> None:
    app = _load_sample_module()
    svc = app.SampleService()
    svc.work()
    svc.set_failing(True)
    svc.work()
    text = svc.render_metrics()
    assert f"{REQUEST_METRIC}{{service=\"checkout\"" in text
    assert f"{ERROR_METRIC}{{service=\"checkout\"" in text
    assert text.count("} 2\n") >= 1  # two requests served
    assert "} 1\n" in text  # one error


def test_sample_service_over_real_http() -> None:
    app = _load_sample_module()
    service = app.SampleService()
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(service))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        code, body = _get(f"{base}/health")
        assert code == 200 and json.loads(body)["status"] == "ok"

        code, _ = _get(f"{base}/work")
        assert code == 200

        code, body = _post(f"{base}/chaos/on")
        assert code == 200 and json.loads(body)["failing"] is True

        code, _ = _get(f"{base}/work")
        assert code == 500

        code, body = _get(f"{base}/metrics")
        assert code == 200
        assert f"{ERROR_METRIC}{{service=\"checkout\"" in body

        code, body = _post(f"{base}/chaos/off")
        assert code == 200 and json.loads(body)["failing"] is False
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Wiring: files agree with each other and with the agent's code
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    assert path.is_file(), f"missing demo file: {path}"
    return path.read_text(encoding="utf-8")


def test_compose_defines_all_four_services() -> None:
    compose = _read(REPO_ROOT / "docker-compose.yml")
    for name in ("sample-service:", "prometheus:", "alertmanager:", "sre-agent:"):
        assert name in compose
    assert "./demo/sample_service" in compose
    assert "./demo/prometheus" in compose
    assert "./demo/alertmanager" in compose
    assert '"8080:8080"' in compose


def test_prometheus_scrapes_the_sample_service_and_loads_the_rules() -> None:
    config = _read(DEMO / "prometheus" / "prometheus.yml")
    assert "sample-service:8000" in config
    assert "alerts.yml" in config
    assert "alertmanager:9093" in config


def test_alert_rule_uses_the_error_metric_and_names_the_target() -> None:
    rules = _read(DEMO / "prometheus" / "alerts.yml")
    assert "alert: HighErrorRate" in rules
    assert f"expr: {ERROR_METRIC} > 0" in rules
    for label in ("service: checkout", "namespace: shop", "environment: demo"):
        assert label in rules


def test_alertmanager_sends_alerts_to_the_agents_webhook() -> None:
    config = _read(DEMO / "alertmanager" / "alertmanager.yml")
    # This exact path is the route server.py serves.
    assert "http://sre-agent:8080/webhooks/alertmanager" in config
    assert "send_resolved: true" in config


def test_dockerfiles_and_inject_script_exist_and_agree() -> None:
    agent_image = _read(REPO_ROOT / "Dockerfile")
    assert "sre_agent.server:app" in agent_image
    sample_image = _read(DEMO / "sample_service" / "Dockerfile")
    assert "app.py" in sample_image
    script = _read(DEMO / "inject_fault.sh")
    assert "/chaos/on" in script
    assert "/chaos/off" in script
