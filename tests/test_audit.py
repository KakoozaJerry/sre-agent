"""The audit log: every decision recorded, no secrets, append-only."""

from sre_agent.audit import (
    GENESIS_HASH,
    AuditLog,
    evaluate_and_audit,
    parameters_hash,
    read_audit_file,
    verify_audit_file,
    verify_audit_records,
)
from sre_agent.models import AuditRecord, Target
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


# --- The hash chain (finding F-04): edits to the file must show ---


def _chained_log(tmp_path=None, count=3):  # type: ignore[no-untyped-def]
    path = (tmp_path / "audit.jsonl") if tmp_path is not None else None
    log = AuditLog(path)
    for _ in range(count):
        evaluate_and_audit(_action(), _policy(), log, incident_id="inc-1")
    return log


def test_records_are_hash_chained_in_order():
    log = _chained_log()
    records = log.records
    assert records[0].prev_hash == GENESIS_HASH
    assert all(len(r.record_hash) == 64 for r in records)
    assert records[1].prev_hash == records[0].record_hash
    assert records[2].prev_hash == records[1].record_hash
    assert verify_audit_records(records) == []


def test_an_edited_record_breaks_the_chain():
    records = _chained_log().records
    records[1].decision = "allow"  # was require_approval: history rewritten
    problems = verify_audit_records(records)
    assert len(problems) == 1
    assert "record 2" in problems[0] and "edited" in problems[0]


def test_a_deleted_middle_record_breaks_the_chain():
    records = _chained_log().records
    del records[1]
    problems = verify_audit_records(records)
    assert len(problems) == 1
    assert "record 2" in problems[0] and "deleted" in problems[0]


def test_reordered_records_break_the_chain():
    records = _chained_log().records
    records[1], records[2] = records[2], records[1]
    assert verify_audit_records(records) != []


def test_unchained_legacy_record_is_reported_not_passed():
    legacy = AuditRecord(
        incident_id="inc-old",
        action_summary="restart-workload on checkout/shop (demo)",
        decision="allow",
        reason="written before the chain existed",
    )
    problems = verify_audit_records([legacy])
    assert len(problems) == 1 and "unchained" in problems[0]


def test_file_chain_verifies_and_detects_an_edit_on_disk(tmp_path):
    path = tmp_path / "audit.jsonl"
    _chained_log(tmp_path)
    assert verify_audit_file(path) == []
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("require_approval", "allow")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    problems = verify_audit_file(path)
    assert problems and "record 1" in problems[0]


def test_a_second_log_continues_an_existing_files_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    _chained_log(tmp_path, count=2)
    second = AuditLog(path)
    evaluate_and_audit(_action(), _policy(), second, incident_id="inc-1")
    back = read_audit_file(path)
    assert len(back) == 3
    assert back[2].prev_hash == back[1].record_hash
    assert verify_audit_file(path) == []


def test_the_chain_hash_covers_the_approver_and_reason():
    log = AuditLog()
    evaluate_and_audit(_action(), _policy(), log, incident_id="inc-1", approved_by="abraham")
    record = log.records[0]
    record.approved_by = "someone-else"
    assert verify_audit_records([record]) != []
