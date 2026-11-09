"""Tier 3 for the demo: pre-approved fixes, and kill switch drills.

In plain words, Tier 3 is bounded autonomy. At Tier 2 a human
approves every fix one by one. At Tier 3 an environment is given,
in advance and in writing, a small set of fix types it may run on
its own — and only inside strict limits. Everything outside that
set still needs a human, and everything outside the limits is
refused outright.

This module is that grant for the one environment where it has
been earned so far: the demo. It has three parts:

- **The demo Tier 3 policy** (``demo_tier3_policy``). In the demo
  environment, in the ``shop`` namespace only, the agent may run
  two fix types on its own: restart a workload, and change how
  many copies of it run (scale). Rolling back a deploy is *not*
  pre-approved: a rollback changes which version is running, so
  it still needs a human's yes, every time. One action may touch
  at most three copies at once. This is the plan's rule in code:
  grants widen one environment and one fix type at a time.

- **The Tier 3 runner** (``execute_tier3``). The one path a
  pre-approved fix runs through. It asks the policy engine,
  writes the decision to the audit log *before* anything runs,
  and runs the fix only on an ALLOW. A decision that still needs
  approval does not run here — needing a human means a human is
  asked, through the Tier 2 approval flow, not skipped. A denied
  action does not run either. A fix that runs leaves its result
  available to be stored on the incident as evidence, like any
  other run.

- **Kill switch drills** (``run_tier3_drills``). A drill is a
  rehearsed check that the stop switch works, run the way a fire
  drill is run: on purpose, in the demo, before it is needed for
  real. The drills cover both halves of this module. Tier 3
  drills prove the pre-approved fixes run, the fix that is not
  pre-approved does not run on its own, and an action that is
  too big or aimed outside the allowed namespace is refused.
  Kill switch drills prove the halt: a fix runs, the environment
  is halted, the same fix is then refused and nothing changes —
  while read-only checks, which change nothing, may continue.

One design lesson from this pass, now part of the design: the
halt is checked at run time, inside the same policy call the
runner makes for every run, not remembered from when a fix was
first proposed. A halt pressed after a proposal but before a
run therefore stops that run — the same rule the approval flow
already follows when it re-checks policy at approval time. The
kill switch drill presses the halt *between* two runs to prove
exactly that, rather than only checking a halt that was set
before anything started.

Scope, stated honestly: Tier 3 here is the demo environment
only, against the in-memory demo workloads, like the rest of
the runbook work. No real system is touched, and no other
environment has a Tier 3 grant. The drills run with no server
and no Docker, so they run in pytest, in CI, and as
``sre-agent tier3-check``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .audit import AuditLog, evaluate_and_audit
from .logging_config import get_logger
from .models import Target
from .policy import Decision, EnvironmentPolicy, PolicyResult, ProposedAction, Tier, evaluate
from .runbooks import (
    DemoWorkloadBackend,
    RunbookExecutor,
    RunbookResult,
    WorkloadState,
    build_action,
    demo_runbooks,
)

log = get_logger(__name__)

CHECKOUT = Target(environment="demo", namespace="shop", service="checkout")

# The demo Tier 3 grant, named in one place so the policy, the
# drills, and the docs cannot quietly disagree about it.
TIER3_RUNBOOKS: set[str] = {"restart-workload", "scale-replicas"}
TIER3_MAX_REPLICAS_AFFECTED = 3
TIER3_ALLOWED_NAMESPACES: set[str] = {"shop"}


def demo_tier3_policy(*, halted: bool = False) -> EnvironmentPolicy:
    """The Tier 3 policy for the demo environment.

    Restart and scale are pre-approved, inside the blast-radius
    limit and the ``shop`` namespace only. Rollback is not in
    the pre-approved set, so it is sent for a human's approval
    like any other Tier 2 action. ``halted`` is how the kill
    switch is pressed in a drill: the same policy, stopped.
    """
    return EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITHIN_BOUNDS,
        tier3_runbooks=set(TIER3_RUNBOOKS),
        max_replicas_affected=TIER3_MAX_REPLICAS_AFFECTED,
        allowed_namespaces=set(TIER3_ALLOWED_NAMESPACES),
        halted=halted,
    )


def execute_tier3(
    executor: RunbookExecutor,
    policy: EnvironmentPolicy,
    audit_log: AuditLog,
    action: ProposedAction,
    *,
    incident_id: str,
    incident_target: Target | None = None,
) -> tuple[PolicyResult, RunbookResult | None]:
    """Run one fix through the Tier 3 path, or not at all.

    The policy engine decides, the decision is written to the
    audit log, and the fix runs only if the decision is ALLOW.
    Any other decision — needs approval, or denied — runs
    nothing, and the decision is returned so the caller can
    route it honestly (for approval, through the Tier 2 flow).
    """
    result = evaluate_and_audit(
        action, policy, audit_log,
        incident_id=incident_id, incident_target=incident_target,
    )
    if result.decision is not Decision.ALLOW:
        return result, None
    return result, executor.execute(action, result)


class DrillResult(BaseModel):
    """The scored result of one drill."""

    drill_id: str
    title: str
    kind: str  # "tier3" | "kill-switch"
    passed: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    failed_checks: list[str] = Field(default_factory=list)
    actions_run: int = 0
    summary: str = ""


class Tier3Report(BaseModel):
    """The scored result of the whole drill set."""

    results: list[DrillResult] = Field(default_factory=list)
    total: int = 0
    passed_count: int = 0
    actions_run: int = 0

    def render_text(self) -> str:
        """The drill scores as plain text."""
        lines = [
            "Tier 3 drills (demo environment, seeded demo workloads)",
            (
                f"Drills: {self.total}  Passed: {self.passed_count}  "
                f"Fixes run across the drills: {self.actions_run} "
                "(each one pre-approved, inside its limits, and audited)"
            ),
            (
                "Demo Tier 3 grant: restart-workload and scale-replicas, "
                "shop namespace only, at most "
                f"{TIER3_MAX_REPLICAS_AFFECTED} replicas touched per action. "
                "rollback-deployment is not pre-approved and still needs a human."
            ),
        ]
        for result in self.results:
            mark = "PASS" if result.passed else "FAIL"
            lines.append(f"- [{mark}] {result.drill_id} ({result.kind}): {result.title}")
            for name in result.failed_checks:
                lines.append(f"  FAILED check: {name}")
        lines.append(
            "Kill switch: halting the demo environment refuses every "
            "further fix and changes nothing, while read-only checks "
            "may continue. The halt is checked at run time, on every run."
        )
        return "\n".join(lines)


def _finish(
    drill_id: str,
    title: str,
    kind: str,
    checks: dict[str, bool],
    *,
    actions_run: int,
    summary: str,
) -> DrillResult:
    failed = [name for name, ok in checks.items() if not ok]
    return DrillResult(
        drill_id=drill_id,
        title=title,
        kind=kind,
        passed=not failed,
        checks=checks,
        failed_checks=failed,
        actions_run=actions_run,
        summary=summary,
    )


def _drill_restart_runs() -> DrillResult:
    executor, backend = demo_runbooks()
    audit = AuditLog()
    action = build_action(
        "restart-workload", CHECKOUT, {}, backend,
        rationale="drill: checkout is failing",
    )
    result, run = execute_tier3(
        executor, demo_tier3_policy(), audit, action,
        incident_id="drill-restart", incident_target=CHECKOUT,
    )
    state = backend.get(CHECKOUT)
    assert state is not None
    checks = {
        "policy_allowed_without_approval": result.decision is Decision.ALLOW,
        "fix_ran": run is not None,
        "failure_cleared": state.failing is False and state.restarts == 1,
        "decision_audited_before_the_run": len(audit.for_incident("drill-restart")) == 1,
    }
    return _finish(
        "tier3-restart-runs", "A pre-approved restart runs on its own",
        "tier3", checks, actions_run=1 if run else 0,
        summary=result.reason,
    )


def _drill_scale_runs() -> DrillResult:
    executor, backend = demo_runbooks()
    audit = AuditLog()
    action = build_action(
        "scale-replicas", CHECKOUT, {"replicas": 3}, backend,
        rationale="drill: one more copy is needed",
    )
    result, run = execute_tier3(
        executor, demo_tier3_policy(), audit, action,
        incident_id="drill-scale", incident_target=CHECKOUT,
    )
    state = backend.get(CHECKOUT)
    assert state is not None
    checks = {
        "policy_allowed_without_approval": result.decision is Decision.ALLOW,
        "fix_ran": run is not None,
        "copies_now_three": state.replicas == 3,
        "decision_audited_before_the_run": len(audit.for_incident("drill-scale")) == 1,
    }
    return _finish(
        "tier3-scale-runs", "A pre-approved scale runs on its own",
        "tier3", checks, actions_run=1 if run else 0,
        summary=result.reason,
    )


def _drill_rollback_needs_approval() -> DrillResult:
    executor, backend = demo_runbooks()
    audit = AuditLog()
    action = build_action(
        "rollback-deployment", CHECKOUT, {}, backend,
        rationale="drill: the new version looks bad",
    )
    result, run = execute_tier3(
        executor, demo_tier3_policy(), audit, action,
        incident_id="drill-rollback", incident_target=CHECKOUT,
    )
    state = backend.get(CHECKOUT)
    assert state is not None
    checks = {
        "policy_sends_it_for_approval": result.decision is Decision.REQUIRE_APPROVAL,
        "nothing_ran_on_its_own": run is None,
        "version_unchanged": state.version == "v2",
        "decision_audited": len(audit.for_incident("drill-rollback")) == 1,
    }
    return _finish(
        "tier3-rollback-needs-approval",
        "Rollback is not pre-approved: it still needs a human",
        "tier3", checks, actions_run=0,
        summary=result.reason,
    )


def _drill_blast_radius_denied() -> DrillResult:
    big = Target(environment="demo", namespace="shop", service="gateway")
    backend = DemoWorkloadBackend(
        [WorkloadState(target=big, replicas=5, version="v1", failing=True)]
    )
    executor = RunbookExecutor(backend)
    audit = AuditLog()
    action = build_action(
        "restart-workload", big, {}, backend,
        rationale="drill: gateway is failing",
    )
    result, run = execute_tier3(
        executor, demo_tier3_policy(), audit, action,
        incident_id="drill-blast", incident_target=big,
    )
    state = backend.get(big)
    assert state is not None
    checks = {
        "action_touches_five_copies": action.replicas_affected == 5,
        "policy_denied_it": result.decision is Decision.DENY,
        "nothing_ran": run is None and state.restarts == 0 and state.failing is True,
        "denial_audited": len(audit.for_incident("drill-blast")) == 1,
    }
    return _finish(
        "tier3-blast-radius-denied",
        "A fix that touches too many copies is refused",
        "tier3", checks, actions_run=0,
        summary=result.reason,
    )


def _drill_outside_namespace_denied() -> DrillResult:
    outside = Target(environment="demo", namespace="billing", service="checkout")
    backend = DemoWorkloadBackend(
        [WorkloadState(target=outside, replicas=1, version="v1", failing=True)]
    )
    executor = RunbookExecutor(backend)
    audit = AuditLog()
    action = build_action(
        "restart-workload", outside, {}, backend,
        rationale="drill: a workload outside the granted namespace",
    )
    result, run = execute_tier3(
        executor, demo_tier3_policy(), audit, action,
        incident_id="drill-namespace", incident_target=outside,
    )
    state = backend.get(outside)
    assert state is not None
    checks = {
        "policy_denied_it": result.decision is Decision.DENY,
        "nothing_ran": run is None and state.restarts == 0,
        "denial_audited": len(audit.for_incident("drill-namespace")) == 1,
    }
    return _finish(
        "tier3-outside-namespace-denied",
        "A fix outside the granted namespace is refused",
        "tier3", checks, actions_run=0,
        summary=result.reason,
    )


def _drill_kill_switch_stops_mutations() -> DrillResult:
    executor, backend = demo_runbooks()
    audit = AuditLog()
    policy = demo_tier3_policy()
    first = build_action(
        "restart-workload", CHECKOUT, {}, backend,
        rationale="drill: first fix, before the halt",
    )
    first_result, first_run = execute_tier3(
        executor, policy, audit, first,
        incident_id="drill-halt", incident_target=CHECKOUT,
    )
    # The kill switch is pressed here, between the two runs.
    halted = demo_tier3_policy(halted=True)
    second = build_action(
        "restart-workload", CHECKOUT, {}, backend,
        rationale="drill: second fix, after the halt",
    )
    second_result, second_run = execute_tier3(
        executor, halted, audit, second,
        incident_id="drill-halt", incident_target=CHECKOUT,
    )
    state = backend.get(CHECKOUT)
    assert state is not None
    checks = {
        "first_fix_ran": first_result.decision is Decision.ALLOW and first_run is not None,
        "halt_denies_the_second_fix": second_result.decision is Decision.DENY,
        "nothing_ran_after_the_halt": second_run is None and state.restarts == 1,
        "both_decisions_audited": len(audit.for_incident("drill-halt")) == 2,
    }
    return _finish(
        "kill-switch-stops-mutations",
        "The halt, pressed between two runs, stops the second one",
        "kill-switch", checks, actions_run=1 if first_run else 0,
        summary=second_result.reason,
    )


def _drill_kill_switch_reads_continue() -> DrillResult:
    halted = demo_tier3_policy(halted=True)
    read = ProposedAction(
        runbook_id="query-metrics",
        target=CHECKOUT,
        parameters={},
        replicas_affected=0,
        rationale="drill: check the numbers while halted",
    )
    mutation = ProposedAction(
        runbook_id="restart-workload",
        target=CHECKOUT,
        parameters={},
        replicas_affected=2,
        rationale="drill: a fix while halted",
    )
    read_result = evaluate(read, halted, incident_target=CHECKOUT)
    mutation_result = evaluate(mutation, halted, incident_target=CHECKOUT)
    checks = {
        "reads_still_allowed": read_result.decision is Decision.ALLOW,
        "mutations_denied": mutation_result.decision is Decision.DENY,
    }
    return _finish(
        "kill-switch-reads-continue",
        "While halted, looking may continue and fixing may not",
        "kill-switch", checks, actions_run=0,
        summary=mutation_result.reason,
    )


_DRILLS = [
    _drill_restart_runs,
    _drill_scale_runs,
    _drill_rollback_needs_approval,
    _drill_blast_radius_denied,
    _drill_outside_namespace_denied,
    _drill_kill_switch_stops_mutations,
    _drill_kill_switch_reads_continue,
]


def run_tier3_drills() -> Tier3Report:
    """Run the whole drill set and return its scored report."""
    results = [drill() for drill in _DRILLS]
    report = Tier3Report(
        results=results,
        total=len(results),
        passed_count=sum(1 for r in results if r.passed),
        actions_run=sum(r.actions_run for r in results),
    )
    log.info(
        "tier3 drills: %d/%d passed, %d fix(es) run",
        report.passed_count,
        report.total,
        report.actions_run,
    )
    return report
