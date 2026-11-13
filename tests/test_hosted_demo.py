"""The public-safe hosted demo mode (Phase 10).

The hosted demo is the one server build meant for a public
address, so its safety rules are pinned here, not just described:

- it is not built at all without a token;
- ``/health`` stays open and every other route needs the token;
- a visitor's whole path works with the token: send the demo
  alert, read the diagnosis, run the pre-approved restart, and
  close the incident with a resolved alert;
- the Tier 3 demo grant is the only action that can run: a fix
  that needs a human approval (rollback) is refused, creates no
  pending action, and the approval routes are disabled;
- the ingestion caps stay on (an oversize payload is refused);
- the guide exists and states the honest limits, including the
  live public URL (deployed 2026-11-12).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sre_agent.hosted_demo import create_hosted_demo_app
from sre_agent.server import ENV_API_TOKEN, TOKEN_HEADER

REPO_ROOT = Path(__file__).resolve().parents[1]

TOKEN = "hosted-demo-test-token"
HEADERS = {TOKEN_HEADER: TOKEN}

GENERIC_ALERT = {
    "alert_name": "HighErrorRate",
    "service": "checkout",
    "namespace": "shop",
    "environment": "demo",
    "severity": "critical",
    "summary": "Checkout errors are above 10%",
    "fingerprint": "hosted-demo-1",
}


@pytest.fixture()
def hosted_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv(ENV_API_TOKEN, raising=False)
    return TestClient(create_hosted_demo_app(auth_token=TOKEN))


def test_no_token_no_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_API_TOKEN, raising=False)
    with pytest.raises(ValueError, match="requires an API token"):
        create_hosted_demo_app()
    with pytest.raises(ValueError, match="requires an API token"):
        create_hosted_demo_app(auth_token="")


def test_token_can_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_API_TOKEN, TOKEN)
    client = TestClient(create_hosted_demo_app())
    assert client.get("/incidents", headers=HEADERS).status_code == 200


def test_health_is_open_and_everything_else_needs_the_token(
    hosted_client: TestClient,
) -> None:
    assert hosted_client.get("/health").status_code == 200
    assert hosted_client.get("/health").json() == {"status": "ok"}
    assert hosted_client.get("/incidents").status_code == 401
    wrong = {TOKEN_HEADER: "not-the-token"}
    assert hosted_client.get("/incidents", headers=wrong).status_code == 401
    refused = hosted_client.post("/webhooks/generic", json=GENERIC_ALERT)
    assert refused.status_code == 401
    # The refused webhook ingested nothing.
    assert hosted_client.get("/incidents", headers=HEADERS).json() == []


def _open_diagnosed_incident(client: TestClient) -> str:
    body = client.post(
        "/webhooks/generic", json=GENERIC_ALERT, headers=HEADERS
    ).json()
    assert body["created"] is True
    incident_id = body["incident_id"]
    incident = client.get(
        f"/incidents/{incident_id}", headers=HEADERS
    ).json()
    assert incident["status"] == "diagnosed"
    assert incident["diagnosis"]
    assert incident["evidence"]
    return incident_id


def test_visitor_path_alert_diagnosis_tier3_restart_and_close(
    hosted_client: TestClient,
) -> None:
    incident_id = _open_diagnosed_incident(hosted_client)
    proposed = hosted_client.post(
        f"/incidents/{incident_id}/actions",
        json={"runbook_id": "restart-workload"},
        headers=HEADERS,
    )
    assert proposed.status_code == 201
    body = proposed.json()
    # The Tier 3 grant allows the restart outright: it runs at
    # once, with no pending approval created for anyone.
    assert body["decision"] == "allow"
    assert body["summary"]
    assert body["pending"] is None
    assert body["incident_status"] == "mitigating"
    assert (
        hosted_client.get(
            f"/incidents/{incident_id}/approvals", headers=HEADERS
        ).json()
        == []
    )
    resolved = hosted_client.post(
        "/webhooks/generic",
        json={**GENERIC_ALERT, "status": "resolved"},
        headers=HEADERS,
    ).json()
    assert resolved["resolved"] is True
    incident = hosted_client.get(
        f"/incidents/{incident_id}", headers=HEADERS
    ).json()
    assert incident["status"] == "resolved"


def test_a_fix_needing_approval_cannot_run_in_hosted_mode(
    hosted_client: TestClient,
) -> None:
    incident_id = _open_diagnosed_incident(hosted_client)
    # Rollback is not in the Tier 3 grant, so it needs a human.
    # Hosted mode has no approval path: it is refused, runs
    # nothing, and leaves no pending action behind.
    proposed = hosted_client.post(
        f"/incidents/{incident_id}/actions",
        json={"runbook_id": "rollback-deployment"},
        headers=HEADERS,
    )
    assert proposed.status_code == 403
    assert "approvals are disabled" in proposed.json()["detail"]
    incident = hosted_client.get(
        f"/incidents/{incident_id}", headers=HEADERS
    ).json()
    assert incident["status"] == "diagnosed"
    assert (
        hosted_client.get(
            f"/incidents/{incident_id}/approvals", headers=HEADERS
        ).json()
        == []
    )
    approve = hosted_client.post(
        f"/incidents/{incident_id}/approvals/any-id/approve",
        json={"by": "A Visitor"},
        headers=HEADERS,
    )
    assert approve.status_code == 409
    assert "approvals are disabled" in approve.json()["detail"]


def test_ingestion_caps_stay_on_in_hosted_mode(
    hosted_client: TestClient,
) -> None:
    one_alert = {
        "status": "firing",
        "labels": {
            "alertname": "HighErrorRate",
            "service": "checkout",
            "severity": "critical",
        },
        "annotations": {"summary": "Checkout errors are above 10%"},
        "startsAt": "2026-11-08T20:00:00Z",
        "fingerprint": "cap-test",
    }
    payload = {
        "status": "firing",
        "commonLabels": {"environment": "demo", "namespace": "shop"},
        "alerts": [one_alert] * 101,
    }
    response = hosted_client.post(
        "/webhooks/alertmanager", json=payload, headers=HEADERS
    )
    assert response.status_code == 422
    assert hosted_client.get("/incidents", headers=HEADERS).json() == []


def test_hosted_demo_guide_states_the_rules_and_the_limits() -> None:
    text = (REPO_ROOT / "docs" / "hosted-demo.md").read_text(
        encoding="utf-8"
    )
    for needle in (
        "Tier 3",
        "Seeded demo data only",
        "SRE_AGENT_API_TOKEN",
        "https://sre-agent-demo.onrender.com",
        "rollback",
        "100 alerts",
    ):
        assert needle in text
    assert "no public URL yet" not in text
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/hosted-demo.md" in readme
