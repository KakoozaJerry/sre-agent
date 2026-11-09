"""Runbook library v1: the three reversible fixes, for demo targets only.

A runbook is a fixed, pre-written fix. The agent may propose one by
name, with a small set of checked parameters. There is no free-form
command anywhere in this module — nothing here can run a shell, and a
parameter that is not on a runbook's own list is refused.

The three runbooks, matching the mutating entries in the policy
engine's catalogue (``policy.RUNBOOK_CATALOG``):

- ``restart-workload`` — restart the workload. In the demo this
  clears the workload's failing state and counts the restart.
- ``scale-replicas`` — set how many copies of the workload run.
  One parameter, ``replicas``, a whole number from 1 to 5.
- ``rollback-deployment`` — go back to the previous version of the
  workload. Refused when there is no previous version.

How safety is kept, in plain words:

- **Parameters are validated twice.** Once when a proposal is built
  (``build_action`` works out how many replicas an action touches,
  so the policy engine's blast-radius check sees a true number), and
  again inside the executor, so a hand-made proposal cannot skip the
  checks.
- **The executor runs nothing without an ALLOW decision.** A denied
  action is refused, and so is an action that still needs a human's
  approval: turning "a human said yes" into a runnable decision is
  the approval flow's job (the next Phase 2 item), not this module's.
- **Demo targets only.** Actions run against a small in-memory
  picture of demo workloads behind an interface, the same pattern
  the tools and the incident store used. A real cluster backend can
  sit behind the same shape later, and will still have to pass the
  same validation and the same policy gate.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .logging_config import get_logger
from .models import Evidence, Target
from .policy import Decision, PolicyResult, ProposedAction

log = get_logger(__name__)

# The scale runbook's own bounds. The policy engine separately checks
# how many replicas one action may touch in an environment; these are
# the hard limits of the runbook itself, whatever the environment says.
MIN_REPLICAS = 1
MAX_REPLICAS = 5

# Which parameters each runbook takes. An empty set means the runbook
# takes none at all. Any parameter not named here is refused.
RUNBOOK_PARAMETERS: dict[str, set[str]] = {
    "restart-workload": set(),
    "scale-replicas": {"replicas"},
    "rollback-deployment": set(),
}


class RunbookError(ValueError):
    """A runbook proposal or execution that must not go ahead."""


class WorkloadState(BaseModel):
    """One demo workload, as the runbooks see it."""

    target: Target
    replicas: int = Field(default=1, ge=0)
    version: str = "v1"
    previous_versions: list[str] = Field(default_factory=list)
    failing: bool = False
    restarts: int = 0


class DemoWorkloadBackend:
    """An in-memory set of demo workloads, behind a small interface.

    States are handed out as copies and stored back with ``save``,
    so a caller cannot change a workload by editing what it was
    given — the same care the audit log takes with its records.
    """

    def __init__(self, workloads: list[WorkloadState] | None = None) -> None:
        self._workloads: dict[tuple[str, str, str], WorkloadState] = {}
        for state in workloads or []:
            self.save(state)

    @staticmethod
    def _key(target: Target) -> tuple[str, str, str]:
        return (target.environment, target.namespace, target.service)

    def get(self, target: Target) -> WorkloadState | None:
        state = self._workloads.get(self._key(target))
        return state.model_copy(deep=True) if state is not None else None

    def save(self, state: WorkloadState) -> None:
        self._workloads[self._key(state.target)] = state.model_copy(deep=True)


def validate_parameters(
    runbook_id: str, parameters: dict[str, str | int | float | bool]
) -> dict[str, str | int | float | bool]:
    """Check one runbook's parameters and return them unchanged.

    Raises :class:`RunbookError` with a plain reason for anything
    that must not run: an unknown runbook, a parameter the runbook
    does not take, a missing one it requires, or a bad value.
    """
    allowed = RUNBOOK_PARAMETERS.get(runbook_id)
    if allowed is None:
        raise RunbookError(f"runbook '{runbook_id}' is not in the runbook library")
    extra = set(parameters) - allowed
    if extra:
        names = ", ".join(sorted(extra))
        raise RunbookError(f"runbook '{runbook_id}' does not take parameter(s): {names}")
    if runbook_id == "scale-replicas":
        if "replicas" not in parameters:
            raise RunbookError("runbook 'scale-replicas' needs a 'replicas' parameter")
        replicas = parameters["replicas"]
        # bool is a subclass of int in Python; True is not a count.
        if isinstance(replicas, bool) or not isinstance(replicas, int):
            raise RunbookError("'replicas' must be a whole number")
        if not MIN_REPLICAS <= replicas <= MAX_REPLICAS:
            raise RunbookError(
                f"'replicas' must be between {MIN_REPLICAS} and {MAX_REPLICAS}, "
                f"got {replicas}"
            )
    return dict(parameters)


def build_action(
    runbook_id: str,
    target: Target,
    parameters: dict[str, str | int | float | bool],
    backend: DemoWorkloadBackend,
    *,
    rationale: str,
) -> ProposedAction:
    """Build a checked proposal for one runbook against one workload.

    The proposal's ``replicas_affected`` is worked out from the
    workload's real state, not guessed by the proposer, so the policy
    engine's blast-radius check judges a true number: a restart or a
    rollback touches every copy that is running; a scale touches the
    difference between the copies running now and the copies asked
    for.
    """
    checked = validate_parameters(runbook_id, parameters)
    state = backend.get(target)
    if state is None:
        raise RunbookError(
            f"no demo workload named {target.service} in "
            f"{target.namespace} ({target.environment})"
        )
    if runbook_id == "scale-replicas":
        wanted = checked["replicas"]
        assert isinstance(wanted, int)  # validated above
        affected = abs(wanted - state.replicas)
    else:
        affected = state.replicas
    return ProposedAction(
        runbook_id=runbook_id,
        target=target,
        parameters=checked,
        replicas_affected=affected,
        rationale=rationale,
    )


class RunbookResult(BaseModel):
    """What a runbook did, in plain words, with before and after."""

    runbook_id: str
    target: Target
    summary: str
    before: dict[str, str | int | bool]
    after: dict[str, str | int | bool]

    def to_evidence(self) -> Evidence:
        """This result as an evidence record for the incident file."""
        target = self.target
        return Evidence(
            kind="action",
            query=(
                f"runbook:{self.runbook_id} on "
                f"{target.service}/{target.namespace} ({target.environment})"
            ),
            summary=self.summary,
        )


def _snapshot(state: WorkloadState) -> dict[str, str | int | bool]:
    return {
        "replicas": state.replicas,
        "version": state.version,
        "failing": state.failing,
        "restarts": state.restarts,
    }


class RunbookExecutor:
    """Runs runbooks against the demo backend — after the policy gate."""

    def __init__(self, backend: DemoWorkloadBackend) -> None:
        self._backend = backend

    def execute(self, action: ProposedAction, decision: PolicyResult) -> RunbookResult:
        """Run one action whose policy decision is ALLOW, and only that.

        Any other decision — denied, or still waiting for a human's
        approval — is refused here and nothing changes. Parameters
        are validated again at this door, so no proposal reaches a
        workload unchecked, however it was built.
        """
        if decision.decision is not Decision.ALLOW:
            raise RunbookError(
                f"policy decision is '{decision.decision.value}', not 'allow'; "
                "nothing was run"
            )
        validate_parameters(action.runbook_id, action.parameters)
        state = self._backend.get(action.target)
        if state is None:
            raise RunbookError(
                f"no demo workload named {action.target.service} in "
                f"{action.target.namespace} ({action.target.environment})"
            )
        before = _snapshot(state)
        if action.runbook_id == "restart-workload":
            state.restarts += 1
            state.failing = False
            summary = (
                f"Restarted {state.target.service}: restarts now {state.restarts}, "
                f"failing state cleared."
            )
        elif action.runbook_id == "scale-replicas":
            wanted = action.parameters["replicas"]
            assert isinstance(wanted, int)  # validated above
            summary = (
                f"Scaled {state.target.service} from {state.replicas} "
                f"to {wanted} replicas."
            )
            state.replicas = wanted
        elif action.runbook_id == "rollback-deployment":
            if not state.previous_versions:
                raise RunbookError(
                    f"{state.target.service} has no previous version to roll back to"
                )
            old = state.version
            state.version = state.previous_versions.pop()
            summary = f"Rolled {state.target.service} back from {old} to {state.version}."
        else:  # pragma: no cover - validate_parameters already refused it
            raise RunbookError(f"runbook '{action.runbook_id}' is not in the runbook library")
        self._backend.save(state)
        log.info("runbook executed: %s", summary)
        return RunbookResult(
            runbook_id=action.runbook_id,
            target=action.target,
            summary=summary,
            before=before,
            after=_snapshot(state),
        )


def demo_runbooks() -> tuple[RunbookExecutor, DemoWorkloadBackend]:
    """A seeded executor anyone can try with no setup.

    The checkout workload matches the demo environment: it is
    failing, runs two copies, and is on version v2 with v1 before
    it — so all three runbooks have something honest to do. The
    search workload is healthy, on its first version, so a rollback
    against it is refused for a real reason.
    """
    backend = DemoWorkloadBackend(
        [
            WorkloadState(
                target=Target(environment="demo", namespace="shop", service="checkout"),
                replicas=2,
                version="v2",
                previous_versions=["v1"],
                failing=True,
            ),
            WorkloadState(
                target=Target(environment="demo", namespace="shop", service="search"),
                replicas=1,
                version="v1",
            ),
        ]
    )
    return RunbookExecutor(backend), backend
