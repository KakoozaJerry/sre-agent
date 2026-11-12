"""The database store: incidents, diagnoses and approvals persist.

``SQLiteIncidentStore`` must behave exactly like the in-memory
store while it runs (same dedupe, grouping, resolving and caps —
that logic is inherited) and must give the same state back after
a restart: a fresh store object on the same file. The server
tests here pin the live path end to end: a diagnosis written by
the server survives a restart, and a pending approval created
before it can still be approved after it — once, with its
original expiry — while a decided one stays decided.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sre_agent.ingestion import (
    GenericAlertPayload,
    IncidentStore,
    IngestionLimitError,
    normalize_generic,
)
from sre_agent.models import IncidentStatus
from sre_agent.policy import EnvironmentPolicy, Tier
from sre_agent.runbooks import demo_runbooks
from sre_agent.server import create_app
from sre_agent.storage import SQLiteIncidentStore
from sre_agent.tools import demo_tool_layer

GENERIC_ALERT = {
    "alert_name": "HighErrorRate",
    "service": "checkout",
    "namespace": "shop",
    "environment": "demo",
    "severity": "critical",
    "summary": "Checkout errors are above 10%",
    "fingerprint": "abc123",
}


def _generic(**overrides) -> GenericAlertPayload:
    body: dict = {
        "alert_name": "HighErrorRate",
        "service": "checkout",
        "namespace": "shop",
        "environment": "demo",
        "severity": "critical",
        "summary": "Checkout errors are above 10%",
        "fingerprint": "abc123",
    }
    body.update(overrides)
    return GenericAlertPayload(**body)


def _policy() -> EnvironmentPolicy:
    return EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITH_APPROVAL,
        max_replicas_affected=5,
    )


def _server(store: IncidentStore, backend) -> TestClient:
    return TestClient(
        create_app(
            store,
            tool_layer=demo_tool_layer(),
            approval_policy=_policy(),
            runbook_backend=backend,
        )
    )


# --- the store itself, across a restart ---


def test_incidents_survive_a_restart_with_dedupe_and_grouping(
    tmp_path: Path,
):
    path = tmp_path / "incidents.db"
    store = SQLiteIncidentStore(path)
    opened = store.ingest(normalize_generic(_generic()))
    assert opened.incident is not None
    store.ingest(normalize_generic(_generic()))  # a duplicate
    store.close()

    reopened = SQLiteIncidentStore(path)
    incidents = reopened.list_incidents()
    assert [inc.id for inc in incidents] == [opened.incident.id]
    assert reopened.duplicate_count(opened.incident.id) == 1
    # The fingerprint index survived: the same alert is still a
    # duplicate, not a new incident.
    again = reopened.ingest(normalize_generic(_generic()))
    assert again.duplicate is True
    assert len(reopened.list_incidents()) == 1
    # The group index survived: a related alert still groups, and
    # a resolved alert still closes the incident.
    grouped = reopened.ingest(normalize_generic(_generic(fingerprint="def456")))
    assert grouped.grouped is True
    closed = reopened.ingest(normalize_generic(_generic(status="resolved")))
    assert closed.resolved is True
    assert closed.incident is not None
    assert closed.incident.status is IncidentStatus.RESOLVED
    reopened.close()

    third = SQLiteIncidentStore(path)
    stored = third.get(opened.incident.id)
    assert stored is not None
    assert stored.status is IncidentStatus.RESOLVED
    assert len(stored.evidence) == 2
    third.close()


def test_a_diagnosis_saved_through_save_survives_a_restart(tmp_path: Path):
    from sre_agent.memory import demo_incident_memory
    from sre_agent.reasoning import ReasoningLoop

    path = tmp_path / "diagnosed.db"
    store = SQLiteIncidentStore(path)
    result = store.ingest(normalize_generic(_generic()))
    assert result.incident is not None
    incident = result.incident
    report = ReasoningLoop(
        demo_tool_layer(), memory=demo_incident_memory()
    ).diagnose(incident)
    store.save(incident)
    store.close()

    reopened = SQLiteIncidentStore(path)
    stored = reopened.get(incident.id)
    assert stored is not None
    assert stored.status is IncidentStatus.DIAGNOSED
    assert stored.diagnosis == report.summary
    assert stored.hypotheses
    assert len(stored.evidence) == len(incident.evidence)
    # The memory advice attached to the diagnosis persists with it.
    assert stored.memory_report is not None
    assert stored.memory_report.past_incidents_held == 4
    reopened.close()


def test_the_intake_caps_still_apply_after_a_restart(tmp_path: Path):
    path = tmp_path / "caps.db"
    store = SQLiteIncidentStore(path, max_incidents=1)
    store.ingest(normalize_generic(_generic()))
    store.close()

    reopened = SQLiteIncidentStore(path, max_incidents=1)
    with pytest.raises(IngestionLimitError):
        reopened.ingest(
            normalize_generic(_generic(service="payments", fingerprint="zzz999"))
        )
    assert len(reopened.list_incidents()) == 1
    reopened.close()


def test_the_in_memory_store_answers_the_persistence_hooks_plainly():
    store = IncidentStore()
    result = store.ingest(normalize_generic(_generic()))
    assert result.incident is not None
    store.save(result.incident)  # must not raise or duplicate
    assert len(store.list_incidents()) == 1
    assert store.load_approvals() == ({}, {})


# --- the server on a database store, across a restart ---


def test_a_server_diagnosis_survives_a_restart(tmp_path: Path):
    path = tmp_path / "server.db"
    _, backend = demo_runbooks()
    store = SQLiteIncidentStore(path)
    client = _server(store, backend)
    body = client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    assert body["status"] == "diagnosed"
    store.close()

    reopened_store = SQLiteIncidentStore(path)
    client2 = _server(reopened_store, backend)
    found = client2.get(f"/incidents/{body['incident_id']}").json()
    assert found["status"] == "diagnosed"
    assert found["diagnosis"] == body["diagnosis"]
    assert found["hypotheses"]
    # The dedupe index survived the restart too.
    again = client2.post("/webhooks/generic", json=GENERIC_ALERT).json()
    assert again["duplicate"] is True
    reopened_store.close()


def test_a_pending_approval_survives_a_restart_and_runs_once(
    tmp_path: Path,
):
    from sre_agent.models import Target

    path = tmp_path / "approvals.db"
    _, backend = demo_runbooks()
    store = SQLiteIncidentStore(path)
    client = _server(store, backend)
    incident_id = client.post("/webhooks/generic", json=GENERIC_ALERT).json()[
        "incident_id"
    ]
    proposed = client.post(
        f"/incidents/{incident_id}/actions",
        json={"runbook_id": "restart-workload"},
    )
    assert proposed.status_code == 201
    pending = proposed.json()["pending"]
    store.close()

    # A new server on the same file: the pending approval is back,
    # with the same id and the same expiry it was proposed with.
    reopened_store = SQLiteIncidentStore(path)
    client2 = _server(reopened_store, backend)
    listed = client2.get(f"/incidents/{incident_id}/approvals").json()
    assert [row["id"] for row in listed] == [pending["id"]]
    assert listed[0]["status"] == "pending"
    assert listed[0]["expires_at"] == pending["expires_at"]

    approved = client2.post(
        f"/incidents/{incident_id}/approvals/{pending['id']}/approve",
        json={"by": "Abraham"},
    )
    assert approved.status_code == 200
    assert approved.json()["incident_status"] == "mitigating"
    state = backend.get(
        Target(environment="demo", namespace="shop", service="checkout")
    )
    assert state is not None and state.restarts == 1
    reopened_store.close()

    # A third server: the decision and the run's effect on the
    # incident both persisted, and the approval cannot run again.
    third_store = SQLiteIncidentStore(path)
    client3 = _server(third_store, backend)
    listed = client3.get(f"/incidents/{incident_id}/approvals").json()
    assert listed[0]["status"] == "approved"
    again = client3.post(
        f"/incidents/{incident_id}/approvals/{pending['id']}/approve",
        json={"by": "Abraham"},
    )
    assert again.status_code == 409
    found = client3.get(f"/incidents/{incident_id}").json()
    assert found["status"] == "mitigating"
    assert any(ev["kind"] == "action" for ev in found["evidence"])
    state = backend.get(
        Target(environment="demo", namespace="shop", service="checkout")
    )
    assert state is not None and state.restarts == 1
    third_store.close()


def test_a_rejected_approval_stays_rejected_after_a_restart(tmp_path: Path):
    path = tmp_path / "rejected.db"
    _, backend = demo_runbooks()
    store = SQLiteIncidentStore(path)
    client = _server(store, backend)
    incident_id = client.post("/webhooks/generic", json=GENERIC_ALERT).json()[
        "incident_id"
    ]
    pending = client.post(
        f"/incidents/{incident_id}/actions",
        json={"runbook_id": "restart-workload"},
    ).json()["pending"]
    rejected = client.post(
        f"/incidents/{incident_id}/approvals/{pending['id']}/reject",
        json={"by": "Abraham"},
    )
    assert rejected.status_code == 200
    store.close()

    reopened_store = SQLiteIncidentStore(path)
    client2 = _server(reopened_store, backend)
    listed = client2.get(f"/incidents/{incident_id}/approvals").json()
    assert listed[0]["status"] == "rejected"
    late = client2.post(
        f"/incidents/{incident_id}/approvals/{pending['id']}/approve",
        json={"by": "Abraham"},
    )
    assert late.status_code == 409
    reopened_store.close()
