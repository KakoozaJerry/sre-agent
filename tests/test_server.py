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
from sre_agent.tools import demo_tool_layer


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


# --- Phase 7: the reasoning loop runs inside the server ---


@pytest.fixture()
def diagnosed_client() -> TestClient:
    return TestClient(
        create_app(IncidentStore(), tool_layer=demo_tool_layer())
    )


def test_a_new_incident_is_diagnosed_by_the_server(
    diagnosed_client: TestClient,
):
    response = diagnosed_client.post("/webhooks/generic", json=GENERIC_ALERT)
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["status"] == "diagnosed"
    assert body["diagnosis"] is not None
    assert body["diagnosis"].startswith("Most likely:")

    found = diagnosed_client.get(f"/incidents/{body['incident_id']}").json()
    assert found["status"] == "diagnosed"
    assert found["diagnosis"] == body["diagnosis"]
    assert found["hypotheses"], "the stored diagnosis must carry its causes"
    evidence_ids = {ev["id"] for ev in found["evidence"]}
    kinds = {ev["kind"] for ev in found["evidence"]}
    assert {"service", "log", "metric"} <= kinds
    for hyp in found["hypotheses"]:
        assert hyp["supporting_evidence"], "a cause must cite evidence"
        assert set(hyp["supporting_evidence"]) <= evidence_ids

    listed = diagnosed_client.get("/incidents").json()
    assert listed[0]["status"] == "diagnosed"
    assert listed[0]["diagnosis"] == body["diagnosis"]


def test_the_cli_show_text_carries_the_server_diagnosis(
    diagnosed_client: TestClient,
):
    from sre_agent.cli import format_incident_detail

    created = diagnosed_client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    found = diagnosed_client.get(f"/incidents/{created['incident_id']}").json()
    found["duplicate_count"] = 0
    text = format_incident_detail(found)
    assert "Diagnosis: Most likely:" in text
    assert "Possible causes, as recorded:" in text


def test_alertmanager_incidents_are_diagnosed_too(
    diagnosed_client: TestClient,
):
    body = diagnosed_client.post(
        "/webhooks/alertmanager", json=ALERTMANAGER_BODY
    ).json()
    assert body["results"][0]["status"] == "diagnosed"
    assert body["results"][0]["diagnosis"] is not None


def test_an_unknown_service_escalates_instead_of_guessing(
    diagnosed_client: TestClient,
):
    alert = {**GENERIC_ALERT, "service": "payments", "fingerprint": "pay111"}
    body = diagnosed_client.post("/webhooks/generic", json=alert).json()
    assert body["status"] == "escalated"
    assert body["diagnosis"] is None


def test_a_duplicate_is_not_diagnosed_again(diagnosed_client: TestClient):
    first = diagnosed_client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    again = diagnosed_client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    assert again["duplicate"] is True
    assert again["status"] == "diagnosed"
    assert again["diagnosis"] == first["diagnosis"]
    found = diagnosed_client.get(f"/incidents/{first['incident_id']}").json()
    # Still exactly the alert record plus the loop's four records.
    assert len(found["evidence"]) == 5


def test_a_grouped_update_keeps_a_diagnosis_already_made(
    diagnosed_client: TestClient,
):
    first = diagnosed_client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    grouped_alert = {**GENERIC_ALERT, "fingerprint": "def456"}
    grouped = diagnosed_client.post(
        "/webhooks/generic", json=grouped_alert
    ).json()
    assert grouped["grouped"] is True
    assert grouped["status"] == "diagnosed"
    assert grouped["diagnosis"] == first["diagnosis"]


def test_a_grouped_update_rediagnoses_an_escalated_incident(
    diagnosed_client: TestClient,
):
    alert = {**GENERIC_ALERT, "service": "payments", "fingerprint": "pay111"}
    first = diagnosed_client.post("/webhooks/generic", json=alert).json()
    assert first["status"] == "escalated"
    update = {**alert, "fingerprint": "pay222"}
    body = diagnosed_client.post("/webhooks/generic", json=update).json()
    assert body["grouped"] is True
    # Re-diagnosed without an error; with no new evidence of a
    # cause, it goes back to a human rather than guessing.
    assert body["status"] == "escalated"
    assert body["diagnosis"] is None


