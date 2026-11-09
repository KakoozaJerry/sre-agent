"""Tests for the Tier 2 approval flow, including the CLI end to end.

The safety-shaped tests matter most here: a denied action can never
be parked for approval, an approval must still hold when policy is
checked again at approval time, a grant is spent by one use even
when the run fails, and a grant for one action runs no other.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sre_agent.approvals import (
    APPROVAL_TTL,
    ApprovalError,
    approve_pending,
    create_pending,
    execute_approved,
)
from sre_agent.audit import AuditLog, evaluate_and_audit, parameters_hash
from sre_agent.cli import main
from sre_agent.models import Target
from sre_agent.policy import (
    Decision,
    EnvironmentPolicy,
    PolicyResult,
    Tier,
)
from sre_agent.runbooks import RunbookError, build_action, demo_runbooks

CHECKOUT = Target(environment="demo", namespace="shop", service="checkout")
SEARCH = Target(environment="demo", namespace="shop", service="search")
TIER2 = EnvironmentPolicy(
    environment="demo", granted_tier=Tier.ACT_WITH_APPROVAL, max_replicas_affected=5
)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _pending(runbook_id="restart-workload", target=CHECKOUT, parameters=None, audit=None):
    """A real Tier 2 proposal: built, policy-checked, parked as pending."""
    executor, backend = demo_runbooks()
    action = build_action(
        runbook_id, target, parameters or {}, backend, rationale="checkout is failing"
    )
    log = audit if audit is not None else AuditLog()
    result = evaluate_and_audit(action, TIER2, log, incident_id="inc-1",
                                incident_target=target)
    assert result.decision is Decision.REQUIRE_APPROVAL
    pending = create_pending(action, result, incident_id="inc-1", now=NOW)
    return pending, executor, backend, log


# --- creating a pending action ---


def test_only_require_approval_can_become_pending() -> None:
    _, backend = demo_runbooks()
    action = build_action("restart-workload", CHECKOUT, {}, backend, rationale="test")
    for decision in (Decision.ALLOW, Decision.DENY):
        result = PolicyResult(decision=decision, reason="test")
        with pytest.raises(ApprovalError, match="not 'require_approval'"):
            create_pending(action, result, incident_id="inc-1", now=NOW)


def test_pending_carries_the_exact_action_and_a_15_minute_window() -> None:
    pending, _, _, _ = _pending()
    assert pending.expires_at - pending.created_at == APPROVAL_TTL == timedelta(minutes=15)
    assert pending.parameters_hash == parameters_hash(pending.action)
    assert not pending.is_expired(NOW + timedelta(minutes=14))
    assert pending.is_expired(NOW + timedelta(minutes=16))


# --- approving ---


def test_approval_must_name_a_person() -> None:
    pending, _, _, log = _pending()
    with pytest.raises(ApprovalError, match="must name who"):
        approve_pending(pending, approved_by="  ", policy=TIER2, audit_log=log, now=NOW)


def test_an_expired_proposal_cannot_be_approved() -> None:
    pending, _, backend, log = _pending()
    with pytest.raises(ApprovalError, match="expired"):
        approve_pending(pending, approved_by="Abraham", policy=TIER2, audit_log=log,
                        now=NOW + timedelta(minutes=16))
    state = backend.get(CHECKOUT)
    assert state is not None and state.failing and state.restarts == 0


def test_policy_is_checked_again_at_approval_time() -> None:
    """Halt the environment after proposal: the human's yes must not stand."""
    pending, _, backend, log = _pending()
    halted = TIER2.model_copy(update={"halted": True})
    with pytest.raises(ApprovalError, match="policy now denies"):
        approve_pending(pending, approved_by="Abraham", policy=halted, audit_log=log,
                        incident_target=CHECKOUT, now=NOW)
    # The re-check's denial is in the audit log alongside the first decision.
    decisions = [r.decision for r in log.for_incident("inc-1")]
    assert decisions == ["require_approval", "deny"]
    state = backend.get(CHECKOUT)
    assert state is not None and state.restarts == 0


def test_approval_is_pointless_when_tier3_already_allows() -> None:
    pending, _, _, log = _pending()
    tier3 = EnvironmentPolicy(
        environment="demo", granted_tier=Tier.ACT_WITHIN_BOUNDS,
        tier3_runbooks={"restart-workload"}, max_replicas_affected=5,
    )
    with pytest.raises(ApprovalError, match="no longer needs approval"):
        approve_pending(pending, approved_by="Abraham", policy=tier3, audit_log=log,
                        now=NOW)


def test_happy_path_records_the_approver_in_the_audit_log() -> None:
    pending, _, _, log = _pending()
    grant, allowed = approve_pending(pending, approved_by="Abraham", policy=TIER2,
                                     audit_log=log, now=NOW)
    assert allowed.decision is Decision.ALLOW
    assert grant.approved_by == "Abraham" and not grant.used
    assert grant.runbook_id == "restart-workload" and grant.target == CHECKOUT
    records = log.for_incident("inc-1")
    assert [r.decision for r in records] == ["require_approval", "allow"]
    assert records[-1].approved_by == "Abraham"


