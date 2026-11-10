"""The webhook server: health, both webhook shapes, incident reads.

Plus the post-v0.1.0 server hardening: a shared token, when one is
set, is required on every route except /health (finding F-01), and
a full incident store answers 429 instead of growing without a
bound (finding F-02).
"""

import pytest
from fastapi.testclient import TestClient

from sre_agent.ingestion import IncidentStore
from sre_agent.server import TOKEN_HEADER, create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(IncidentStore()))


GENERIC_ALERT = {
    "alert_name": "HighErrorRate",
    "service": "checkout",
    "namespace": "shop",
    "environment": "demo",
    "severity": "critical",
    "summary": "Checkout errors are above 10%",
    "fingerprint": "abc123",
}

ALERTMANAGER_BODY = {
    "status": "firing",
    "commonLabels": {"environment": "demo", "namespace": "shop"},
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "HighErrorRate",
                "service": "checkout",
                "severity": "critical",
            },
            "annotations": {"summary": "Checkout errors are above 10%"},
            "startsAt": "2026-11-08T20:00:00Z",
            "fingerprint": "abc123",
        }
    ],
}


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_generic_webhook_opens_an_incident(client: TestClient):
    response = client.post("/webhooks/generic", json=GENERIC_ALERT)
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["incident_id"]
    listed = client.get("/incidents").json()
    assert len(listed) == 1
    assert listed[0]["status"] == "triaging"
    assert listed[0]["title"] == "Checkout errors are above 10%"


def test_generic_webhook_repeat_is_a_duplicate(client: TestClient):
    client.post("/webhooks/generic", json=GENERIC_ALERT)
    again = client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    assert again["duplicate"] is True
    assert len(client.get("/incidents").json()) == 1


def test_alertmanager_webhook_opens_an_incident(client: TestClient):
    response = client.post("/webhooks/alertmanager", json=ALERTMANAGER_BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 1
    assert body["results"][0]["created"] is True
    assert body["active_incidents"] == 1


def test_get_one_incident_and_missing_incident_404s(client: TestClient):
    created = client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    found = client.get(f"/incidents/{created['incident_id']}")
    assert found.status_code == 200
    assert found.json()["duplicate_count"] == 0
    assert client.get("/incidents/does-not-exist").status_code == 404


# --- F-01: token authentication, when a token is set ---


@pytest.fixture()
def authed_client() -> TestClient:
    return TestClient(
        create_app(IncidentStore(), auth_token="test-token")
    )


def test_health_stays_open_when_a_token_is_set(authed_client: TestClient):
    response = authed_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_without_a_token_is_refused_and_ingests_nothing(
    authed_client: TestClient,
):
    response = authed_client.post("/webhooks/generic", json=GENERIC_ALERT)
    assert response.status_code == 401
    headers = {TOKEN_HEADER: "test-token"}
    assert authed_client.get("/incidents", headers=headers).json() == []


def test_webhook_with_a_wrong_token_is_refused(authed_client: TestClient):
    response = authed_client.post(
        "/webhooks/generic",
        json=GENERIC_ALERT,
        headers={TOKEN_HEADER: "not-the-token"},
    )
    assert response.status_code == 401


def test_webhook_and_reads_work_with_the_token(authed_client: TestClient):
    headers = {TOKEN_HEADER: "test-token"}
    created = authed_client.post(
        "/webhooks/generic", json=GENERIC_ALERT, headers=headers
    )
    assert created.status_code == 200
    assert created.json()["created"] is True
    assert authed_client.get("/incidents").status_code == 401
    listed = authed_client.get("/incidents", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_token_can_come_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SRE_AGENT_API_TOKEN", "env-token")
    env_client = TestClient(create_app(IncidentStore()))
    assert env_client.get("/incidents").status_code == 401
    ok = env_client.get(
        "/incidents", headers={TOKEN_HEADER: "env-token"}
    )
    assert ok.status_code == 200


# --- F-02: intake caps at the server boundary ---


def test_a_full_store_answers_429_not_a_new_incident():
    full_client = TestClient(create_app(IncidentStore(max_incidents=1)))
    first = full_client.post("/webhooks/generic", json=GENERIC_ALERT)
    assert first.status_code == 200
    other = {**GENERIC_ALERT, "service": "payments", "fingerprint": "zzz999"}
    refused = full_client.post("/webhooks/generic", json=other)
    assert refused.status_code == 429
    assert "full" in refused.json()["detail"]


def test_an_oversize_alertmanager_payload_is_refused(client: TestClient):
    one_alert = ALERTMANAGER_BODY["alerts"][0]
    body = {**ALERTMANAGER_BODY, "alerts": [one_alert] * 101}
    response = client.post("/webhooks/alertmanager", json=body)
    assert response.status_code == 422
    assert client.get("/incidents").json() == []
