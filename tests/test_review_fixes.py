"""Regression tests for fixes from the 2026-11-25 code review."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from pydantic import ValidationError

from sre_agent.audit import AuditLog, verify_audit_file
from sre_agent.backend_url import fetch_json, validate_backend_url
from sre_agent.models import Incident, Target
from sre_agent.policy import Decision, PolicyResult, ProposedAction


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_fetch_json_refuses_redirects_and_keeps_credentials_home():
    seen: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.headers.get("DD-API-KEY"))
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server, base = _serve(Handler)
    try:
        import urllib.request

        request = urllib.request.Request(f"{base}/start", headers={"DD-API-KEY": "k"})
        with pytest.raises(urllib.error.HTTPError) as caught:
            fetch_json(request, timeout=5)
        assert caught.value.code == 302
        assert seen == ["k"]  # the redirect target was never requested
    finally:
        server.shutdown()


def test_fetch_json_refuses_an_oversized_reply():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"[" + b"1," * 100 + b"1]"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server, base = _serve(Handler)
    try:
        with pytest.raises(ValueError, match="exceeds"):
            fetch_json(base, timeout=5, max_bytes=50)
        assert fetch_json(base, timeout=5) == [1] * 101
    finally:
        server.shutdown()


def test_a_url_with_credentials_is_refused_without_echoing_them():
    with pytest.raises(ValueError) as caught:
        validate_backend_url("https://user:S3cretPW@metrics.example")
    assert "S3cretPW" not in str(caught.value)
    assert "metrics.example" in str(caught.value)


def test_concurrent_audit_records_keep_one_unbroken_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    action = ProposedAction(
        runbook_id="restart-workload",
        target=Target(environment="demo", namespace="shop", service="checkout"),
        rationale="test",
    )
    result = PolicyResult(decision=Decision.DENY, reason="test")

    def write() -> None:
        for _ in range(50):
            log.record_decision(incident_id="i", action=action, result=result)

    threads = [threading.Thread(target=write) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    records = log.records
    assert len(records) == 200
    assert len({r.prev_hash for r in records}) == 200
    assert verify_audit_file(path) == []


def test_an_incident_target_cannot_be_reassigned():
    incident = Incident(
        title="t",
        target=Target(environment="demo", namespace="shop", service="checkout"),
    )
    with pytest.raises(ValidationError):
        incident.target = Target(environment="prod", namespace="shop", service="x")


@pytest.mark.parametrize("module", ["model_backend", "model_proposals"])
def test_deeply_nested_model_json_is_refused_not_raised(module):
    deep = "[" * 200_000
    if module == "model_backend":
        from sre_agent.model_backend import parse_model_reply

        assert parse_model_reply(deep, None) == ([], [])  # type: ignore[arg-type]
    else:
        from sre_agent.model_proposals import _checked_reply

        assert _checked_reply(deep) is None


# --- Second 2026-11-25 review pass -----------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com?api_key=secret",
        "https://api.example.com/v1?x=1",
        "https://api.example.com/#frag",
    ],
)
def test_a_backend_url_with_a_query_or_fragment_is_refused(url):
    with pytest.raises(ValueError) as caught:
        validate_backend_url(url)
    assert "secret" not in str(caught.value)


def test_a_plain_backend_url_is_still_accepted():
    assert validate_backend_url("https://api.example.com/v1/") == (
        "https://api.example.com/v1"
    )


@pytest.mark.parametrize(
    "service", ["checkout,tier!=web", "a b", "app=x", "-bad", "x" * 64, ""]
)
def test_kubernetes_service_must_be_a_label_value(service):
    from sre_agent.adapters.kubernetes import ListPodsRequest

    with pytest.raises(ValidationError):
        ListPodsRequest(namespace="shop", service=service)


def test_kubernetes_ordinary_service_names_pass():
    from sre_agent.adapters.kubernetes import ListPodsRequest

    for name in ("checkout", "check_out.v2", "a", "x" * 63):
        assert ListPodsRequest(namespace="shop", service=name).service == name


def test_live_demo_http_exception_is_not_taken_from_the_address():
    from sre_agent.demo_live import live_demo_tool_layer

    with pytest.raises(ValueError):
        live_demo_tool_layer(prometheus_url="http://attacker.example:9090")
    with pytest.raises(ValueError):
        live_demo_tool_layer(sample_service_url="http://attacker.example:8000")
    # The Compose names themselves are still allowed.
    live_demo_tool_layer()


def test_a_corrupt_audit_tail_is_logged_not_silent(tmp_path, caplog):
    path = tmp_path / "audit.jsonl"
    path.write_text("{not json\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        AuditLog(path)
    assert any("genesis" in r.getMessage() for r in caplog.records)


def _alert(service="checkout", status="firing", fingerprint="fp-1", namespace="shop"):
    from sre_agent.ingestion import IncomingAlert

    return IncomingAlert(
        fingerprint=fingerprint,
        alert_name="HighErrorRate",
        title=f"HighErrorRate on {service}",
        target=Target(environment="demo", namespace=namespace, service=service),
        status=status,
        source="generic",
    )


def test_a_reused_fingerprint_does_not_fold_another_target_in():
    from sre_agent.ingestion import IncidentStore

    store = IncidentStore()
    checkout = store.ingest(_alert("checkout")).incident
    payments = store.ingest(_alert("payments"))
    assert payments.created and payments.incident.id != checkout.id


def test_a_reused_fingerprint_does_not_resolve_another_target():
    from sre_agent.ingestion import IncidentStore
    from sre_agent.models import IncidentStatus

    store = IncidentStore()
    checkout = store.ingest(_alert("checkout")).incident
    result = store.ingest(_alert("payments", status="resolved"))
    assert not result.resolved
    assert checkout.status is not IncidentStatus.RESOLVED


def test_empty_target_fields_are_refused_not_a_500():
    from fastapi.testclient import TestClient

    from sre_agent.ingestion import IncidentStore
    from sre_agent.server import create_app

    client = TestClient(create_app(IncidentStore()))
    for field in ("service", "namespace", "environment"):
        body = {"alert_name": "X", "service": "checkout", field: ""}
        assert client.post("/webhooks/generic", json=body).status_code == 422
    am = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "X", "service": "checkout",
                    "namespace": "", "environment": "",
                },
                "fingerprint": "f",
            }
        ],
    }
    response = client.post("/webhooks/alertmanager", json=am)
    assert response.status_code < 500


def test_concurrent_ingests_into_one_group_open_one_incident():
    import sys

    from sre_agent.ingestion import IncidentStore

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for _ in range(30):
            store = IncidentStore()
            barrier = threading.Barrier(4)

            def fire(i, store=store, barrier=barrier):
                barrier.wait()
                store.ingest(_alert(fingerprint=f"fp-{i}"))

            threads = [threading.Thread(target=fire, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert len(store.list_incidents()) == 1
    finally:
        sys.setswitchinterval(old)


def test_index_rows_for_an_unreadable_incident_are_dropped(tmp_path):
    import sqlite3

    from sre_agent.storage import SQLiteIncidentStore

    path = tmp_path / "store.db"
    with SQLiteIncidentStore(path) as store:
        incident = store.ingest(_alert()).incident
        store.ingest(_alert())  # a duplicate count row too
    conn = sqlite3.connect(path)
    conn.execute("UPDATE incidents SET data='{broken' WHERE id=?", (incident.id,))
    conn.commit()
    conn.close()
    with SQLiteIncidentStore(path) as reopened:
        assert reopened.ingest(_alert(status="resolved")).resolved is False
        assert reopened.ingest(_alert()).created


def test_a_failed_audit_write_does_not_advance_the_chain(tmp_path, monkeypatch):
    from pathlib import Path as _Path

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    action = ProposedAction(
        runbook_id="restart-workload",
        target=Target(environment="demo", namespace="shop", service="checkout"),
        parameters={},
        replicas_affected=1,
        rationale="t",
    )
    result = PolicyResult(decision=Decision.DENY, reason="t")
    log.record_decision(incident_id="i", action=action, result=result)
    real_open = _Path.open

    def failing_open(self, *a, **k):
        if self == path:
            raise OSError("disk full")
        return real_open(self, *a, **k)

    monkeypatch.setattr(_Path, "open", failing_open)
    with pytest.raises(OSError):
        log.record_decision(incident_id="i", action=action, result=result)
    monkeypatch.setattr(_Path, "open", real_open)
    log.record_decision(incident_id="i", action=action, result=result)
    assert len(log.records) == 2
    assert verify_audit_file(path) == []


@pytest.mark.parametrize(
    "query",
    [
        "AppTraces; .purge table T records <| where 1==1",
        "AppTraces |.drop table T",
        "AppTraces\n.append T <| AppTraces",
        "AppTraces;.rename table A to B",
    ],
)
def test_kql_dot_commands_are_refused_whatever_their_name(query):
    from sre_agent.adapters.azure import KqlQueryRequest

    with pytest.raises(ValidationError):
        KqlQueryRequest(query=query)


def test_ordinary_kql_still_passes():
    from sre_agent.adapters.azure import KqlQueryRequest

    KqlQueryRequest(query="AppTraces | where Message has 'a.b' | take 10")


def test_one_bad_datadog_point_does_not_discard_the_series():
    from sre_agent.adapters.datadog import parse_datadog_response

    payload = {
        "status": "ok",
        "series": [
            {
                "metric": "m",
                "pointlist": [[1e20, 1], [1.7e12, float("inf")], [1.7e12, 2.0]],
            }
        ]
    }
    series = parse_datadog_response(payload)
    assert [s.value for s in series[0].samples] == [2.0]


def test_a_negative_restart_count_does_not_discard_the_pod_list():
    from sre_agent.adapters.kubernetes import parse_kubernetes_pods

    payload = {
        "items": [
            {
                "metadata": {"name": "p", "namespace": "shop"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"ready": True, "restartCount": -1}],
                },
            }
        ]
    }
    pods = parse_kubernetes_pods(payload)
    assert len(pods) == 1 and pods[0].restarts == 0


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:abc", "http://127.0.0.1:0x50", "https://h.example:99999"]
)
def test_an_invalid_port_is_refused_up_front(url):
    with pytest.raises(ValueError, match="invalid port"):
        validate_backend_url(url)


def test_a_model_proposal_is_not_made_for_an_undiagnosed_incident():
    from sre_agent.model_proposals import LanguageModelProposer, submit_model_proposal
    from sre_agent.models import IncidentStatus
    from sre_agent.policy import EnvironmentPolicy, Tier
    from sre_agent.runbooks import demo_runbooks

    class Client:
        def complete(self, prompt: str) -> str:
            return (
                '{"runbook_id": "restart-workload", "parameters": {}, '
                '"rationale": "x"}'
            )

    executor, backend = demo_runbooks()
    incident = Incident(
        title="t", target=Target(environment="demo", namespace="shop", service="checkout")
    )
    incident.transition(IncidentStatus.TRIAGING)
    incident.transition(IncidentStatus.RESOLVED)
    policy = EnvironmentPolicy(
        environment="demo", granted_tier=Tier.ACT_WITHIN_BOUNDS, max_replicas_affected=5
    )
    outcome = submit_model_proposal(
        LanguageModelProposer(Client()), incident, runbook_backend=backend,
        executor=executor, policy=policy, audit_log=AuditLog(),
    )
    assert outcome.run_result is None
    state = backend.get(incident.target)
    assert state is not None and state.restarts == 0
