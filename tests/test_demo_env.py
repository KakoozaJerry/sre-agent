"""Demo environment v1: the sample service and the wiring.

Three kinds of checks live here:

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
- **The stack's visitor path (Phase 10).** The server the Compose
  file runs is the shipped app, so the whole path a visitor takes
  through the stack -- the Alertmanager-shaped alert the demo's
  own config would send, the diagnosis, a restart approved by
  name, and the resolved alert that closes the incident -- is run
  against that app here, with the demo files' own labels and
  summary text.
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


def test_compose_agent_runs_the_live_factory() -> None:
    # Phase 12: the Compose demo's agent is the live factory, so
    # its diagnosis reads the stack's own Prometheus and sample
    # service — named here by their Compose addresses — and not
    # the shipped app's seeded demo data.
    compose = _read(REPO_ROOT / "docker-compose.yml")
    assert "sre_agent.demo_live:create_live_demo_app" in compose
    assert "SRE_AGENT_PROMETHEUS_URL=http://prometheus:9090" in compose
    assert "SRE_AGENT_SAMPLE_SERVICE_URL=http://sample-service:8000" in compose
    assert "sre_agent.server:app" not in compose


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


# ---------------------------------------------------------------------------
# The stack's visitor path (Phase 10): the shipped server, driven the
# way the demo's own Prometheus and Alertmanager files would drive it
# ---------------------------------------------------------------------------

# The labels and summary are copied from demo/prometheus/alerts.yml:
# they are what Prometheus attaches to the firing alert and what
# Alertmanager then posts to the agent's webhook.
_STACK_LABELS = {
    "alertname": "HighErrorRate",
    "service": "checkout",
    "severity": "critical",
    "namespace": "shop",
    "environment": "demo",
}
_STACK_SUMMARY = "Checkout is returning errors in the demo environment"


def _stack_payload(status: str) -> dict:
    return {
        "status": status,
        "commonLabels": _STACK_LABELS,
        "alerts": [
            {
                "status": status,
                "labels": _STACK_LABELS,
                "annotations": {"summary": _STACK_SUMMARY},
                "startsAt": "2026-11-08T20:00:00Z",
                "fingerprint": "demo-stack-visitor-path",
            }
        ],
    }


def test_the_stack_server_runs_the_whole_visitor_path() -> None:
    from fastapi.testclient import TestClient

    from sre_agent.server import app as shipped_app

    client = TestClient(shipped_app)
    received = client.post(
        "/webhooks/alertmanager", json=_stack_payload("firing")
    ).json()
    assert received["results"][0]["created"] is True
    incident_id = received["results"][0]["incident_id"]

    # The alert arrives diagnosed, not just recorded: the server
    # the Compose file runs diagnoses from the seeded demo data.
    incident = client.get(f"/incidents/{incident_id}").json()
    assert incident["status"] == "diagnosed"
    assert incident["diagnosis"]
    assert incident["evidence"]

    # The restart waits for a person, under the shipped Tier 2
    # policy, and runs only when approved by name.
    proposed = client.post(
        f"/incidents/{incident_id}/actions",
        json={"runbook_id": "restart-workload"},
    )
    assert proposed.status_code == 201
    body = proposed.json()
    assert body["decision"] == "require_approval"
    pending = body["pending"]
    assert pending["runbook_id"] == "restart-workload"
    approved = client.post(
        f"/incidents/{incident_id}/approvals/{pending['id']}/approve",
        json={"by": "A Visitor"},
    )
    assert approved.status_code == 200
    incident = client.get(f"/incidents/{incident_id}").json()
    assert incident["status"] == "mitigating"

    # The resolved alert -- send_resolved is on in the demo's
    # Alertmanager config -- closes the incident.
    closed = client.post(
        "/webhooks/alertmanager", json=_stack_payload("resolved")
    ).json()
    assert closed["results"][0]["resolved"] is True
    incident = client.get(f"/incidents/{incident_id}").json()
    assert incident["status"] == "resolved"


def test_demo_guide_matches_the_stack_as_it_runs_today() -> None:
    guide = _read(REPO_ROOT / "docs" / "demo.md")
    # Phase 10: the guide once said the loop was not wired and
    # nothing could be fixed; both stopped being true in Phase 7,
    # and the guide must not say them again.
    assert "not** wired" not in guide
    assert "Nothing in the demo can be fixed" not in guide
    # Phase 12: the guide once said the Compose diagnosis reads
    # seeded demo data, not the live Prometheus numbers; that
    # stopped being true when the demo was wired to live
    # Prometheus, and the guide must not say it again.
    assert "diagnosis reads seeded demo data" not in guide
    assert "not the demo's live" not in guide
    for needle in (
        "docker compose up --build",
        "diagnosed",
        "approve",
        "live Prometheus",
        "seeded demo workloads",
        "no Docker in the build environment",
    ):
        assert needle in guide


# ---------------------------------------------------------------------------
# Hardening (finding F-06): non-root images, hashed dependency lock
# ---------------------------------------------------------------------------


def test_both_demo_images_run_as_a_named_non_root_user() -> None:
    for path, user in (
        (REPO_ROOT / "Dockerfile", "sreagent"),
        (DEMO / "sample_service" / "Dockerfile", "sample"),
    ):
        text = _read(path)
        assert "useradd" in text and user in text
        assert f"\nUSER {user}\n" in text
        assert "USER root" not in text


def test_agent_image_installs_dependencies_from_the_hashed_lock() -> None:
    agent_image = _read(REPO_ROOT / "Dockerfile")
    assert "requirements.lock" in agent_image
    assert "--require-hashes -r requirements.lock" in agent_image
    # The package itself adds no unpinned dependencies on top.
    assert "--no-deps" in agent_image


def test_requirements_lock_pins_every_package_with_a_hash() -> None:
    lock = _read(REPO_ROOT / "requirements.lock")
    entries = [
        line
        for line in lock.splitlines()
        if line and not line.startswith((" ", "#"))
    ]
    assert len(entries) == 17
    for entry in entries:
        assert "==" in entry, entry
    hashes = [part for part in lock.split() if part.startswith("--hash=sha256:")]
    assert len(hashes) == len(entries)
    for hashed in hashes:
        digest = hashed.removeprefix("--hash=sha256:")
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
    # The direct dependencies from pyproject.toml are all locked.
    for name in ("pydantic==", "fastapi==", "uvicorn==", "httpx=="):
        assert name in lock
