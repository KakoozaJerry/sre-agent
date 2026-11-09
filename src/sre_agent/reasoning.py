"""Reasoning loop v1: turn gathered evidence into a diagnosis.

In plain words, this is the part that looks at an incident and
writes down what it thinks is wrong — and *why* it thinks so.

How v1 works, honestly:

- **No model call.** V1 is a small set of plain rules over the
  three read-only tools, not a language model. That is on
  purpose: anyone can run it with no API key, and the tests can
  check every single diagnosis exactly, word for word. A model
  backend can replace the ranking rules later, behind the same
  report shape (the plan, Section 5, now says this).
- **A fixed, bounded plan.** The loop runs at most four read-only
  queries, in a set order: service status, error logs, the error
  metric, then the request metric. Each query counts as one
  step against a step budget. The same query is never run
  twice in one diagnosis. If the budget runs out before the
  plan finishes, the loop stops gathering and escalates.
- **Every claim cites evidence.** A claim or a hypothesis with
  no evidence behind it is refused by its own data model, and
  the loop checks again that every cited id is a real evidence
  record on the incident before it writes anything down.
- **"I don't know" is a normal answer.** If the gathered
  evidence shows no clear problem — the service looks healthy,
  there are no error logs, and the error metric is quiet — the
  loop says so, sets no diagnosis, and moves the incident to
  "escalated" for a human. Guessing confidently is a bug here.
- **Telemetry is data, never orders.** Log lines can say
  anything, including attacks. Claims in the report are built
  from counts, statuses and measured values only; raw log text
  is never copied into a claim, and nothing in a report can
  propose or run an action. Fixes come in a later phase,
  through the policy engine and a human approval.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator

from .logging_config import get_logger
from .models import Evidence, Hypothesis, Incident, IncidentStatus, Target
from .tools import (
    QueryMetricsRequest,
    QueryMetricsResult,
    SearchLogsRequest,
    SearchLogsResult,
    ServiceStatusResult,
    ToolLayer,
    demo_tool_layer,
)

log = get_logger(__name__)

# The fixed evidence plan: these two metrics, in this order.
ERROR_METRIC = "http_request_errors_total"
REQUEST_METRIC = "http_requests_total"
DEFAULT_MAX_STEPS = 25


class DiagnosisOutcome(str, Enum):
    DIAGNOSED = "diagnosed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class DiagnosisClaim(BaseModel):
    """One observed fact in a diagnosis, backed by evidence.

    A claim without evidence is not a claim this project will
    make, so the model itself refuses to build one.
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


class DiagnosisReport(BaseModel):
    """The full result of one run of the reasoning loop."""

    incident_id: str
    outcome: DiagnosisOutcome
    summary: str
    claims: list[DiagnosisClaim] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    gathered_evidence: list[Evidence] = Field(default_factory=list)
    steps_used: int = 0
    queries_run: list[str] = Field(default_factory=list)

    def render_text(self) -> str:
        """Write the report as plain text a human can check.

        Every claim is printed with the exact query behind the
        evidence it cites, so a reader can re-run that query and
        see the same thing the agent saw.
        """
        by_id = {ev.id: ev for ev in self.gathered_evidence}
        lines = [
            f"Diagnosis for incident {self.incident_id}",
            f"Outcome: {self.outcome.value}",
            f"Summary: {self.summary}",
            f"Steps used: {self.steps_used}",
            "Claims (each one backed by the evidence named under it):",
        ]
        if not self.claims:
            lines.append("- No claims: the evidence did not support a diagnosis.")
        for claim in self.claims:
            lines.append(f"- {claim.statement} (confidence {claim.confidence:.2f})")
            for ev_id in claim.evidence_ids:
                ev = by_id.get(ev_id)
                if ev is not None:
                    lines.append(f"  evidence: {ev.query}")
        if self.hypotheses:
            lines.append("Possible causes, most likely first:")
            for hyp in self.hypotheses:
                lines.append(
                    f"- {hyp.statement} (confidence {hyp.confidence:.2f}, "
                    f"supported by {len(hyp.supporting_evidence)} evidence record(s))"
                )
        return "\n".join(lines)