# --- running with a grant ---


def test_approved_restart_runs_end_to_end() -> None:
    pending, executor, backend, log = _pending()
    grant, allowed = approve_pending(pending, approved_by="Abraham", policy=TIER2,
                                     audit_log=log, now=NOW)
    result = execute_approved(executor, pending, grant, allowed, now=NOW)
    assert result.after["failing"] is False
    assert grant.used
    state = backend.get(CHECKOUT)
    assert state is not None and state.restarts == 1 and not state.failing


def test_a_grant_runs_once_and_only_once() -> None:
    pending, executor, backend, log = _pending()
    grant, allowed = approve_pending(pending, approved_by="Abraham", policy=TIER2,
                                     audit_log=log, now=NOW)
    execute_approved(executor, pending, grant, allowed, now=NOW)
    with pytest.raises(ApprovalError, match="already been used"):
        execute_approved(executor, pending, grant, allowed, now=NOW)
    state = backend.get(CHECKOUT)
    assert state is not None and state.restarts == 1


def test_a_grant_for_one_action_runs_no_other() -> None:
    pending, executor, backend, log = _pending()
    other, _, _, _ = _pending("scale-replicas", parameters={"replicas": 4})
    grant, allowed = approve_pending(pending, approved_by="Abraham", policy=TIER2,
                                     audit_log=log, now=NOW)
    with pytest.raises(ApprovalError, match="does not match"):
        execute_approved(executor, other, grant, allowed, now=NOW)
    state = backend.get(CHECKOUT)
    assert state is not None and state.replicas == 2 and state.restarts == 0


def test_a_failed_run_still_spends_the_approval() -> None:
    """Rollback on search has no earlier version: the run fails, and
    the approval is spent anyway — no silent replay."""
    pending, executor, backend, log = _pending("rollback-deployment", target=SEARCH)
    grant, allowed = approve_pending(pending, approved_by="Abraham", policy=TIER2,
                                     audit_log=log, now=NOW)
    with pytest.raises(RunbookError, match="no previous version"):
        execute_approved(executor, pending, grant, allowed, now=NOW)
    assert grant.used
    with pytest.raises(ApprovalError, match="already been used"):
        execute_approved(executor, pending, grant, allowed, now=NOW)
    state = backend.get(SEARCH)
    assert state is not None and state.version == "v1"


def test_execute_still_needs_an_allow_decision() -> None:
    pending, executor, _, log = _pending()
    grant, _ = approve_pending(pending, approved_by="Abraham", policy=TIER2,
                               audit_log=log, now=NOW)
    waiting = PolicyResult(decision=Decision.REQUIRE_APPROVAL, reason="test")
    with pytest.raises(ApprovalError, match="not 'allow'"):
        execute_approved(executor, pending, grant, waiting, now=NOW)
    assert not grant.used


# --- the CLI: Tier 2 end to end ---


def test_cli_approve_demo_with_yes_runs_the_restart(capsys) -> None:
    code = main(["approve-demo", "--by", "Abraham", "--yes"])
    assert code == 0
    out = capsys.readouterr().out
    assert "require_approval" in out
    assert "Approved by Abraham. Ran: Restarted checkout" in out
    assert "resolved" in out and "restarts=1" in out


def test_cli_approve_demo_declined_runs_nothing(capsys) -> None:
    code = main(["approve-demo", "--by", "Abraham"], confirm_approval=lambda q: False)
    assert code == 0
    out = capsys.readouterr().out
    assert "Not approved. Nothing was run." in out
    assert "Ran:" not in out


def test_cli_approve_demo_prompt_yes_runs_it(capsys) -> None:
    code = main(["approve-demo", "--by", "Abraham"], confirm_approval=lambda q: True)
    assert code == 0
    assert "Ran: Restarted checkout" in capsys.readouterr().out


def test_cli_approve_demo_needs_a_name(capsys) -> None:
    assert main(["approve-demo", "--yes"]) == 1
    assert "--by NAME" in capsys.readouterr().err


def test_cli_approve_demo_scale_needs_a_count(capsys) -> None:
    assert main(["approve-demo", "--by", "Abraham", "--runbook", "scale-replicas",
                 "--yes"]) == 1
    assert "--replicas N" in capsys.readouterr().err
    code = main(["approve-demo", "--by", "Abraham", "--runbook", "scale-replicas",
                 "--replicas", "4", "--yes"])
    assert code == 0
    assert "Scaled checkout from 2 to 4 replicas" in capsys.readouterr().out


def test_cli_approve_demo_unknown_runbook_is_a_plain_error(capsys) -> None:
    assert main(["approve-demo", "--by", "Abraham", "--runbook", "nuke-it",
                 "--yes"]) == 1
    assert "not in the runbook library" in capsys.readouterr().err
