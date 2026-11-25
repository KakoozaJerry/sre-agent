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
