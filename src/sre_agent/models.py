"""Core data models: incidents, evidence, proposed actions, audit records.

The incident is the spine of the system. Its target (environment, namespace,
service) is pinned at creation time and cannot be changed afterwards: an
action proposed for one target can never be redirected to another.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(UTC)


class IncidentStatus(str, Enum):
    OPEN = "open"
    TRIAGING = "triaging"
    DIAGNOSED = "diagnosed"
    MITIGATING = "mitigating"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


# The only legal moves through the incident life cycle. Anything else is a
# programming error and raises, rather than silently corrupting the record.
_ALLOWED_TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    IncidentStatus.OPEN: {IncidentStatus.TRIAGING, IncidentStatus.ESCALATED},
    IncidentStatus.TRIAGING: {
        IncidentStatus.DIAGNOSED,
        IncidentStatus.ESCALATED,
        IncidentStatus.RESOLVED,  # alert auto-recovered before diagnosis
    },
    IncidentStatus.DIAGNOSED: {
        IncidentStatus.MITIGATING,
        IncidentStatus.ESCALATED,
        IncidentStatus.RESOLVED,
    },
    IncidentStatus.MITIGATING: {
        IncidentStatus.RESOLVED,
        IncidentStatus.TRIAGING,  # fix did not work: back to diagnosis
        IncidentStatus.ESCALATED,
    },
    IncidentStatus.ESCALATED: {IncidentStatus.TRIAGING, IncidentStatus.RESOLVED},
    IncidentStatus.RESOLVED: set(),
}


class Target(BaseModel):
    """Where an incident is. Pinned at creation; immutable by design."""

    environment: str
    namespace: str
    service: str

    model_config = {"frozen": True}


class Evidence(BaseModel):
    """One observed fact, with the query that produced it."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    kind: str  # e.g. "metric", "log", "event", "change"
    query: str  # the exact query or command that produced this
    summary: str
    observed_at: datetime = Field(default_factory=_utcnow)


