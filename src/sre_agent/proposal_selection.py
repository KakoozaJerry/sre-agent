"""Proposal selection: which fix a diagnosis points to, scored.

In plain words, the chaos suite (``evaluation.py``) scores what
the agent *diagnoses*. This module scores the next step: given
a diagnosis, which fix — if any — would the agent *select*?
Phase 12 asked for that selection to be measured, not only the
diagnosis.

The selector is a small set of plain, conservative rules over
the top hypothesis of a diagnosis:

- A recent deploy, with a previous version to go back to,
  selects ``rollback-deployment``.
- Fewer copies ready than wanted selects ``scale-replicas``
  back to the wanted count.
- A call timing out, a service crashing and restarting, or a
  generic request failure selects ``restart-workload``.
- Every other named cause — a full disk, a bad configuration
  value, an upstream rate limit, and the rest — selects
  *nothing*. No runbook in the library fixes those, so the
  honest selection is to escalate to a human rather than run a
  fix that cannot help. Choosing nothing is a scored answer,
  not a missing one.

What this module is not, stated plainly:

- It runs nothing, proposes nothing through the policy engine,
  and approves nothing. It returns a runbook name (or none) as
  advice, Tier 1, exactly like a memory suggestion. Any real
  proposal still goes through ``build_action``, the policy
  engine, and a human approval or a Tier 3 grant, unchanged.
- It is not wired into the server or the CLI. It exists so the
  selection can be measured in tests and in CI before any
  wiring is considered — the same order this project used for
  memory advice in Phase 7 (scored first, wired later, as
  advice only).

The suite runs each case's chaos scenario through the real
reasoning loop first, so the selector is scored on diagnoses
the loop actually produced, never on hand-written ones. Safety
is counted the same way as the chaos suite: an action would
leave an ``action`` evidence record on the incident, and the
count must be zero.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .evaluation import Scenario, default_scenarios, run_scenario
from .logging_config import get_logger
from .runbooks import WorkloadState

log = get_logger(__name__)

# Causes for which no library runbook is an honest fix. The
# selector escalates these instead of guessing a runbook.
_NO_FIX_MARKERS = (
    "disk for",
    "bad configuration",
    "running out of memory",
    "cannot be pulled",
    "deadlock",
    "connection pool",
    "name resolution",
    "tls certificate",
    "authentication or permission",
    "rate-limiting",
    "is being refused",
    "work queue",
    "is unavailable",
)


def select_runbook(
    top_hypothesis: str | None,
    *,
    ready_replicas: int,
    desired_replicas: int,
    has_previous_version: bool,
) -> str | None:
    """The runbook a diagnosis points to, or None to escalate.

    Plain rules over the top hypothesis's wording — the loop's
    own fixed wordings, so the mapping can be checked exactly.
    Advice only: this builds no proposal and runs nothing.
    """
    if top_hypothesis is None:
        return None
    text = top_hypothesis.lower()
    if "recent deploy" in text and has_previous_version:
        return "rollback-deployment"
    if any(marker in text for marker in _NO_FIX_MARKERS):
        return None
    if "fewer copies" in text and ready_replicas < desired_replicas:
        return "scale-replicas"
    if (
        "timing out" in text
        or "crashing and restarting" in text
        or "failing on some requests" in text
    ):
        return "restart-workload"
    return None


class ProposalCase(BaseModel):
    """One diagnosis, the workload behind it, and the selection
    that diagnosis should produce."""

    id: str
    scenario_id: str
    workload: WorkloadState
    expected_runbook: str | None = None


class ProposalResult(BaseModel):
    """The scored result of one proposal case."""

    case_id: str
    scenario_id: str
    top_hypothesis: str | None = None
    expected_runbook: str | None = None
    selected_runbook: str | None = None
    selection_correct: bool
    unsafe_actions: int


class ProposalReport(BaseModel):
    """The scored result of the whole proposal suite."""

    results: list[ProposalResult] = Field(default_factory=list)
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0
    unsafe_actions: int = 0

    def render_text(self) -> str:
        """The scores as plain text."""
        lines = [
            "Proposal-selection suite (diagnoses from the chaos scenarios, seeded demo data)",
            f"Cases: {self.total}  Correct selections: {self.correct}  Accuracy: {self.accuracy:.0%}",
            f"Unsafe actions across the suite: {self.unsafe_actions} (counted from incident records)",
        ]
        for result in self.results:
            mark = "PASS" if result.selection_correct else "FAIL"
            lines.append(
                f"- [{mark}] {result.case_id}: top hypothesis: "
                f"{result.top_hypothesis or 'none'}; expected "
                f"{result.expected_runbook or 'no fix (escalate)'}, "
                f"selected {result.selected_runbook or 'no fix (escalate)'}"
            )
        lines.append(
            "Advice only: a selection runs nothing and approves "
            "nothing; a real proposal still goes through the "
            "policy engine and a human approval or a Tier 3 grant."
        )
        return "\n".join(lines)


def _workload(scenario: Scenario, *, previous: list[str] | None = None) -> WorkloadState:
    status = scenario.service_status
    return WorkloadState(
        target=scenario.target,
        replicas=status.ready_replicas,
        version=status.version,
        previous_versions=previous or [],
        failing=True,
    )


def default_proposal_cases() -> list[ProposalCase]:
    """Eight cases over the chaos scenarios, by scenario id.

    Each expected selection follows the selector's stated
    rules: three causes map to a runbook, the rest escalate.
    The bad-deploy case carries a previous version, because a
    rollback can only be selected when one exists.
    """
    by_id = {s.id: s for s in default_scenarios()}
    return [
        ProposalCase(
            id="deploy-rolls-back",
            scenario_id="bad-deploy",
            workload=_workload(by_id["bad-deploy"], previous=["v1"]),
            expected_runbook="rollback-deployment",
        ),
        ProposalCase(
            id="missing-copies-scale",
            scenario_id="pod-kill",
            workload=_workload(by_id["pod-kill"]),
            expected_runbook="scale-replicas",
        ),
        ProposalCase(
            id="timeout-restarts",
            scenario_id="latency-injection",
            workload=_workload(by_id["latency-injection"]),
            expected_runbook="restart-workload",
        ),
        ProposalCase(
            id="crash-loop-restarts",
            scenario_id="crash-loop",
            workload=_workload(by_id["crash-loop"]),
            expected_runbook="restart-workload",
        ),
        ProposalCase(
            id="generic-failure-restarts",
            scenario_id="generic-error-rate",
            workload=_workload(by_id["generic-error-rate"]),
            expected_runbook="restart-workload",
        ),
        ProposalCase(
            id="disk-full-escalates",
            scenario_id="disk-fill",
            workload=_workload(by_id["disk-fill"]),
            expected_runbook=None,
        ),
        ProposalCase(
            id="bad-config-escalates",
            scenario_id="config-error",
            workload=_workload(by_id["config-error"]),
            expected_runbook=None,
        ),
        ProposalCase(
            id="rate-limit-escalates",
            scenario_id="rate-limited",
            workload=_workload(by_id["rate-limited"]),
            expected_runbook=None,
        ),
    ]


def run_proposal_case(case: ProposalCase) -> ProposalResult:
    """Diagnose the case's scenario for real, then score the
    selection made from that diagnosis."""
    scenario = next(
        s for s in default_scenarios() if s.id == case.scenario_id
    )
    diagnosed = run_scenario(scenario)
    state = case.workload
    selected = select_runbook(
        diagnosed.top_hypothesis,
        ready_replicas=state.replicas,
        desired_replicas=scenario.service_status.desired_replicas,
        has_previous_version=bool(state.previous_versions),
    )
    return ProposalResult(
        case_id=case.id,
        scenario_id=case.scenario_id,
        top_hypothesis=diagnosed.top_hypothesis,
        expected_runbook=case.expected_runbook,
        selected_runbook=selected,
        selection_correct=selected == case.expected_runbook,
        unsafe_actions=diagnosed.unsafe_actions,
    )


def run_proposal_suite(
    cases: list[ProposalCase] | None = None,
) -> ProposalReport:
    """Run the whole proposal-selection suite."""
    suite = cases if cases is not None else default_proposal_cases()
    results = [run_proposal_case(case) for case in suite]
    correct = sum(1 for r in results if r.selection_correct)
    total = len(results)
    report = ProposalReport(
        results=results,
        total=total,
        correct=correct,
        accuracy=(correct / total) if total else 0.0,
        unsafe_actions=sum(r.unsafe_actions for r in results),
    )
    log.info(
        "proposal selection: %d/%d correct, %d unsafe action(s)",
        correct,
        total,
        report.unsafe_actions,
    )
    return report
