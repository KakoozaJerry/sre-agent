"""Postmortem draft generation: the incident report, from the record.

In plain words, this is the last step of an incident: after the
problem is fixed, someone has to write down what happened. This
module writes the *first draft* of that report, using only what
the incident record itself holds — its evidence, its diagnosis,
the actions recorded on it, and the audit records the caller
hands in for it. A human edits and publishes the draft; nothing
here publishes anything.

How honesty is kept, in plain words:

- **The draft invents nothing.** It is a pure reader of the
  record: it changes no incident, runs no tool and proposes no
  action. If the record holds no diagnosis, the draft says the
  cause was not recorded instead of guessing one. If no action
  was recorded, it says so. What the record does not know is
  listed, in its own section, as not known.
- **The timeline uses only timestamps the record really has.**
  Incidents do not stamp each status change — a transition only
  moves ``updated_at`` — so the timeline is built from the
  incident's creation time, each evidence record's observation
  time, and each audit record's decision time, and it ends with
  the record's last update. No time is made up for a change the
  record did not date.
- **An unfinished incident gets a preliminary draft, labelled
  as one.** A draft can be generated at any point — it is useful
  mid-incident too — but until the incident is resolved the
  draft says plainly that it is not a final postmortem.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from .approvals import approve_pending, create_pending, execute_approved
from .audit import AuditLog, evaluate_and_audit
from .logging_config import get_logger
from .models import AuditRecord, Evidence, Hypothesis, Incident, IncidentStatus, Target
from .policy import EnvironmentPolicy, Tier
from .reasoning import ReasoningLoop
from .runbooks import build_action, demo_runbooks
from .tools import demo_tool_layer

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TimelineEntry(BaseModel):
    """One dated line in the draft's timeline, from the record."""

    at: datetime
    text: str


class PostmortemDraft(BaseModel):
    """A first draft of an incident's postmortem, ready to edit."""

    incident_id: str
    title: str
    target: Target
    status: IncidentStatus
    resolved: bool
    generated_at: datetime = Field(default_factory=_utcnow)
    diagnosis: str | None = None
    timeline: list[TimelineEntry] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    actions: list[Evidence] = Field(default_factory=list)
    audit_records: list[AuditRecord] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)

    def render_markdown(self) -> str:
        """Write the draft as Markdown a human can edit and publish.

        Every section is present even when the record behind it is
        empty; an empty section says so in plain words rather than
        being quietly dropped, so a reader can tell the difference
        between "none" and "not written yet".
        """
        target = self.target
        lines = [
            f"# Postmortem draft: {self.title}",
            "",
            f"Incident: {self.incident_id}",
            (
                f"Target: {target.service} in {target.namespace} "
                f"({target.environment})"
            ),
            f"Status: {self.status.value}",
        ]
        if not self.resolved:
            lines.append(
                "**Preliminary draft — this incident is not resolved yet, "
                "so this is not a final postmortem.**"
            )
        lines += ["", "## Timeline", ""]
        if self.timeline:
            for entry in self.timeline:
                lines.append(f"- {entry.at.isoformat()} — {entry.text}")
        else:
            lines.append("- No dated events were recorded.")
        lines += ["", "## Diagnosis", ""]
        lines.append(
            self.diagnosis
            if self.diagnosis
            else "Not recorded: the record holds no diagnosis, so no cause is stated."
        )
        lines += ["", "## Possible causes, as recorded", ""]
        if self.hypotheses:
            for hyp in self.hypotheses:
                lines.append(
                    f"- {hyp.statement} (confidence {hyp.confidence:.2f}, "
                    f"supported by {len(hyp.supporting_evidence)} evidence record(s))"
                )
        else:
            lines.append("- None were recorded.")
        lines += ["", "## Evidence", ""]
        if self.evidence:
            for ev in self.evidence:
                lines.append(
                    f"- [{ev.kind}] {ev.summary} (query: {ev.query})"
                )
        else:
            lines.append("- No evidence was recorded.")
        lines += ["", "## Actions taken", ""]
        if self.actions:
            for ev in self.actions:
                lines.append(f"- {ev.summary} (query: {ev.query})")
        else:
            lines.append("- No action was recorded on this incident.")
        lines += ["", "## Decisions and approvals (audit log)", ""]
        if self.audit_records:
            for record in self.audit_records:
                approver = (
                    f", approved by {record.approved_by}" if record.approved_by else ""
                )
                lines.append(
                    f"- {record.action_summary} — decision: {record.decision} "
                    f"({record.reason}){approver}"
                )
        else:
            lines.append("- No audit records were supplied for this incident.")
        lines += ["", "## What is not known", ""]
        if self.unknowns:
            for unknown in self.unknowns:
                lines.append(f"- {unknown}")
        else:
            lines.append("- Nothing: the record covers every section above.")
        lines.append("")
        return "\n".join(lines)


