"""The audit log: every decision recorded, no secrets, append-only."""

from sre_agent.audit import AuditLog, evaluate_and_audit, parameters_hash, read_audit_file
from sre_agent.models import Target
from sre_agent.policy import Decision, EnvironmentPolicy, ProposedAction, Tier, evaluate

TARGET = Target(environment="demo", namespace="shop", service="checkout")


def _action(**overrides) -> ProposedAction:
    base: dict = {
        "runbook_id": "restart-workload",
        "target": TARGET,
        "parameters": {"deployment": "checkout"},
        "replicas_affected": 1,
        "rationale": "pods are crash-looping after the last deploy",
    }
    base.update(overrides)
    return ProposedAction(**base)


def _policy(**overrides) -> EnvironmentPolicy:
    base: dict = {"environment": "demo", "granted_tier": Tier.ACT_WITH_APPROVAL}
    base.update(overrides)
    return EnvironmentPolicy(**base)


def test_every_decision_is_recorded_including_denials():
    log = AuditLog()
    allowed = evaluate_and_audit(
        _action(runbook_id="query-metrics", replicas_affected=0),
        _policy(granted_tier=Tier.OBSERVE),
        log,
        incident_id="inc-1",
    )
    denied = evaluate_and_audit(_action(), _policy(granted_tier=Tier.OBSERVE), log, incident_id="inc-1")
    needs = evaluate_and_audit(_action(), _policy(), log, incident_id="inc-1")
    assert (allowed.decision, denied.decision, needs.decision) == (
        Decision.ALLOW,
        Decision.DENY,
        Decision.REQUIRE_APPROVAL,
    )
    records = log.records
    assert [r.decision for r in records] == ["allow", "deny", "require_approval"]
    assert all(r.incident_id == "inc-1" for r in records)
    assert all(r.reason for r in records)


def test_record_matches_a_plain_evaluate_call():
    action = _action()
    result = evaluate(action, _policy())
    log = AuditLog()
    record = log.record_decision(incident_id="inc-9", action=action, result=result)
    assert record.decision == result.decision.value
    assert record.reason == result.reason
    assert "restart-workload" in record.action_summary
    assert "checkout/shop" in record.action_summary


def test_parameter_values_never_appear_in_the_record():
    action = _action(parameters={"api_token": "s3cret-value-123", "deployment": "checkout"})
    log = AuditLog()
    record = log.record_decision(
        incident_id="inc-1", action=action, result=evaluate(action, _policy())
    )
    blob = record.model_dump_json()
    assert "s3cret-value-123" not in blob
    assert record.parameters_hash == parameters_hash(action)
    assert len(record.parameters_hash) == 64


def test_parameters_hash_changes_with_the_parameters():
    assert parameters_hash(_action()) != parameters_hash(
        _action(parameters={"deployment": "other"})
    )
    # key order does not change the fingerprint
    a = _action(parameters={"a": 1, "b": 2})
    b = _action(parameters={"b": 2, "a": 1})
    assert parameters_hash(a) == parameters_hash(b)


def test_records_are_append_only_from_the_outside():
    log = AuditLog()
    evaluate_and_audit(_action(), _policy(), log, incident_id="inc-1")
    handed_out = log.records
    handed_out.clear()
    handed_out.append("junk")  # type: ignore[arg-type]
    assert len(log.records) == 1
    log.records[0].decision = "tampered"
    assert log.records[0].decision == "require_approval"


def test_for_incident_filters_records():
    log = AuditLog()
    evaluate_and_audit(_action(), _policy(), log, incident_id="inc-1")
    evaluate_and_audit(_action(), _policy(), log, incident_id="inc-2")
    assert len(log.for_incident("inc-1")) == 1
    assert log.for_incident("inc-2")[0].incident_id == "inc-2"
    assert log.for_incident("inc-nope") == []


def test_file_log_appends_json_lines_and_reads_back(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    evaluate_and_audit(_action(), _policy(), log, incident_id="inc-1")
    evaluate_and_audit(
        _action(runbook_id="made-up-runbook"), _policy(), log, incident_id="inc-1"
    )
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    back = read_audit_file(path)
    assert [r.decision for r in back] == ["require_approval", "deny"]
    assert back[0].parameters_hash == log.records[0].parameters_hash


def test_approved_by_is_recorded_when_given():
    log = AuditLog()
    evaluate_and_audit(_action(), _policy(), log, incident_id="inc-1", approved_by="abraham")
    assert log.records[0].approved_by == "abraham"