class ReasoningLoop:
    """The bounded, deterministic reasoning loop."""

    def __init__(self, tools: ToolLayer, max_steps: int = DEFAULT_MAX_STEPS) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self._tools = tools
        self._max_steps = max_steps

    def diagnose(self, incident: Incident) -> DiagnosisReport:
        """Gather evidence for *incident* and write a diagnosis.

        Moves the incident to "diagnosed" when the evidence
        supports one, or to "escalated" when it does not (or the
        step budget ran out). Any other starting state is a
        programming error and raises.
        """
        if incident.status is IncidentStatus.OPEN:
            incident.transition(IncidentStatus.TRIAGING)
        if incident.status is not IncidentStatus.TRIAGING:
            raise ValueError(
                f"cannot diagnose an incident in status {incident.status.value!r}"
            )

        gathered: list[Evidence] = []
        queries_run: list[str] = []
        seen_queries: set[str] = set()
        steps = 0
        budget_exhausted = False

        def run_once(key: str) -> bool:
            """Count one step. False means: stop, budget is gone.

            The same query key is never counted or run twice.
            """
            nonlocal steps, budget_exhausted
            if key in seen_queries:
                return True
            if steps >= self._max_steps:
                budget_exhausted = True
                return False
            seen_queries.add(key)
            queries_run.append(key)
            steps += 1
            return True

        target = incident.target

        # Step 1: is the service itself up, degraded, or down?
        status_result = None
        status_ev: Evidence | None = None
        if run_once(f"service status: {target.service}"):
            status_result = self._tools.get_service_status(target)
            status_ev = status_result.to_evidence()
            gathered.append(status_ev)
            incident.add_evidence(status_ev)

        # Step 2: what do its error logs say (in counts, not words)?
        logs_result = None
        logs_ev: Evidence | None = None
        if not budget_exhausted and run_once(f"error logs: {target.service}"):
            logs_result = self._tools.search_logs(
                SearchLogsRequest(
                    service=target.service,
                    namespace=target.namespace,
                    level="ERROR",
                )
            )
            logs_ev = logs_result.to_evidence()
            gathered.append(logs_ev)
            incident.add_evidence(logs_ev)

        # Step 3: are errors showing up in the metrics?
        errors_result = None
        errors_ev: Evidence | None = None
        if not budget_exhausted and run_once(f"promql: {ERROR_METRIC}"):
            errors_result = self._tools.query_metrics(
                QueryMetricsRequest(query=ERROR_METRIC)
            )
            errors_ev = errors_result.to_evidence()
            gathered.append(errors_ev)
            incident.add_evidence(errors_ev)

        # Step 4: how much traffic is there, for context? It is
        # gathered and stored, but it never creates a claim or a
        # hypothesis on its own: traffic alone diagnoses nothing.
        if not budget_exhausted and run_once(f"promql: {REQUEST_METRIC}"):
            requests_ev = self._tools.query_metrics(
                QueryMetricsRequest(query=REQUEST_METRIC)
            ).to_evidence()
            gathered.append(requests_ev)
            incident.add_evidence(requests_ev)

        claims, hypotheses = self._reason(
            incident=incident,
            status_result=status_result,
            status_ev=status_ev,
            logs_result=logs_result,
            logs_ev=logs_ev,
            errors_result=errors_result,
            errors_ev=errors_ev,
        )

        # Final honesty check: nothing may cite evidence that is
        # not really on this incident.
        known_ids = {ev.id for ev in incident.evidence}
        for claim in claims:
            missing = [i for i in claim.evidence_ids if i not in known_ids]
            if missing:
                raise ValueError(f"claim cites evidence not on the incident: {missing}")
        for hyp in hypotheses:
            missing = [i for i in hyp.supporting_evidence if i not in known_ids]
            if missing:
                raise ValueError(
                    f"hypothesis cites evidence not on the incident: {missing}"
                )

        if budget_exhausted:
            summary = (
                "I don't know: the step budget ran out before the evidence "
                "plan finished, so this needs a human."
            )
            incident.transition(IncidentStatus.ESCALATED)
            outcome = DiagnosisOutcome.INSUFFICIENT_EVIDENCE
            # A half-finished plan supports no claims at all, even
            # ones the gathered part suggested on its own.
            claims = []
            hypotheses = []
        elif hypotheses:
            summary = f"Most likely: {hypotheses[0].statement}"
            incident.diagnosis = summary
            incident.transition(IncidentStatus.DIAGNOSED)
            outcome = DiagnosisOutcome.DIAGNOSED
        else:
            summary = (
                "I don't know: the evidence gathered shows no clear problem, "
                "so this needs a human rather than a guess."
            )
            incident.transition(IncidentStatus.ESCALATED)
            outcome = DiagnosisOutcome.INSUFFICIENT_EVIDENCE

        incident.hypotheses.extend(hypotheses)

        log.info(
            "diagnosed incident %s -> %s in %d step(s)",
            incident.id,
            outcome.value,
            steps,
        )
        return DiagnosisReport(
            incident_id=incident.id,
            outcome=outcome,
            summary=summary,
            claims=claims,
            hypotheses=hypotheses,
            gathered_evidence=gathered,
            steps_used=steps,
            queries_run=queries_run,
        )

    def _reason(
        self,
        *,
        incident: Incident,
        status_result: ServiceStatusResult | None,
        status_ev: Evidence | None,
        logs_result: SearchLogsResult | None,
        logs_ev: Evidence | None,
        errors_result: QueryMetricsResult | None,
        errors_ev: Evidence | None,
    ) -> tuple[list[DiagnosisClaim], list[Hypothesis]]:
        """Apply the plain rules. Claims are facts; hypotheses are
        possible causes. Both cite the evidence they came from."""
        claims: list[DiagnosisClaim] = []
        hypotheses: list[Hypothesis] = []
        target = incident.target

        if status_result is not None and status_ev is not None:
            status = status_result.status
            if status_result.found and status is not None and status.status in {
                "degraded",
                "down",
            }:
                claims.append(
                    DiagnosisClaim(
                        statement=(
                            f"Service {target.service} is {status.status}: "
                            f"{status.ready_replicas}/{status.desired_replicas} "
                            "replicas ready"
                        ),
                        evidence_ids=[status_ev.id],
                        confidence=0.9 if status.status == "down" else 0.8,
                    )
                )
                if status.ready_replicas < status.desired_replicas:
                    hypotheses.append(
                        Hypothesis(
                            statement=(
                                f"Fewer copies of {target.service} are ready than "
                                "wanted, so a copy may have failed or be stuck"
                            ),
                            supporting_evidence=[status_ev.id],
                            confidence=0.65,
                        )
                    )

        timeout_seen = False
        if logs_result is not None and logs_ev is not None and logs_result.total_found > 0:
            claims.append(
                DiagnosisClaim(
                    statement=(
                        f"{logs_result.total_found} error log line(s) were found "
                        f"for service {target.service}"
                    ),
                    evidence_ids=[logs_ev.id],
                    confidence=0.85,
                )
            )
            messages = " ".join(e.message.lower() for e in logs_result.entries)
            timeout_seen = "timeout" in messages or "timed out" in messages
            if timeout_seen:
                hypotheses.append(
                    Hypothesis(
                        statement=(
                            f"A call that {target.service} depends on is timing out"
                        ),
                        supporting_evidence=[logs_ev.id],
                        confidence=0.75,
                    )
                )
            else:
                hypotheses.append(
                    Hypothesis(
                        statement=(
                            f"{target.service} is failing on some requests; "
                            "the error logs are the starting point"
                        ),
                        supporting_evidence=[logs_ev.id],
                        confidence=0.6,
                    )
                )

        errors_seen = False
        if errors_result is not None and errors_ev is not None:
            last_values = [
                s.samples[-1].value for s in errors_result.series if s.samples
            ]
            if last_values and max(last_values) > 0:
                errors_seen = True
                claims.append(
                    DiagnosisClaim(
                        statement=(
                            f"The error metric {ERROR_METRIC} is not zero: "
                            f"latest value {max(last_values):g}"
                        ),
                        evidence_ids=[errors_ev.id],
                        confidence=0.85,
                    )
                )

        # Strengthen the timeout hypothesis when the error metric
        # agrees with the error logs: two independent sources.
        if timeout_seen and errors_seen and errors_ev is not None:
            for hyp in hypotheses:
                if "timing out" in hyp.statement:
                    if errors_ev.id not in hyp.supporting_evidence:
                        hyp.supporting_evidence.append(errors_ev.id)
                    hyp.confidence = 0.85

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return claims, hypotheses[:3]


def demo_diagnosis() -> DiagnosisReport:
    """Run the loop once on the seeded demo data, with no setup.

    The demo incident is the checkout service from
    ``demo_tool_layer``: degraded, timing out, with errors in its
    metrics. The report claims nothing about any real system.
    """
    incident = Incident(
        title="HighErrorRate on checkout",
        target=Target(environment="demo", namespace="shop", service="checkout"),
    )
    incident.transition(IncidentStatus.TRIAGING)
    return ReasoningLoop(demo_tool_layer()).diagnose(incident)
