"""Webhook server: the agent's front door for alerts.

Run it with:

    uvicorn sre_agent.server:app --port 8080

Endpoints, in plain words:

- ``GET /health`` — is the agent up? Always answers ``{"status": "ok"}``.
  This one stays open even when a token is set, so a health check
  never needs the secret.
- ``POST /webhooks/alertmanager`` — send an Alertmanager payload;
  each alert in it is ingested (see ingestion.py for dedupe/grouping).
- ``POST /webhooks/generic`` — send one simple alert as JSON.
- ``GET /incidents`` — list the incidents the agent knows about.
- ``GET /incidents/{id}`` — one incident, in full.
- ``POST /incidents/{id}/actions`` — propose a runbook fix for a
  diagnosed incident; the policy engine decides and, when it asks
  for a human, the proposal is stored as a pending approval.
- ``GET /incidents/{id}/approvals`` — the pending approvals held
  for one incident, with their state.
- ``POST /incidents/{id}/approvals/{pending_id}/approve`` — one
  named human approves one pending action; it then runs, once.
- ``POST /incidents/{id}/approvals/{pending_id}/reject`` — one
  named human turns one pending action down; nothing runs.

Authentication (security finding F-01, fixed after v0.1.0): set the
``SRE_AGENT_API_TOKEN`` environment variable (or pass ``auth_token``
to :func:`create_app`) and every endpoint except ``/health`` then
requires that exact token in the ``X-SRE-Agent-Token`` header —
webhooks and incident reads alike. A missing or wrong token gets a
401 and nothing is ingested or shown. With no token set, the server
is unauthenticated, exactly as v0.1.0 was: that stays fine for one
operator on their own machine, and only for that. Before the server
is shared with anyone else, set a token. Alertmanager can send the
header too (its webhook receiver's ``http_config``), and the CLI
sends it automatically when the same environment variable is set.

Diagnosis (Phase 7): when the app is built with a tool layer
(``create_app(tool_layer=...)``), the reasoning loop runs inside
the server. A firing alert that opens a new incident is diagnosed
at once: the loop's evidence, its possible causes and its
diagnosis are stored on the incident itself, so ``GET
/incidents/{id}`` — and the CLI's ``show`` command, which reads
that endpoint — show the diagnosis with the incident. An update
that groups onto an incident a human was handed (status
"escalated") is diagnosed again; a duplicate changes nothing and
is never re-diagnosed, and an incident already diagnosed keeps
its diagnosis when a grouped alert adds evidence. The shipped
``app`` is built with the demo tool layer, so its diagnoses come
from the seeded demo data and claim nothing about a real system;
an app built with no tool layer records incidents only, exactly
as before. If the loop itself fails, the incident stays as
ingestion left it and the failure is logged — a broken diagnosis
never loses an alert.

Adapter evidence (Phase 7): when the app is also built with a
bundle of read adapters (``create_app(adapters=...)``, see
``adapters.AdapterSources``), the loop asks each selected
adapter one fixed question during a diagnosis and stores the
answers on the incident as evidence, under the same rules as
in ``reasoning.py``: adapter questions are built only from
validated target names, a failing adapter is skipped, and
adapter answers can support a claim or a cause only as counts
and measured values. The shipped ``app`` is built with the demo
adapter bundle, so its diagnoses carry Kubernetes, Datadog and
Azure Monitor evidence from the same seeded demo picture; a
real deployment passes its own bundle, whose live adapters
already passed the backend address rules when they were built.
An app built with adapters but no tool layer still records
only, because adapters supplement the loop, they do not
replace it.

Memory advice (Phase 7): when the app is also built with a
memory of past incidents (``create_app(memory=...)``, see
``memory.IncidentMemory``), the loop attaches that memory's
report to each incident it diagnoses — similar past incidents,
each runbook's record, and suggested runbooks — stored on the
incident as ``memory_report``, so ``GET /incidents/{id}`` and
``sre-agent show`` carry it with the diagnosis. The report is
computed after the diagnosis is decided and is advice only:
it is never an input to a claim, a cause, a proposal or an
action, and the server creates no proposal from a suggestion —
a suggested fix still has to be proposed and approved through
the live approval flow like any other. The shipped ``app`` is
built with the seeded demo memory (four past incidents), so
its diagnosed demo incidents carry advice from that seeded
past and claim nothing about a real system; an app built with
no memory attaches none, and a memory handed in without a
tool layer still records only, because there is no diagnosis
to attach advice to.

Database store (Phase 7): the store is passed in, so a server
built with ``storage.SQLiteIncidentStore`` (a file path) keeps
its incidents across a restart — diagnoses included, because a
diagnosis is a field on the incident — and its pending approvals
too: the server restores the approvals the store holds when it
starts, keeps each new one through the store, and saves the
incident again after a diagnosis and after a run, which is what
writes those later changes through. A restored approval keeps
its original expiry, and a decided one stays decided. A server
built with the in-memory store (the shipped ``app`` included)
behaves exactly as before: stop it and its incidents and
approvals are gone.

Live approvals (Phase 7): when the app is built with an
environment policy and a runbook backend
(``create_app(approval_policy=..., runbook_backend=...)``), the
server also serves the Tier 2 approval flow. A fix is proposed
for one diagnosed incident: the proposal's target is the target
pinned on that incident — the request body cannot name a target,
so a proposal cannot be aimed anywhere else — and how many
replicas it touches is counted from the backend's real state by
``runbooks.build_action``, never claimed by the proposer. The
policy engine decides, and the decision is written to the
server's audit log before anything else happens. A denied
proposal is refused. A proposal the policy sends for approval
waits as a pending action for at most 15 minutes; approving it
re-runs the policy check at approval time (a halt or tightening
since the proposal stands), spends the approval before the run
starts, and runs the fix exactly once. Rejecting a pending
action runs nothing. A run that succeeds adds its result to the
incident as evidence and moves the incident to "mitigating" —
never straight to "resolved", because the server has not checked
that the fix worked; resolution still arrives as a resolved
alert, as it always has. The shipped ``app`` is built with the
demo policy (Tier 2, the demo environment) and the demo runbook
backend, so its approvals act on the seeded demo workloads only
and claim nothing about a real system; an app built without a
policy or a backend holds no approvals, and its approval routes
answer 409 saying so.

Hosted demo mode (Phase 10): a server built with
``tier3_only=True`` can run only what its policy allows outright.
A proposal the policy sends for a human's approval is refused
with 403 and no pending action is created, and the approve and
reject routes answer 409 — in that mode there is no approval
path at all, so the only actions that can ever run are the ones
a Tier 3 grant pre-approved. ``hosted_demo.py`` builds the
public-safe server this way: a token is required, the data is
the seeded demo data only, and the policy is the demo Tier 3
grant. See ``docs/hosted-demo.md``.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .adapters import AdapterSources, demo_adapter_sources
from .approvals import (
    ApprovalError,
    PendingAction,
    approve_pending,
    create_pending,
    execute_approved,
)
from .audit import AuditLog, evaluate_and_audit
from .ingestion import (
    AlertmanagerPayload,
    GenericAlertPayload,
    IncidentStore,
    IngestionLimitError,
    IngestResult,
    normalize_alertmanager,
    normalize_generic,
)
from .logging_config import get_logger
from .memory import IncidentMemory, demo_incident_memory
from .model_backend import DiagnosisBackend
from .models import Incident, IncidentStatus
from .policy import Decision, EnvironmentPolicy, Tier
from .reasoning import ReasoningLoop
from .runbooks import (
    DemoWorkloadBackend,
    RunbookError,
    RunbookExecutor,
    build_action,
    demo_runbooks,
)
from .tools import ToolLayer, demo_tool_layer

log = get_logger(__name__)

ENV_API_TOKEN = "SRE_AGENT_API_TOKEN"
TOKEN_HEADER = "X-SRE-Agent-Token"


def _result_view(result: IngestResult) -> dict[str, object]:
    incident = result.incident
    return {
        "incident_id": incident.id if incident else None,
        "created": result.created,
        "duplicate": result.duplicate,
        "grouped": result.grouped,
        "resolved": result.resolved,
        "status": incident.status.value if incident else None,
        "diagnosis": incident.diagnosis if incident else None,
    }


def _incident_summary(store: IncidentStore, incident: Incident) -> dict[str, object]:
    return {
        "id": incident.id,
        "title": incident.title,
        "status": incident.status.value,
        "target": incident.target.model_dump(),
        "evidence_count": len(incident.evidence),
        "duplicate_count": store.duplicate_count(incident.id),
        "diagnosis": incident.diagnosis,
        "memory_matches": (
            len(incident.memory_report.matches) if incident.memory_report else 0
        ),
        "memory_suggestions": (
            len(incident.memory_report.suggestions)
            if incident.memory_report
            else 0
        ),
    }


class ProposeActionRequest(BaseModel):
    """One fix proposed through the server, for one incident.

    There is deliberately no target field: the action's target is
    the target pinned on the incident named in the URL, so a
    request cannot aim a fix anywhere else.
    """

    runbook_id: str = Field(max_length=200)
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    rationale: str = Field(
        default="proposed through the server for this incident",
        max_length=1000,
    )


class ApprovalDecisionRequest(BaseModel):
    """One named human's decision on one pending action."""

    by: str = Field(max_length=200)