def test_a_server_without_a_tool_layer_records_only(client: TestClient):
    body = client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    assert body["status"] == "triaging"
    assert body["diagnosis"] is None


# --- Phase 7: live approvals are served by the server ---


@pytest.fixture()
def approvals_setup():
    from sre_agent.audit import AuditLog
    from sre_agent.policy import EnvironmentPolicy, Tier
    from sre_agent.runbooks import demo_runbooks

    _, backend = demo_runbooks()
    audit = AuditLog()
    policy = EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITH_APPROVAL,
        max_replicas_affected=5,
    )
    client = TestClient(
        create_app(
            IncidentStore(),
            tool_layer=demo_tool_layer(),
            approval_policy=policy,
            runbook_backend=backend,
            audit_log=audit,
        )
    )
    return client, backend, policy, audit


def _diagnosed_incident(client: TestClient) -> str:
    body = client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    assert body["status"] == "diagnosed"
    return str(body["incident_id"])


def _propose(client: TestClient, incident_id: str, **overrides):
    payload = {"runbook_id": "restart-workload", **overrides}
    return client.post(f"/incidents/{incident_id}/actions", json=payload)


def test_a_proposal_becomes_a_pending_approval(approvals_setup):
    client, _, _, audit = approvals_setup
    incident_id = _diagnosed_incident(client)
    response = _propose(client, incident_id)
    assert response.status_code == 201
    body = response.json()
    assert body["decision"] == "require_approval"
    pending = body["pending"]
    assert pending["status"] == "pending"
    assert pending["runbook_id"] == "restart-workload"
    assert pending["target"]["service"] == "checkout"
    assert pending["replicas_affected"] == 2
    assert len(pending["parameters_hash"]) == 64
    listed = client.get(f"/incidents/{incident_id}/approvals").json()
    assert [row["id"] for row in listed] == [pending["id"]]
    # The proposal decision is in the audit log before anything runs.
    assert [r.decision for r in audit.for_incident(incident_id)] == [
        "require_approval"
    ]


