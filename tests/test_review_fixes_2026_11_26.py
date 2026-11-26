"""Regression tests for fixes from the 2026-11-26 code review."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from sre_agent.hosted_demo import create_hosted_demo_app
from sre_agent.models import Incident, IncidentStatus, Target
from sre_agent.reasoning import ReasoningLoop
from sre_agent.runbooks import RunbookExecutor
from sre_agent.server import ENV_API_TOKEN, TOKEN_HEADER
from sre_agent.tools import demo_tool_layer

TOKEN = "review-2026-11-26-token"
HEADERS = {TOKEN_HEADER: TOKEN}


def _incident() -> Incident:
    return Incident(
        title="HighErrorRate on checkout",
        target=Target(environment="demo", namespace="shop", service="checkout"),
    )


# --- R1: a failing core source escalates instead of stranding ---


class _FailingMetrics:
    """The demo tool layer, with the metrics source down."""

    def __init__(self) -> None:
        self._inner = demo_tool_layer()

    def get_service_status(self, target):  # type: ignore[no-untyped-def]
        return self._inner.get_service_status(target)

    def search_logs(self, request):  # type: ignore[no-untyped-def]
        return self._inner.search_logs(request)

    def query_metrics(self, request):  # type: ignore[no-untyped-def]
        raise ConnectionError("prometheus unreachable")


def test_a_core_source_failure_escalates_with_no_claims():
    incident = _incident()
    report = ReasoningLoop(_FailingMetrics()).diagnose(incident)  # type: ignore[arg-type]
    assert incident.status is IncidentStatus.ESCALATED
    assert report.claims == []
    assert report.hypotheses == []
    assert "core evidence source failed" in report.summary
    # The evidence gathered before the failure is kept on the record.
    assert len(incident.evidence) == 2


def test_a_failing_first_step_still_escalates():
    class Down:
        def get_service_status(self, target):  # type: ignore[no-untyped-def]
            raise TimeoutError("status endpoint timed out")

    incident = _incident()
    report = ReasoningLoop(Down()).diagnose(incident)  # type: ignore[arg-type]
    assert incident.status is IncidentStatus.ESCALATED
    assert report.steps_used == 1
    assert incident.evidence == []


# --- R2: concurrent Tier 3 proposals run the action once ---


def test_concurrent_tier3_proposals_run_the_action_once(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(ENV_API_TOKEN, raising=False)
    runs: list[str] = []
    original = RunbookExecutor.execute

    def slow_execute(self, action, decision):  # type: ignore[no-untyped-def]
        runs.append(action.runbook_id)
        time.sleep(0.2)  # widen the check-then-run window
        return original(self, action, decision)

    monkeypatch.setattr(RunbookExecutor, "execute", slow_execute)
    client = TestClient(create_hosted_demo_app(auth_token=TOKEN))
    incident_id = client.post(
        "/webhooks/generic",
        json={
            "alert_name": "HighErrorRate",
            "service": "checkout",
            "namespace": "shop",
            "environment": "demo",
            "severity": "critical",
            "summary": "Checkout errors are above 10%",
            "fingerprint": "review-2026-11-26",
        },
        headers=HEADERS,
    ).json()["incident_id"]

    codes: list[int] = []

    def propose() -> None:
        response = client.post(
            f"/incidents/{incident_id}/actions",
            json={"runbook_id": "restart-workload"},
            headers=HEADERS,
        )
        codes.append(response.status_code)

    threads = [threading.Thread(target=propose) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(codes) == [201, 409]
    assert runs == ["restart-workload"]