class DiagnosisClaim(BaseModel):
    """One observed fact in a diagnosis, backed by evidence.

    A claim without evidence is not a claim this project will
    make, so the model itself refuses to build one. Defined here,
    with the other core models, because a diagnosis backend
    (``model_backend.py``) returns claims and the reasoning loop
    cannot be imported by the backends it calls; ``reasoning.py``
    re-exports this class, so existing imports keep working.
    """

    statement: str
    evidence_ids: list[str]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("evidence_ids")
    @classmethod
    def _claim_must_cite_evidence(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("a diagnosis claim must cite at least one evidence record")
        return v


class Hypothesis(BaseModel):
    """A possible cause, tied to the evidence for and against it."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    statement: str
    supporting_evidence: list[str] = Field(default_factory=list)  # Evidence ids
    contradicting_evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PastIncidentMatch(BaseModel):
    """One past incident that looks like the query, and why.

    Defined here, with the other core models, because a match is
    part of the memory report an incident record can carry (see
    ``Incident.memory_report``); ``memory.py`` builds them.
    """

    incident_id: str
    title: str
    target: Target
    status: IncidentStatus
    diagnosis: str | None = None
    score: int
    reasons: list[str] = Field(default_factory=list)
    runbooks_run: list[str] = Field(default_factory=list)


class RunbookStats(BaseModel):
    """One runbook's record, counted from incident action records."""

    runbook_id: str
    attempts: int = 0
    ended_resolved: int = 0
    not_resolved: int = 0

    @property
    def success_rate(self) -> float:
        """Share of runs whose incident ended resolved (0 if none)."""
        return self.ended_resolved / self.attempts if self.attempts else 0.0


class RunbookSuggestion(BaseModel):
    """Advice only: a runbook worth considering, and its record.

    Carrying a suggestion changes nothing and runs nothing; any
    fix still goes through the policy engine and its tier gate.
    """

    runbook_id: str
    attempts_on_similar: int
    ended_resolved_on_similar: int
    success_rate_on_similar: float
    supporting_incident_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class MemoryReport(BaseModel):
    """What memory can say about one incident, in one report.

    Advice only, always: a report attached to an incident is
    computed after that incident's diagnosis is decided, and it
    is never an input to a claim, a cause, a proposal or an
    action (see ``memory.py`` and ``reasoning.py``).
    """

    query_incident_id: str
    query_target: Target
    past_incidents_held: int
    matches: list[PastIncidentMatch] = Field(default_factory=list)
    overall_stats: list[RunbookStats] = Field(default_factory=list)
    suggestions: list[RunbookSuggestion] = Field(default_factory=list)

    def render_text(self) -> str:
        """The report as plain text a human can check."""
        target = self.query_target
        lines = [
            (
                "Memory report (past incidents held in memory; advice only — "
                "nothing here runs or approves a fix)"
            ),
            (
                f"Query: incident {self.query_incident_id} — "
                f"{target.service}/{target.namespace} ({target.environment})"
            ),
            f"Past incidents held: {self.past_incidents_held}",
            "Similar past incidents, most similar first:",
        ]
        if not self.matches:
            lines.append("- None similar enough to report.")
        for match in self.matches:
            lines.append(
                f"- {match.incident_id} [{match.status.value}] score {match.score}: "
                f"{match.title} — {'; '.join(match.reasons)}"
            )
            if match.diagnosis:
                lines.append(f"  diagnosis then: {match.diagnosis}")
            if match.runbooks_run:
                lines.append(f"  runbook(s) run then: {', '.join(match.runbooks_run)}")
        lines.append("Runbook record across all past incidents held:")
        if not self.overall_stats:
            lines.append("- No runbook runs are recorded on any past incident.")
        for stats in self.overall_stats:
            lines.append(
                f"- {stats.runbook_id}: {stats.attempts} run(s), "
                f"{stats.ended_resolved} on incidents that ended resolved, "
                f"{stats.not_resolved} not resolved "
                f"(resolved share {stats.success_rate:.0%})"
            )
        lines.append(
            "Suggested runbooks for this incident (from same-service past incidents only):"
        )
        if not self.suggestions:
            lines.append("- None: no similar past incident ran a runbook.")
        for suggestion in self.suggestions:
            lines.append(f"- {suggestion.runbook_id}: {suggestion.reason}")
        lines.append(
            "Note: 'ended resolved' means the incident's final status was "
            "resolved after the run was recorded; it does not by itself "
            "prove the run caused the resolution."
        )
        return "\n".join(lines)


class Incident(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    title: str
    target: Target
    status: IncidentStatus = IncidentStatus.OPEN
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    evidence: list[Evidence] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    diagnosis: str | None = None
    # Past-incident advice attached after a diagnosis, when the
    # reasoning loop was built with a memory (Phase 7). Advice
    # only: it is computed after the diagnosis is decided, and
    # nothing reads it to propose or run an action.
    memory_report: MemoryReport | None = None

    @field_validator("target")
    @classmethod
    def _target_must_be_complete(cls, v: Target) -> Target:
        if not (v.environment and v.namespace and v.service):
            raise ValueError("incident target must name environment, namespace and service")
        return v

    def transition(self, to: IncidentStatus) -> None:
        if to not in _ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"illegal incident transition: {self.status} -> {to}")
        self.status = to
        self.updated_at = _utcnow()

    def add_evidence(self, ev: Evidence) -> None:
        self.evidence.append(ev)
        self.updated_at = _utcnow()


class AuditRecord(BaseModel):
    """Written before any action executes, and for denied actions too."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    incident_id: str
    action_summary: str
    decision: str  # "allow" | "require_approval" | "deny"
    reason: str
    decided_at: datetime = Field(default_factory=_utcnow)
    approved_by: str | None = None
    # SHA-256 of the action's parameters: enough to check later that the
    # action that ran is the action that was decided, without storing
    # any parameter values (which may be secrets) in the log.
    parameters_hash: str = ""
    # The hash chain (finding F-04): each record carries the hash of
    # the record before it (``prev_hash``; the first record in a log
    # carries the genesis value in ``audit.py``) and its own hash
    # (``record_hash``), computed over every other field including
    # ``prev_hash``. Editing, deleting or reordering a record breaks
    # the chain, and ``audit.verify_audit_records`` reports where.
    # Records written before the chain existed carry empty hashes and
    # are reported as unchained by that check, never silently passed.
    prev_hash: str = ""
    record_hash: str = ""