def test_an_approved_action_runs_once_on_the_server(approvals_setup):
    client, backend, _, audit = approvals_setup
    from sre_agent.models import Target

    incident_id = _diagnosed_incident(client)
    pending = _propose(client, incident_id).json()["pending"]
    approved = client.post(
        f"/incidents/{incident_id}/approvals/{pending['id']}/approve",
        json={"by": "Abraham"},
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["decision"] == "approved"
    assert body["approved_by"] == "Abraham"
    assert "Restarted checkout" in body["summary"]
    # A run is not a recovery check: mitigating, not resolved.
    assert body["incident_status"] == "mitigating"
    state = backend.get(Target(environment="demo", namespace="shop", service="checkout"))
    assert state is not None and state.restarts == 1 and not state.failing
    found = client.get(f"/incidents/{incident_id}").json()
    assert found["status"] == "mitigating"
    assert any(ev["kind"] == "action" for ev in found["evidence"])
    assert [r.decision for r in audit.for_incident(incident_id)] == [
        "require_approval",
        "allow",
    ]
    # Single-use: the same approval cannot be decided a second time.
    again = client.post(
        f"/incidents/{incident_id}/approvals/{pending['id']}/approve",
        json={"by": "Abraham"},
    )
    assert again.status_code == 409
    state = backend.get(Target(environment="demo", namespace="shop", service="checkout"))
    assert state is not None and state.restarts == 1
    listed = client.get(f"/incidents/{incident_id}/approvals").json()
    assert listed[0]["status"] == "approved"


def test_a_rejected_action_runs_nothing_and_cannot_be_approved(approvals_setup):
    client, backend, _, _ = approvals_setup
    from sre_agent.models import Target

    incident_id = _diagnosed_incident(client)
    pending = _propose(client, incident_id).json()["pending"]
    rejected = client.post(
        f"/incidents/{incident_id}/approvals/{pending['id']}/reject",
        json={"by": "Abraham"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["decision"] == "rejected"
    late = client.post(
        f"/incidents/{incident_id}/approvals/{pending['id']}/approve",
        json={"by": "Abraham"},
    )
    assert late.status_code == 409
    state = backend.get(Target(environment="demo", namespace="shop", service="checkout"))
    assert state is not None and state.restarts == 0 and state.failing


def test_a_proposal_cannot_be_retargeted_by_the_request_body(approvals_setup):
    client, _, _, _ = approvals_setup
    incident_id = _diagnosed_incident(client)
    response = _propose(
        client,
        incident_id,
        target={"environment": "demo", "namespace": "shop", "service": "search"},
    )
    assert response.status_code == 201
    assert response.json()["pending"]["target"]["service"] == "checkout"


def test_policy_is_rechecked_when_the_server_approval_is_given(approvals_setup):
    client, backend, policy, audit = approvals_setup
    from sre_agent.models import Target

    incident_id = _diagnosed_incident(client)
    pending = _propose(client, incident_id).json()["pending"]
    policy.halted = True
    refused = client.post(
        f"/incidents/{incident_id}/approvals/{pending['id']}/approve",
        json={"by": "Abraham"},
    )
    assert refused.status_code == 403
    assert "policy now denies" in refused.json()["detail"]
    assert [r.decision for r in audit.for_incident(incident_id)] == [
        "require_approval",
        "deny",
    ]
    state = backend.get(Target(environment="demo", namespace="shop", service="checkout"))
    assert state is not None and state.restarts == 0


def test_an_expired_approval_cannot_be_used_on_the_server(
    approvals_setup, monkeypatch: pytest.MonkeyPatch
):
    client, backend, _, _ = approvals_setup
    from datetime import UTC, datetime, timedelta

    from sre_agent.models import Target

    incident_id = _diagnosed_incident(client)
    pending = _propose(client, incident_id).json()["pending"]
    future = datetime.now(UTC) + timedelta(minutes=16)
    monkeypatch.setattr("sre_agent.approvals._utcnow", lambda: future)
    refused = client.post(
        f"/incidents/{incident_id}/approvals/{pending['id']}/approve",
        json={"by": "Abraham"},
    )
    assert refused.status_code == 409
    assert "expired" in refused.json()["detail"]
    state = backend.get(Target(environment="demo", namespace="shop", service="checkout"))
    assert state is not None and state.restarts == 0


def test_bad_proposals_are_refused_before_any_approval(approvals_setup):
    client, _, _, _ = approvals_setup
    incident_id = _diagnosed_incident(client)
    unknown = _propose(client, incident_id, runbook_id="nuke-it")
    assert unknown.status_code == 422
    bad_params = client.post(
        f"/incidents/{incident_id}/actions",
        json={"runbook_id": "scale-replicas", "parameters": {}},
    )
    assert bad_params.status_code == 422
    assert client.get(f"/incidents/{incident_id}/approvals").json() == []


def test_a_scale_approval_runs_with_its_parameters(approvals_setup):
    client, backend, _, _ = approvals_setup
    from sre_agent.models import Target

    incident_id = _diagnosed_incident(client)
    pending = client.post(
        f"/incidents/{incident_id}/actions",
        json={"runbook_id": "scale-replicas", "parameters": {"replicas": 4}},
    ).json()["pending"]
    assert pending["parameters"] == {"replicas": 4}
    approved = client.post(
        f"/incidents/{incident_id}/approvals/{pending['id']}/approve",
        json={"by": "Abraham"},
    )
    assert approved.status_code == 200
    assert "Scaled checkout from 2 to 4 replicas" in approved.json()["summary"]
    state = backend.get(Target(environment="demo", namespace="shop", service="checkout"))
    assert state is not None and state.replicas == 4


def test_approvals_need_a_diagnosed_incident_and_a_known_incident(approvals_setup):
    client, _, _, _ = approvals_setup
    missing = _propose(client, "does-not-exist")
    assert missing.status_code == 404
    assert client.get("/incidents/does-not-exist/approvals").status_code == 404
    # A server with approval wiring but no tool layer records only:
    # its incidents are still in triage, and no fix may be proposed.
    from sre_agent.policy import EnvironmentPolicy, Tier
    from sre_agent.runbooks import demo_runbooks

    _, backend = demo_runbooks()
    recording = TestClient(
        create_app(
            IncidentStore(),
            approval_policy=EnvironmentPolicy(
                environment="demo",
                granted_tier=Tier.ACT_WITH_APPROVAL,
                max_replicas_affected=5,
            ),
            runbook_backend=backend,
        )
    )
    body = recording.post("/webhooks/generic", json=GENERIC_ALERT).json()
    assert body["status"] == "triaging"
    refused = _propose(recording, str(body["incident_id"]))
    assert refused.status_code == 409
    assert "only a diagnosed incident" in refused.json()["detail"]


def test_a_server_without_approval_wiring_holds_no_approvals():
    plain = TestClient(create_app(IncidentStore(), tool_layer=demo_tool_layer()))
    incident_id = _diagnosed_incident(plain)
    assert _propose(plain, incident_id).status_code == 409
    assert plain.get(f"/incidents/{incident_id}/approvals").status_code == 409


def test_the_token_guard_covers_the_approval_routes(approvals_setup):
    from sre_agent.policy import EnvironmentPolicy, Tier
    from sre_agent.runbooks import demo_runbooks

    _, backend = demo_runbooks()
    authed = TestClient(
        create_app(
            IncidentStore(),
            auth_token="test-token",
            tool_layer=demo_tool_layer(),
            approval_policy=EnvironmentPolicy(
                environment="demo",
                granted_tier=Tier.ACT_WITH_APPROVAL,
                max_replicas_affected=5,
            ),
            runbook_backend=backend,
        )
    )
    headers = {TOKEN_HEADER: "test-token"}
    created = authed.post("/webhooks/generic", json=GENERIC_ALERT, headers=headers)
    incident_id = created.json()["incident_id"]
    assert _propose(authed, incident_id).status_code == 401
    ok = authed.post(
        f"/incidents/{incident_id}/actions",
        json={"runbook_id": "restart-workload"},
        headers=headers,
    )
    assert ok.status_code == 201


# --- Phase 7: selected read adapters feed the server's diagnosis ---


def test_server_diagnosis_includes_selected_adapter_evidence():
    from sre_agent.adapters import demo_adapter_sources

    adapter_client = TestClient(
        create_app(
            IncidentStore(),
            tool_layer=demo_tool_layer(),
            adapters=demo_adapter_sources(),
        )
    )
    body = adapter_client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    assert body["status"] == "diagnosed"
    found = adapter_client.get(f"/incidents/{body['incident_id']}").json()
    kinds = {ev["kind"] for ev in found["evidence"]}
    assert {"kubernetes", "azure-log"} <= kinds
    assert any("datadog:" in ev["query"] for ev in found["evidence"])
    evidence_ids = {ev["id"] for ev in found["evidence"]}
    for hyp in found["hypotheses"]:
        assert set(hyp["supporting_evidence"]) <= evidence_ids


def test_adapters_without_a_tool_layer_still_record_only():
    from sre_agent.adapters import demo_adapter_sources

    plain = TestClient(
        create_app(IncidentStore(), adapters=demo_adapter_sources())
    )
    body = plain.post("/webhooks/generic", json=GENERIC_ALERT).json()
    assert body["status"] == "triaging"
    assert body["diagnosis"] is None


def test_the_shipped_app_diagnoses_with_demo_adapter_evidence():
    from sre_agent.server import app as shipped_app

    shipped = TestClient(shipped_app)
    alert = {**GENERIC_ALERT, "fingerprint": "ship-adapters-1"}
    body = shipped.post("/webhooks/generic", json=alert).json()
    assert body["status"] == "diagnosed"
    found = shipped.get(f"/incidents/{body['incident_id']}").json()
    kinds = {ev["kind"] for ev in found["evidence"]}
    assert "kubernetes" in kinds


def test_a_failing_tool_layer_never_loses_the_alert():
    class BrokenTools:
        def get_service_status(self, target):
            raise RuntimeError("backend down")

    broken_client = TestClient(
        create_app(IncidentStore(), tool_layer=BrokenTools())  # type: ignore[arg-type]
    )
    response = broken_client.post("/webhooks/generic", json=GENERIC_ALERT)
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["status"] == "triaging"
    assert body["diagnosis"] is None
    assert len(broken_client.get("/incidents").json()) == 1


# --- Phase 7: incident memory advice is attached to live diagnoses ---


def test_server_diagnosis_carries_memory_advice():
    from sre_agent.memory import demo_incident_memory

    memory_client = TestClient(
        create_app(
            IncidentStore(),
            tool_layer=demo_tool_layer(),
            memory=demo_incident_memory(),
        )
    )
    body = memory_client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    assert body["status"] == "diagnosed"
    found = memory_client.get(f"/incidents/{body['incident_id']}").json()
    advice = found["memory_report"]
    assert advice is not None
    assert advice["past_incidents_held"] == 4
    assert advice["matches"]
    assert advice["matches"][0]["target"]["service"] == "checkout"
    assert [s["runbook_id"] for s in advice["suggestions"]] == [
        "restart-workload",
        "scale-replicas",
    ]
    listed = memory_client.get("/incidents").json()
    assert listed[0]["memory_matches"] == len(advice["matches"])
    assert listed[0]["memory_suggestions"] == 2
    # Advice alone proposes nothing: no action evidence was added.
    assert not any(ev["kind"] == "action" for ev in found["evidence"])

    from sre_agent.cli import format_incident_detail

    found["duplicate_count"] = 0
    text = format_incident_detail(found)
    assert "Memory advice (advice only" in text
    assert "restart-workload" in text


def test_memory_without_a_tool_layer_still_records_only():
    from sre_agent.memory import demo_incident_memory

    plain = TestClient(
        create_app(IncidentStore(), memory=demo_incident_memory())
    )
    body = plain.post("/webhooks/generic", json=GENERIC_ALERT).json()
    assert body["status"] == "triaging"
    assert body["diagnosis"] is None
    found = plain.get(f"/incidents/{body['incident_id']}").json()
    assert found["memory_report"] is None


def test_memory_advice_creates_no_pending_approval(approvals_setup):
    from sre_agent.memory import demo_incident_memory
    from sre_agent.policy import EnvironmentPolicy, Tier
    from sre_agent.runbooks import demo_runbooks

    _, backend = demo_runbooks()
    client = TestClient(
        create_app(
            IncidentStore(),
            tool_layer=demo_tool_layer(),
            memory=demo_incident_memory(),
            approval_policy=EnvironmentPolicy(
                environment="demo",
                granted_tier=Tier.ACT_WITH_APPROVAL,
                max_replicas_affected=5,
            ),
            runbook_backend=backend,
        )
    )
    incident_id = _diagnosed_incident(client)
    found = client.get(f"/incidents/{incident_id}").json()
    assert found["memory_report"] is not None
    # A suggestion is not a proposal: nothing waits for approval.
    assert client.get(f"/incidents/{incident_id}/approvals").json() == []


def test_the_shipped_app_carries_demo_memory_advice():
    from sre_agent.server import app as shipped_app

    shipped = TestClient(shipped_app)
    alert = {**GENERIC_ALERT, "fingerprint": "ship-memory-1"}
    body = shipped.post("/webhooks/generic", json=alert).json()
    assert body["status"] == "diagnosed"
    found = shipped.get(f"/incidents/{body['incident_id']}").json()
    assert found["memory_report"] is not None
    assert found["memory_report"]["past_incidents_held"] == 4
