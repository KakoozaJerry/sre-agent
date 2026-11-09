"""Tests for postmortem draft generation.

The honesty-shaped tests matter most here: a draft states only
what the incident record holds, an unfinished incident is labelled
preliminary, missing sections say so instead of being invented or
dropped, the timeline uses only real recorded times, and one
incident's draft never carries another incident's audit records.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sre_agent.audit import AuditLog, evaluate_and_audit
from sre_agent.cli import main
from sre_agent.models import (
    AuditRecord,
    Evidence,
    Hypothesis,
    Incident,
    IncidentStatus,
    Target,
)
from sre_agent.policy import EnvironmentPolicy, Tier
from sre_agent.postmortems import demo_postmortem, generate_postmortem
from sre_agent.runbooks import build_action, demo_runbooks

CHECKOUT = Target(environment="demo", namespace="shop", service="checkout")
T0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _resolved_incident() -> Incident:
    """A resolved incident with a diagnosis, a cause, and an action."""
    incident = Incident(
        id="inc-1", title="Checkout errors", target=CHECKOUT, created_at=T0,
        updated_at=T0 + timedelta(minutes=30),
    )
    incident.transition(IncidentStatus.TRIAGING)
    ev = Evidence(
        kind="metric", query="http_request_errors_total",
        summary="Error metric is not zero.", observed_at=T0 + timedelta(minutes=5),
    )
    incident.add_evidence(ev)
    incident.hypotheses.append(
        Hypothesis(
            statement="A call checkout depends on is timing out",
            supporting_evidence=[ev.id], confidence=0.85,
        )
    )
    incident.diagnosis = "Most likely: a call checkout depends on is timing out"
    incident.transition(IncidentStatus.DIAGNOSED)
    incident.add_evidence(
        Evidence(
            kind="action", query="runbook:restart-workload on checkout/shop (demo)",
            summary="Restarted checkout: restarts now 1, failing state cleared.",
            observed_at=T0 + timedelta(minutes=20),
        )
    )
    incident.transition(IncidentStatus.MITIGATING)
    incident.transition(IncidentStatus.RESOLVED)
    return incident


def _audit_for(incident_id="inc-1") -> list[AuditRecord]:
    _, backend = demo_runbooks()
    action = build_action(
        "restart-workload", CHECKOUT, {}, backend, rationale="checkout is failing"
    )
    policy = EnvironmentPolicy(
        environment="demo", granted_tier=Tier.ACT_WITH_APPROVAL, max_replicas_affected=5
    )
    log = AuditLog()
    evaluate_and_audit(action, policy, log, incident_id=incident_id,
                       incident_target=CHECKOUT)
    return log.for_incident(incident_id)


# --- the resolved case: every section filled from the record ---


def test_resolved_draft_covers_every_section() -> None:
    draft = generate_postmortem(_resolved_incident(), _audit_for(), now=T0)
    assert draft.resolved and draft.status is IncidentStatus.RESOLVED
    assert draft.unknowns == []
    assert len(draft.actions) == 1 and len(draft.evidence) == 2
    text = draft.render_markdown()
    assert "# Postmortem draft: Checkout errors" in text
    assert "Preliminary" not in text
    assert "Most likely: a call checkout depends on is timing out" in text
    assert "confidence 0.85" in text
    assert "(query: http_request_errors_total)" in text
    assert "Restarted checkout: restarts now 1" in text
    assert "decision: require_approval" in text
    assert "Nothing: the record covers every section above." in text


def test_the_draft_does_not_change_the_incident() -> None:
    incident = _resolved_incident()
    before = (incident.status, len(incident.evidence), incident.updated_at)
    generate_postmortem(incident, _audit_for(), now=T0)
    assert (incident.status, len(incident.evidence), incident.updated_at) == before


def test_timeline_uses_only_recorded_times_in_order() -> None:
    draft = generate_postmortem(_resolved_incident(), _audit_for(), now=T0)
    times = [entry.at for entry in draft.timeline]
    assert times == sorted(times)
    assert draft.timeline[0].text.startswith("Incident opened:")
    assert draft.timeline[0].at == T0
    assert any("Evidence (metric):" in e.text for e in draft.timeline)
    assert any("Decision recorded:" in e.text for e in draft.timeline)
    assert any(e.text.startswith("Record last updated") for e in draft.timeline)


# --- the honesty cases: what the record does not hold ---


def test_unresolved_incident_is_a_labelled_preliminary_draft() -> None:
    incident = Incident(id="inc-2", title="Still broken", target=CHECKOUT)
    incident.transition(IncidentStatus.TRIAGING)
    draft = generate_postmortem(incident, now=T0)
    assert not draft.resolved
    text = draft.render_markdown()
    assert "Preliminary draft — this incident is not resolved yet" in text
    assert any("not resolved yet" in u for u in draft.unknowns)


def test_no_diagnosis_means_no_cause_is_invented() -> None:
    incident = Incident(id="inc-3", title="Mystery", target=CHECKOUT)
    draft = generate_postmortem(incident, now=T0)
    text = draft.render_markdown()
    assert "Not recorded: the record holds no diagnosis" in text
    assert any("cause is not stated" in u for u in draft.unknowns)
    assert "- None were recorded." in text
    assert "- No evidence was recorded." in text
    assert "- No action was recorded on this incident." in text
    assert "- No audit records were supplied for this incident." in text


def test_audit_records_for_other_incidents_are_left_out() -> None:
    incident = _resolved_incident()
    others = _audit_for(incident_id="someone-else")
    draft = generate_postmortem(incident, others, now=T0)
    assert draft.audit_records == []
    assert "No audit records were supplied" in draft.render_markdown()


# --- the demo and the CLI ---


def test_demo_postmortem_is_a_real_end_to_end_record() -> None:
    draft = demo_postmortem()
    assert draft.resolved and draft.incident_id == "demo-incident-1"
    assert draft.diagnosis is not None and "timing out" in draft.diagnosis
    assert len(draft.actions) == 1
    decisions = [r.decision for r in draft.audit_records]
    assert decisions == ["require_approval", "allow"]
    assert draft.audit_records[-1].approved_by == "Demo Approver"
    text = draft.render_markdown()
    assert "Restarted checkout" in text
    assert "approved by Demo Approver" in text


def test_cli_postmortem_demo_prints_the_draft(capsys) -> None:
    assert main(["postmortem-demo"]) == 0
    out = capsys.readouterr().out
    assert "# Postmortem draft: HighErrorRate on checkout" in out
    assert "## Actions taken" in out and "Restarted checkout" in out


def test_cli_postmortem_demo_takes_no_extra_arguments(capsys) -> None:
    assert main(["postmortem-demo", "extra"]) == 1
    assert "takes no extra arguments" in capsys.readouterr().err
