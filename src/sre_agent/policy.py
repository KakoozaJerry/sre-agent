"""The policy engine: the only path between a proposal and an action.

Pure functions over explicit inputs. No model output is ever trusted here;
a proposal arrives as structured data and leaves as a decision with a reason.
See docs/safety-model.md for the contract this code implements.

What this module decides, in plain words:

- **Classification.** Every action names a runbook, and every runbook the
  agent may ever propose is listed in one fixed catalogue in this file.
  The catalogue says whether the action only reads (it changes nothing)
  or mutates reversibly (it changes something that can be undone, like a
  restart, a scale, or a rollback). A runbook that is not in the
  catalogue is unknown, and unknown actions are denied — a made-up
  runbook name can never slip through as "needs approval".
- **Blast radius.** A mutating action is checked against the limits of
  its environment: how many replicas it touches, which namespaces are
  allowed at all, which namespaces are on the no-touch list, and whether
  its target is still the target pinned on the incident it belongs to.
- **Tier gate.** What remains is decided by the environment's tier
  (Section 6 of the plan): read-only actions are allowed, mutations
  need a human's approval at Tier 2, and only pre-approved runbook
  classes run without one at Tier 3.

Recording the decision is the audit log's job (``audit.py``); this
module only decides. ``audit.evaluate_and_audit`` is the normal way to
call both together, so no decision goes unrecorded.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from .models import Target


class Tier(int, Enum):
    OBSERVE = 0
    ADVISE = 1
    ACT_WITH_APPROVAL = 2
    ACT_WITHIN_BOUNDS = 3


class Decision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class ActionClass(str, Enum):
    """What kind of action a runbook is. Fixed by the catalogue below."""

    READ_ONLY = "read_only"
    REVERSIBLE_MUTATION = "reversible_mutation"


# The allow-list. Every runbook id the agent may propose must be named
# here, with its class. Anything not named here is unknown and denied.
# The three read-only entries are the tool layer's tools; the three
# mutating entries are the runbook library's first runbooks (Phase 2).
RUNBOOK_CATALOG: dict[str, ActionClass] = {
    "query-metrics": ActionClass.READ_ONLY,
    "search-logs": ActionClass.READ_ONLY,
    "get-service-status": ActionClass.READ_ONLY,
    "restart-workload": ActionClass.REVERSIBLE_MUTATION,
    "scale-replicas": ActionClass.REVERSIBLE_MUTATION,
    "rollback-deployment": ActionClass.REVERSIBLE_MUTATION,
}


def classify_action(runbook_id: str) -> ActionClass | None:
    """Return the class of a runbook, or None if it is not allow-listed."""
    return RUNBOOK_CATALOG.get(runbook_id)


class ProposedAction(BaseModel):
    """A structured proposal. Free-form commands are not representable."""

    runbook_id: str
    target: Target
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    replicas_affected: int = Field(default=0, ge=0)
    rationale: str


class EnvironmentPolicy(BaseModel):
    """What the agent may do in one environment. Defaults are restrictive."""

    environment: str
    granted_tier: Tier = Tier.ADVISE
    # runbook classes allowed to run at Tier 3 in this environment
    tier3_runbooks: set[str] = Field(default_factory=set)
    max_replicas_affected: int = 1
    # if non-empty, only these namespaces may be touched at all
    allowed_namespaces: set[str] = Field(default_factory=set)
    denied_namespaces: set[str] = Field(default_factory=set)
    halted: bool = False


class PolicyResult(BaseModel):
    decision: Decision
    reason: str
    action_class: ActionClass | None = None


def evaluate(
    action: ProposedAction,
    policy: EnvironmentPolicy,
    *,
    incident_target: Target | None = None,
) -> PolicyResult:
    """Decide whether a proposed action may run, needs approval, or is denied.

    Order matters: hard stops are checked before any allowance, so a halted
    or out-of-scope environment can never be overridden by a grant.

    ``incident_target`` is the target pinned on the incident the action
    belongs to, when the caller has one. An action aimed anywhere else is
    denied: actions cannot retarget mid-flight.
    """
    action_class = classify_action(action.runbook_id)

    if action.target.environment != policy.environment:
        return PolicyResult(
            decision=Decision.DENY,
            reason="action target environment does not match this policy",
            action_class=action_class,
        )

    if action.target.namespace in policy.denied_namespaces:
        return PolicyResult(
            decision=Decision.DENY,
            reason=f"namespace '{action.target.namespace}' is on the no-touch list",
            action_class=action_class,
        )

    if incident_target is not None and action.target != incident_target:
        return PolicyResult(
            decision=Decision.DENY,
            reason="action target does not match the target pinned on the incident",
            action_class=action_class,
        )

    if action_class is None:
        return PolicyResult(
            decision=Decision.DENY,
            reason=f"runbook '{action.runbook_id}' is not on the allow-list",
            action_class=None,
        )

    if action_class is ActionClass.READ_ONLY:
        # Reads change nothing, so the tier gate and the halt switch do
        # not block them (docs/safety-model.md: "Reads may continue").
        return PolicyResult(
            decision=Decision.ALLOW,
            reason="read-only action; it changes nothing",
            action_class=action_class,
        )

    if policy.halted:
        return PolicyResult(
            decision=Decision.DENY,
            reason="agent is halted in this environment",
            action_class=action_class,
        )

    if policy.allowed_namespaces and action.target.namespace not in policy.allowed_namespaces:
        return PolicyResult(
            decision=Decision.DENY,
            reason=f"namespace '{action.target.namespace}' is not in this environment's "
            "allowed namespaces",
            action_class=action_class,
        )

    if action.replicas_affected > policy.max_replicas_affected:
        return PolicyResult(
            decision=Decision.DENY,
            reason=(
                f"blast radius too large: {action.replicas_affected} replicas affected, "
                f"limit is {policy.max_replicas_affected}"
            ),
            action_class=action_class,
        )

    if policy.granted_tier < Tier.ACT_WITH_APPROVAL:
        return PolicyResult(
            decision=Decision.DENY,
            reason=f"environment is at Tier {policy.granted_tier}; mutations are not granted",
            action_class=action_class,
        )

    if (
        policy.granted_tier >= Tier.ACT_WITHIN_BOUNDS
        and action.runbook_id in policy.tier3_runbooks
    ):
        return PolicyResult(
            decision=Decision.ALLOW,
            reason="pre-approved runbook class within blast-radius limits (Tier 3)",
            action_class=action_class,
        )

    return PolicyResult(
        decision=Decision.REQUIRE_APPROVAL,
        reason="mutation requires a human to approve this specific action (Tier 2)",
        action_class=action_class,
    )