def _pending_view(
    pending: PendingAction, decisions: dict[str, str]
) -> dict[str, object]:
    """One pending action as the API shows it, with its state."""
    if pending.id in decisions:
        status = decisions[pending.id]
    elif pending.is_expired(datetime.now(UTC)):
        status = "expired"
    else:
        status = "pending"
    return {
        "id": pending.id,
        "incident_id": pending.incident_id,
        "runbook_id": pending.action.runbook_id,
        "target": pending.action.target.model_dump(),
        "parameters": pending.action.parameters,
        "replicas_affected": pending.action.replicas_affected,
        "parameters_hash": pending.parameters_hash,
        "reason": pending.reason,
        "status": status,
        "created_at": pending.created_at.isoformat(),
        "expires_at": pending.expires_at.isoformat(),
    }


def create_app(
    store: IncidentStore | None = None,
    auth_token: str | None = None,
    tool_layer: ToolLayer | None = None,
    approval_policy: EnvironmentPolicy | None = None,
    runbook_backend: DemoWorkloadBackend | None = None,
    audit_log: AuditLog | None = None,
    adapters: AdapterSources | None = None,
    memory: IncidentMemory | None = None,
    diagnosis_backend: DiagnosisBackend | None = None,
    tier3_only: bool = False,
) -> FastAPI:
    """Build the webhook app. Pass a store to share or inspect state.

    ``auth_token`` wins when given; otherwise the token is read from
    the ``SRE_AGENT_API_TOKEN`` environment variable. An empty value
    counts as no token.

    ``tool_layer`` turns on server-side diagnosis: with one given,
    the reasoning loop diagnoses each new incident as its alert is
    ingested, and the diagnosis is stored on the incident. With no
    tool layer, the server records incidents only.

    ``approval_policy`` and ``runbook_backend`` together turn on
    live approvals: proposals for diagnosed incidents are decided
    by that policy (every decision written to ``audit_log``, a
    fresh in-memory one when none is given) and approved actions
    run against that backend. With either missing, the server
    holds no approvals and the approval routes answer 409.

    ``adapters`` selects read adapters as extra evidence sources
    for the loop's diagnoses. It only takes effect together with
    a ``tool_layer``: with no tool layer there is no loop, and
    the server records incidents only.

    ``memory`` hands the loop a memory of past incidents; the
    loop attaches its advice (a ``MemoryReport``) to each
    incident it diagnoses, after the diagnosis is decided. It
    likewise only takes effect together with a ``tool_layer``.

    ``diagnosis_backend`` selects who reads the gathered
    evidence for the loop's diagnoses (see
    ``model_backend.py``). With none given, the deterministic
    backend runs — the default. A language-model backend takes
    effect only when an operator builds one and passes it here
    on purpose; the shipped ``app`` below passes none, so the
    model backend is off in the shipped server. It likewise
    only takes effect together with a ``tool_layer``.

    ``store`` may be a ``storage.SQLiteIncidentStore``: then
    incidents, diagnoses and pending approvals persist in its
    file and are restored when a server is built on it again.
    With the in-memory store (the default) nothing persists.

    ``tier3_only`` turns off the approval path: a proposal the
    policy does not allow outright is refused and never becomes
    a pending action, and the approve/reject routes answer 409.
    Only a policy ALLOW — in the hosted demo, the demo Tier 3
    grant — can run an action on such a server.
    """
    incident_store = store if store is not None else IncidentStore()
    loop = (
        ReasoningLoop(
            tool_layer, adapters=adapters, memory=memory, backend=diagnosis_backend
        )
        if tool_layer is not None
        else None
    )
    executor = (
        RunbookExecutor(runbook_backend) if runbook_backend is not None else None
    )
    audit = audit_log if audit_log is not None else AuditLog()
    # Restored approvals (a database store hands back what a
    # previous server left; the in-memory store hands back none).
    pendings, decisions = incident_store.load_approvals()

    def diagnose_if_needed(result: IngestResult) -> None:
        """Run the reasoning loop for one ingested alert, if it fits.

        Only two moments are diagnosed: a newly opened incident
        (still in triage), and a grouped update to an incident that
        was escalated to a human (sent back to triage for a fresh
        look). Duplicates, resolved alerts, and incidents already
        diagnosed or further along keep the record they have.
        """
        if loop is None or result.incident is None:
            return
        if result.duplicate or result.resolved:
            return
        incident = result.incident
        if incident.status is IncidentStatus.ESCALATED:
            incident.transition(IncidentStatus.TRIAGING)
        if incident.status is not IncidentStatus.TRIAGING:
            return
        try:
            loop.diagnose(incident)
        except Exception:
            log.exception(
                "diagnosis failed for incident %s; the incident is "
                "kept as ingested",
                incident.id,
            )
        # The diagnosis (or the triage move that preceded a failed
        # one) changed the record outside ingestion: keep the
        # change through the store, so a database store writes it.
        incident_store.save(incident)
    token = auth_token
    if token is None:
        token = os.environ.get(ENV_API_TOKEN) or None
    if token == "":
        token = None
    app = FastAPI(title="sre-agent")

    def require_token(
        x_sre_agent_token: str | None = Header(default=None, alias=TOKEN_HEADER),
    ) -> None:
        if token is None:
            return
        if x_sre_agent_token is None or not secrets.compare_digest(
            x_sre_agent_token, token
        ):
            raise HTTPException(
                status_code=401, detail="missing or wrong API token"
            )

    guarded = [Depends(require_token)]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/alertmanager", dependencies=guarded)
    def alertmanager_webhook(payload: AlertmanagerPayload) -> dict[str, object]:
        try:
            results = [
                incident_store.ingest(alert)
                for alert in normalize_alertmanager(payload)
            ]
        except IngestionLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        for result in results:
            diagnose_if_needed(result)
        return {
            "received": len(results),
            "results": [_result_view(r) for r in results],
            "active_incidents": sum(
                1
                for inc in incident_store.list_incidents()
                if inc.status.value != "resolved"
            ),
        }

    @app.post("/webhooks/generic", dependencies=guarded)
    def generic_webhook(payload: GenericAlertPayload) -> dict[str, object]:
        try:
            result = incident_store.ingest(normalize_generic(payload))
        except IngestionLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        diagnose_if_needed(result)
        return _result_view(result)

    @app.get("/incidents", dependencies=guarded)
    def list_incidents() -> list[dict[str, object]]:
        return [
            _incident_summary(incident_store, inc)
            for inc in incident_store.list_incidents()
        ]

    @app.get("/incidents/{incident_id}", dependencies=guarded)
    def get_incident(incident_id: str) -> dict[str, object]:
        incident = incident_store.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return {
            **incident.model_dump(mode="json"),
            "duplicate_count": incident_store.duplicate_count(incident.id),
        }

    def _incident_or_404(incident_id: str) -> Incident:
        incident = incident_store.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return incident

    def _approvals_or_409() -> tuple[EnvironmentPolicy, RunbookExecutor]:
        if approval_policy is None or executor is None or runbook_backend is None:
            raise HTTPException(
                status_code=409,
                detail="live approvals are not configured on this server",
            )
        return approval_policy, executor

    def _pending_or_404(incident_id: str, pending_id: str) -> PendingAction:
        pending = pendings.get(pending_id)
        if pending is None or pending.incident_id != incident_id:
            raise HTTPException(status_code=404, detail="approval not found")
        return pending

    @app.post(
        "/incidents/{incident_id}/actions",
        dependencies=guarded,
        status_code=201,
    )
    def propose_action(
        incident_id: str, body: ProposeActionRequest
    ) -> dict[str, object]:
        incident = _incident_or_404(incident_id)
        policy, _ = _approvals_or_409()
        assert runbook_backend is not None  # checked by _approvals_or_409
        if incident.status is not IncidentStatus.DIAGNOSED:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"incident is '{incident.status.value}'; only a diagnosed "
                    "incident can have a fix proposed for it"
                ),
            )
        try:
            action = build_action(
                body.runbook_id,
                incident.target,
                body.parameters,
                runbook_backend,
                rationale=body.rationale,
            )
        except RunbookError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result = evaluate_and_audit(
            action,
            policy,
            audit,
            incident_id=incident.id,
            incident_target=incident.target,
        )
        if result.decision is Decision.DENY:
            raise HTTPException(status_code=403, detail=result.reason)
        if result.decision is Decision.ALLOW:
            # A policy that allows outright (a Tier 3 grant) runs
            # through the same executor; the decision is already
            # in the audit log.
            assert executor is not None
            try:
                run_result = executor.execute(action, result)
            except RunbookError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            incident.add_evidence(run_result.to_evidence())
            incident.transition(IncidentStatus.MITIGATING)
            incident_store.save(incident)
            return {
                "decision": result.decision.value,
                "reason": result.reason,
                "summary": run_result.summary,
                "incident_status": incident.status.value,
                "pending": None,
            }
        if tier3_only:
            # Hosted demo mode: there is no approval path, so a
            # proposal that is not allowed outright cannot run.
            # The decision is already in the audit log; nothing
            # becomes pending and nothing runs.
            raise HTTPException(
                status_code=403,
                detail=(
                    "this server runs only actions its policy allows "
                    "outright (the Tier 3 demo grant); "
                    f"{body.runbook_id} needs a human approval, and "
                    "approvals are disabled in hosted demo mode"
                ),
            )
        pending = create_pending(action, result, incident_id=incident.id)
        pendings[pending.id] = pending
        incident_store.save_approval(pending, None)
        return {
            "decision": result.decision.value,
            "reason": result.reason,
            "summary": None,
            "incident_status": incident.status.value,
            "pending": _pending_view(pending, decisions),
        }

    @app.get("/incidents/{incident_id}/approvals", dependencies=guarded)
    def list_approvals(incident_id: str) -> list[dict[str, object]]:
        _incident_or_404(incident_id)
        _approvals_or_409()
        return [
            _pending_view(pending, decisions)
            for pending in pendings.values()
            if pending.incident_id == incident_id
        ]

    @app.post(
        "/incidents/{incident_id}/approvals/{pending_id}/approve",
        dependencies=guarded,
    )
    def approve_action(
        incident_id: str, pending_id: str, body: ApprovalDecisionRequest
    ) -> dict[str, object]:
        incident = _incident_or_404(incident_id)
        policy, executor = _approvals_or_409()
        if tier3_only:
            raise HTTPException(
                status_code=409,
                detail=(
                    "approvals are disabled in hosted demo mode; "
                    "only the Tier 3 demo grant can run an action"
                ),
            )
        pending = _pending_or_404(incident_id, pending_id)
        if pending.id in decisions:
            raise HTTPException(
                status_code=409,
                detail="this approval has already been decided; "
                "propose the action again for a new approval",
            )
        try:
            grant, allowed = approve_pending(
                pending,
                approved_by=body.by,
                policy=policy,
                audit_log=audit,
                incident_target=incident.target,
            )
        except ApprovalError as exc:
            status_code = 403 if "policy now denies" in str(exc) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        # The grant is spent by the run attempt, success or failure
        # (see approvals.execute_approved), so the pending action
        # is decided from here on either way.
        decisions[pending.id] = "approved"
        incident_store.save_approval(pending, "approved")
        try:
            run_result = execute_approved(executor, pending, grant, allowed)
        except RunbookError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"the approved action could not run: {exc}",
            ) from exc
        incident.add_evidence(run_result.to_evidence())
        if incident.status is IncidentStatus.DIAGNOSED:
            incident.transition(IncidentStatus.MITIGATING)
        incident_store.save(incident)
        return {
            "pending_id": pending.id,
            "decision": "approved",
            "approved_by": grant.approved_by,
            "runbook_id": pending.action.runbook_id,
            "summary": run_result.summary,
            "incident_status": incident.status.value,
        }

    @app.post(
        "/incidents/{incident_id}/approvals/{pending_id}/reject",
        dependencies=guarded,
    )
    def reject_action(
        incident_id: str, pending_id: str, body: ApprovalDecisionRequest
    ) -> dict[str, object]:
        incident = _incident_or_404(incident_id)
        _approvals_or_409()
        if tier3_only:
            raise HTTPException(
                status_code=409,
                detail=(
                    "approvals are disabled in hosted demo mode; "
                    "only the Tier 3 demo grant can run an action"
                ),
            )
        pending = _pending_or_404(incident_id, pending_id)
        if not body.by.strip():
            raise HTTPException(
                status_code=400,
                detail="a rejection must name who is rejecting",
            )
        if pending.id in decisions:
            raise HTTPException(
                status_code=409,
                detail="this approval has already been decided",
            )
        decisions[pending.id] = "rejected"
        incident_store.save_approval(pending, "rejected")
        return {
            "pending_id": pending.id,
            "decision": "rejected",
            "rejected_by": body.by.strip(),
            "runbook_id": pending.action.runbook_id,
            "summary": None,
            "incident_status": incident.status.value,
        }

    return app


# The shipped app is the demo server: it diagnoses new incidents
# from the seeded demo tool layer's data (see tools.demo_tool_layer),
# with the seeded demo read adapters selected as extra evidence
# sources and the seeded demo memory attached as advice, and serves
# live approvals against the seeded demo workloads, under a Tier 2
# policy for the demo environment. It passes no diagnosis backend,
# so its diagnoses come from the deterministic backend — the
# language-model backend is off in the shipped server.
_demo_backend = demo_runbooks()[1]
app = create_app(
    tool_layer=demo_tool_layer(),
    adapters=demo_adapter_sources(),
    memory=demo_incident_memory(),
    approval_policy=EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITH_APPROVAL,
        max_replicas_affected=5,
    ),
    runbook_backend=_demo_backend,
)
