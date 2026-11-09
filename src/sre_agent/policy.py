"""The policy engine: the only path between a proposal and an action.

Pure functions over explicit inputs. No model output is ever trusted here;
a proposal arrives as structured data and leaves as a decision with a reason.
See docs/safety-model.md for the contract this code implements.
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
    denied_namespaces: set[str] = Field(default_factory=set)
    halted: bool = False


class PolicyResult(BaseModel):
    decision: Decision
    reason: str


def evaluate(action: ProposedAction, policy: EnvironmentPolicy) -> PolicyResult:
    """Decide whether a proposed action may run, needs approval, or is denied.

    Order matters: hard stops are checked before any allowance, so a halted
    or out-of-scope environment can never be overridden by a grant.
    """
    if policy.halted:
        return PolicyResult(decision=Decision.DENY, reason="agent is halted in this environment")

    if action.target.environment != policy.environment:
        return PolicyResult(
            decision=Decision.DENY,
            reason="action target environment does not match this policy",
        )

    if action.target.namespace in policy.denied_namespaces:
        return PolicyResult(
            decision=Decision.DENY,
            reason=f"namespace '{action.target.namespace}' is on the no-touch list",
        )

    if action.replicas_affected > policy.max_replicas_affected:
        return PolicyResult(
            decision=Decision.DENY,
            reason=(
                f"blast radius too large: {action.replicas_affected} replicas affected, "
                f"limit is {policy.max_replicas_affected}"
            ),
        )

    if policy.granted_tier < Tier.ACT_WITH_APPROVAL:
        return PolicyResult(
            decision=Decision.DENY,
            reason=f"environment is at Tier {policy.granted_tier}; mutations are not granted",
        )

    if (
        policy.granted_tier >= Tier.ACT_WITHIN_BOUNDS
        and action.runbook_id in policy.tier3_runbooks
    ):
        return PolicyResult(
            decision=Decision.ALLOW,
            reason="pre-approved runbook class within blast-radius limits (Tier 3)",
        )

    return PolicyResult(
        decision=Decision.REQUIRE_APPROVAL,
        reason="mutation requires a human to approve this specific action (Tier 2)",
    )