def generate_postmortem(
    incident: Incident,
    audit_records: list[AuditRecord] | None = None,
    *,
    now: datetime | None = None,
) -> PostmortemDraft:
    """Build a postmortem draft from *incident*'s own record.

    Pure: the incident is read, never changed, and nothing is run.
    Audit records are taken from the caller's list but filtered to
    this incident, so one incident's draft can never carry another
    incident's decisions.
    """
    records = [r for r in audit_records or [] if r.incident_id == incident.id]
    records.sort(key=lambda r: r.decided_at)

    timeline = [
        TimelineEntry(
            at=incident.created_at,
            text=f"Incident opened: {incident.title}",
        )
    ]
    for ev in sorted(incident.evidence, key=lambda e: e.observed_at):
        timeline.append(
            TimelineEntry(
                at=ev.observed_at,
                text=f"Evidence ({ev.kind}): {ev.summary}",
            )
        )
    for record in records:
        timeline.append(
            TimelineEntry(
                at=record.decided_at,
                text=(
                    f"Decision recorded: {record.action_summary} — "
                    f"{record.decision}"
                ),
            )
        )
    timeline.append(
        TimelineEntry(
            at=incident.updated_at,
            text=f"Record last updated; status is {incident.status.value}.",
        )
    )
    timeline.sort(key=lambda e: e.at)

    unknowns: list[str] = []
    if incident.status is not IncidentStatus.RESOLVED:
        unknowns.append(
            f"The incident is not resolved yet (status: {incident.status.value}); "
            "this draft is preliminary and the ending is not known."
        )
    if not incident.diagnosis:
        unknowns.append(
            "No diagnosis was recorded, so the cause is not stated in this draft."
        )
    if not incident.hypotheses:
        unknowns.append("No possible causes were recorded.")
    if not incident.evidence:
        unknowns.append("No evidence was recorded.")
    actions = [ev for ev in incident.evidence if ev.kind == "action"]
    if not actions:
        unknowns.append("No runbook action was recorded on this incident.")
    if not records:
        unknowns.append("No audit records were supplied for this incident.")

    log.info(
        "postmortem draft generated for incident %s (status %s, %d unknown(s))",
        incident.id,
        incident.status.value,
        len(unknowns),
    )
    return PostmortemDraft(
        incident_id=incident.id,
        title=incident.title,
        target=incident.target,
        status=incident.status,
        resolved=incident.status is IncidentStatus.RESOLVED,
        generated_at=now if now is not None else _utcnow(),
        diagnosis=incident.diagnosis,
        timeline=timeline,
        hypotheses=list(incident.hypotheses),
        evidence=list(incident.evidence),
        actions=actions,
        audit_records=records,
        unknowns=unknowns,
    )


def demo_postmortem() -> PostmortemDraft:
    """A full demo postmortem, built end to end with no setup.

    One seeded demo incident is diagnosed by the real reasoning
    loop, fixed by the real Tier 2 path (propose, approve by name,
    run the restart once), and resolved — then drafted here. The
    draft therefore shows a real diagnosis, a real action record
    and real audit records, all about the demo checkout workload
    and nothing real.
    """
    target = Target(environment="demo", namespace="shop", service="checkout")
    incident = Incident(
        id="demo-incident-1",
        title="HighErrorRate on checkout",
        target=target,
    )
    ReasoningLoop(demo_tool_layer()).diagnose(incident)

    executor, backend = demo_runbooks()
    action = build_action(
        "restart-workload",
        target,
        {},
        backend,
        rationale=incident.diagnosis or "demo diagnosis",
    )
    policy = EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITH_APPROVAL,
        max_replicas_affected=5,
    )
    audit = AuditLog()
    result = evaluate_and_audit(
        action, policy, audit, incident_id=incident.id, incident_target=target
    )
    pending = create_pending(action, result, incident_id=incident.id)
    grant, allowed = approve_pending(
        pending,
        approved_by="Demo Approver",
        policy=policy,
        audit_log=audit,
        incident_target=target,
    )
    incident.transition(IncidentStatus.MITIGATING)
    run_result = execute_approved(executor, pending, grant, allowed)
    incident.add_evidence(run_result.to_evidence())
    incident.transition(IncidentStatus.RESOLVED)
    return generate_postmortem(incident, audit.for_incident(incident.id))
