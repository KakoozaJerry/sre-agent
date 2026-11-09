"""Tests for Tier 3 in the demo: the pre-approved grant, the
runner that enforces it, and the kill switch drills.

Tier 3 is bounded autonomy, so these tests pin both directions:
the fixes inside the grant do run on their own, and everything
outside it — a fix type that was not granted, a fix that is too
big, a fix aimed outside the granted namespace, and any fix at
all after the halt — does not.
"""

from __future__ import annotations

from sre_agent.audit import AuditLog
from sre_agent.cli import main
from sre_agent.models import Target
from sre_agent.policy import Decision, Tier
from sre_agent.runbooks import build_action, demo_runbooks
from sre_agent.tier3 import (
    CHECKOUT,
    demo_tier3_policy,
    execute_tier3,
    run_tier3_drills,
)

EXPECTED_IDS = [
    "tier3-restart-runs",
    "tier3-scale-runs",
    "tier3-rollback-needs-approval",
    "tier3-blast-radius-denied",
    "tier3-outside-namespace-denied",
    "kill-switch-stops-mutations",
    "kill-switch-reads-continue",
]


def test_demo_policy_is_the_named_grant() -> None:
    policy = demo_tier3_policy()
    assert policy.environment == "demo"
    assert policy.granted_tier is Tier.ACT_WITHIN_BOUNDS
    assert policy.tier3_runbooks == {"restart-workload", "scale-replicas"}
    assert "rollback-deployment" not in policy.tier3_runbooks
    assert policy.max_replicas_affected == 3
    assert policy.allowed_namespaces == {"shop"}
    assert policy.halted is False


def test_all_drills_pass_with_current_code() -> None:
    report = run_tier3_drills()
    assert report.total == 7
    assert [r.drill_id for r in report.results] == EXPECTED_IDS
    assert report.passed_count == 7
    for result in report.results:
        assert result.passed, (result.drill_id, result.failed_checks)


def test_drills_run_only_the_preapproved_fixes() -> None:
    # Two Tier 3 fixes (restart, scale) plus the first fix of the
    # kill switch drill. Nothing else in the set may run a fix.
    report = run_tier3_drills()
    assert report.actions_run == 3


def test_runner_runs_a_preapproved_restart_and_audits_it() -> None:
    executor, backend = demo_runbooks()
    audit = AuditLog()
    action = build_action(
        "restart-workload", CHECKOUT, {}, backend, rationale="test"
    )
    result, run = execute_tier3(
        executor, demo_tier3_policy(), audit, action,
        incident_id="inc-1", incident_target=CHECKOUT,
    )
    assert result.decision is Decision.ALLOW
    assert run is not None
    state = backend.get(CHECKOUT)
    assert state is not None and state.restarts == 1 and state.failing is False
    records = audit.for_incident("inc-1")
    assert len(records) == 1 and records[0].decision == "allow"


def test_runner_does_not_run_what_still_needs_approval() -> None:
    executor, backend = demo_runbooks()
    audit = AuditLog()
    action = build_action(
        "rollback-deployment", CHECKOUT, {}, backend, rationale="test"
    )
    result, run = execute_tier3(
        executor, demo_tier3_policy(), audit, action,
        incident_id="inc-2", incident_target=CHECKOUT,
    )
    assert result.decision is Decision.REQUIRE_APPROVAL
    assert run is None
    state = backend.get(CHECKOUT)
    assert state is not None and state.version == "v2"


def test_halt_pressed_between_runs_stops_the_second_run() -> None:
    executor, backend = demo_runbooks()
    audit = AuditLog()
    first = build_action(
        "restart-workload", CHECKOUT, {}, backend, rationale="test"
    )
    _, first_run = execute_tier3(
        executor, demo_tier3_policy(), audit, first,
        incident_id="inc-3", incident_target=CHECKOUT,
    )
    assert first_run is not None
    second = build_action(
        "restart-workload", CHECKOUT, {}, backend, rationale="test"
    )
    result, second_run = execute_tier3(
        executor, demo_tier3_policy(halted=True), audit, second,
        incident_id="inc-3", incident_target=CHECKOUT,
    )
    assert result.decision is Decision.DENY
    assert second_run is None
    state = backend.get(CHECKOUT)
    assert state is not None and state.restarts == 1
    assert len(audit.for_incident("inc-3")) == 2


def test_retargeted_action_is_denied_on_the_tier3_path() -> None:
    executor, backend = demo_runbooks()
    audit = AuditLog()
    action = build_action(
        "restart-workload", CHECKOUT, {}, backend, rationale="test"
    )
    other = Target(environment="demo", namespace="shop", service="payments")
    result, run = execute_tier3(
        executor, demo_tier3_policy(), audit, action,
        incident_id="inc-4", incident_target=other,
    )
    assert result.decision is Decision.DENY
    assert run is None


def test_drills_are_deterministic() -> None:
    first = run_tier3_drills()
    second = run_tier3_drills()
    assert [(r.drill_id, r.passed) for r in first.results] == [
        (r.drill_id, r.passed) for r in second.results
    ]


def test_rendered_report_states_the_grant_and_the_halt() -> None:
    text = run_tier3_drills().render_text()
    assert "Passed: 7" in text
    assert "restart-workload and scale-replicas" in text
    assert "rollback-deployment is not pre-approved" in text
    assert "Kill switch:" in text


def test_cli_tier3_check_prints_report_and_exits_zero(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["tier3-check"]) == 0
    out = capsys.readouterr().out
    assert "Passed: 7" in out
    assert "kill-switch-stops-mutations" in out


def test_cli_tier3_check_rejects_extra_arguments(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["tier3-check", "extra"]) == 1
    assert "no extra arguments" in capsys.readouterr().err
