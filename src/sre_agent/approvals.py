"""Approval flow: how a human's yes turns one proposal into one run.

In plain words, this is the Tier 2 path from the safety model:

1. The policy engine looks at a proposed fix and says it needs a
   human's approval. That proposal becomes a *pending action* here:
   one specific runbook, on one specific target, with one specific
   set of parameters, for one specific incident.
2. A person approves that pending action — by name, never
   anonymously. The approval is not a blanket yes: it carries the
   fingerprint (a SHA-256 hash) of the exact parameters that were
   proposed, and it expires 15 minutes after the proposal was made.
3. At approval time the policy engine is asked *again*. The world
   can change between proposal and approval — the environment can be
   halted, taken out of scope, or tightened — and an action the
   policy now denies stays denied even though a human said yes. The
   safety model has always said this: an out-of-limits action is
   denied even if a human approved it.
4. Only then does approval produce an ALLOW decision, written to the
   audit log with the approver's name. That decision, together with
   the single-use grant made here, is what the runbook executor
   will run — and the grant is spent the moment it is used, even
   if the run itself fails, so it can never be replayed for a
   second run. A second run needs a new proposal and a new yes.

Scope, stated honestly: this module is the one approval flow,
used in two places. The CLI's ``approve-demo`` runs it in one
process against the demo runbook backend. Since Phase 7 the
server runs the same functions for live incidents when it is
built with a policy and a runbook backend (see ``server.py``):
the pending actions, grants and re-checks are exactly these,
held by the server instead of one terminal process. Either way
the backend is still the demo backend today — no real system is
acted on — and nothing here claims otherwise.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from .audit import AuditLog, parameters_hash
from .models import Target
from .policy import (
    Decision,
    EnvironmentPolicy,
    PolicyResult,
    ProposedAction,
    evaluate,
)
from .runbooks import RunbookExecutor, RunbookResult

# How long a proposal waits for a human before it goes stale. A
# proposal older than this must be proposed again, so the human
# approves the state of the world now, not a quarter-hour ago.
APPROVAL_TTL = timedelta(minutes=15)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ApprovalError(ValueError):
    """An approval that must not go ahead, with a plain reason."""


class PendingAction(BaseModel):
    """One proposal waiting for a human, exactly as it was proposed."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    incident_id: str
    action: ProposedAction
    reason: str  # the policy engine's reason for needing approval
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    parameters_hash: str

    def is_expired(self, now: datetime) -> bool:
        return now > self.expires_at


def create_pending(
    action: ProposedAction,
    result: PolicyResult,
    *,
    incident_id: str,
    now: datetime | None = None,
) -> PendingAction:
    """Make a pending action from a REQUIRE_APPROVAL policy decision.

    Anything else is refused: an allowed action needs no approval,
    and a denied action must never be parked as if a human could
    wave it through.
    """
    if result.decision is not Decision.REQUIRE_APPROVAL:
        raise ApprovalError(
            f"policy decision is '{result.decision.value}', not 'require_approval'; "
            "only an action the policy sends for approval can wait for one"
        )
    created = now if now is not None else _utcnow()
    return PendingAction(
        incident_id=incident_id,
        action=action,
        reason=result.reason,
        created_at=created,
        expires_at=created + APPROVAL_TTL,
        parameters_hash=parameters_hash(action),
    )


class ApprovalGrant(BaseModel):
    """One human's yes for one pending action. Single-use, expiring."""

    pending_id: str
    incident_id: str
    runbook_id: str
    target: Target
    parameters_hash: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime
    used: bool = False


def approve_pending(
    pending: PendingAction,
    *,
    approved_by: str,
    policy: EnvironmentPolicy,
    audit_log: AuditLog,
    incident_target: Target | None = None,
    now: datetime | None = None,
) -> tuple[ApprovalGrant, PolicyResult]:
    """Approve one pending action and return its grant and ALLOW decision.

    The policy engine decides again here, at approval time: a
    decision of DENY is recorded in the audit log and the approval
    fails; a decision of ALLOW means the action no longer needs a
    human (for example the environment gained a Tier 3 grant), so
    there is nothing to approve. Only a fresh REQUIRE_APPROVAL is
    turned, by this named human's yes, into an ALLOW — and that
    ALLOW is written to the audit log, with the approver's name,
    before anything can run.
    """
    approver = approved_by.strip()
    if not approver:
        raise ApprovalError("an approval must name who is approving")
    moment = now if now is not None else _utcnow()
    if pending.is_expired(moment):
        raise ApprovalError(
            "this proposal has expired (approvals last "
            f"{int(APPROVAL_TTL.total_seconds() // 60)} minutes); "
            "propose the action again so the approval covers it as it stands now"
        )
    rechecked = evaluate(pending.action, policy, incident_target=incident_target)
    if rechecked.decision is Decision.DENY:
        audit_log.record_decision(
            incident_id=pending.incident_id, action=pending.action, result=rechecked
        )
        raise ApprovalError(
            f"the policy now denies this action, so the approval cannot stand: "
            f"{rechecked.reason}"
        )
    if rechecked.decision is Decision.ALLOW:
        raise ApprovalError(
            "this action no longer needs approval — the policy allows it outright; "
            "run it through the normal Tier 3 path instead"
        )
    allowed = PolicyResult(
        decision=Decision.ALLOW,
        reason=(
            f"approved by {approver} for this specific action on incident "
            f"{pending.incident_id} (Tier 2)"
        ),
        action_class=rechecked.action_class,
    )
    audit_log.record_decision(
        incident_id=pending.incident_id,
        action=pending.action,
        result=allowed,
        approved_by=approver,
    )
    grant = ApprovalGrant(
        pending_id=pending.id,
        incident_id=pending.incident_id,
        runbook_id=pending.action.runbook_id,
        target=pending.action.target,
        parameters_hash=pending.parameters_hash,
        approved_by=approver,
        approved_at=moment,
        expires_at=pending.expires_at,
    )
    return grant, allowed


def execute_approved(
    executor: RunbookExecutor,
    pending: PendingAction,
    grant: ApprovalGrant,
    decision: PolicyResult,
    *,
    now: datetime | None = None,
) -> RunbookResult:
    """Run one approved action, once, through the runbook executor.

    Every part of the grant must match the pending action it is
    used with — the proposal it came from, the incident, the
    runbook, the target, and the fingerprint of the exact
    parameters. A grant changed in any of those, expired, or
    already used is refused and nothing runs. The grant is marked
    used *before* the run starts: if the run fails, the approval
    is still spent, and running again needs a new proposal and a
    new human yes.
    """
    if decision.decision is not Decision.ALLOW:
        raise ApprovalError(
            f"policy decision is '{decision.decision.value}', not 'allow'; nothing was run"
        )
    moment = now if now is not None else _utcnow()
    if grant.used:
        raise ApprovalError(
            "this approval has already been used; an approval runs one action, once — "
            "propose the action again for a new approval"
        )
    if moment > grant.expires_at:
        raise ApprovalError("this approval has expired; propose the action again")
    matches = (
        grant.pending_id == pending.id
        and grant.incident_id == pending.incident_id
        and grant.runbook_id == pending.action.runbook_id
        and grant.target == pending.action.target
        and grant.parameters_hash == pending.parameters_hash
        and grant.parameters_hash == parameters_hash(pending.action)
    )
    if not matches:
        raise ApprovalError(
            "this approval does not match this action — an approval covers the exact "
            "action it was given for, and no other"
        )
    grant.used = True
    return executor.execute(pending.action, decision)
