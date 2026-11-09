"""The webhook server: health, both webhook shapes, incident reads."""

import pytest
from fastapi.testclient import TestClient

from sre_agent.ingestion import IncidentStore
from sre_agent.server import create_app


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
