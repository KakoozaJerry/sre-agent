"""Core data models: incidents, evidence, proposed actions, audit records.

The incident is the spine of the system. Its target (environment, namespace,
service) is pinned at creation time and cannot be changed afterwards: an
action proposed for one target can never be redirected to another.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


class Hypothesis(BaseModel):
    """A possible cause, tied to the evidence for and against it."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    statement: str
    supporting_evidence: list[str] = Field(default_factory=list)  # Evidence ids
    contradicting_evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


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
