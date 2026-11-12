"""Reasoning loop v1: turn gathered evidence into a diagnosis.

In plain words, this is the part that looks at an incident and
writes down what it thinks is wrong — and *why* it thinks so.

How v1 works, honestly:

- **A backend reads the evidence, and the default is plain
  rules.** The loop gathers evidence the same way no matter
  what, then hands it to a diagnosis backend (see
  ``model_backend.py``). The default backend is deterministic:
  a small set of plain rules, no model call, no API key, so
  the tests can check every diagnosis exactly, word for word.
  An operator may instead hand the loop a language-model
  backend built around their own model client — off by
  default, enabled only by building it in code and passing it
  in, with no client shipped in this repository. Whichever
  backend runs, the report has the same shape and names the
  backend, and the loop's own checks below apply to both: a
  claim or cause citing evidence that is not on the incident
  is refused, and a backend that returns no causes gets the
  same "I don't know" escalation. The deterministic backend
  reads counts, statuses and measured values only; the model
  backend's reply is checked as untrusted input before any of
  it can become a claim (``model_backend.py`` lists the rules).
- **A fixed, bounded plan.** The loop runs at most four read-only
  queries, in a set order: service status, error logs, the error
  metric, then the request metric. Each query counts as one
  step against a step budget. The same query is never run
  twice in one diagnosis. If the budget runs out before the
  plan finishes, the loop stops gathering and escalates.
- **Selected adapters are extra steps, never a new plan.**
  An operator may select read adapters (Kubernetes, Datadog,
  Azure Monitor — see ``adapters.py``) as extra evidence
  sources. Each selected adapter is asked exactly one fixed
  question, after the four core questions, and counts as one
  more step. Adapter questions are built only from the
  incident's target names, and only when a name is a plain
  service or namespace name — a name carrying query syntax is
  never built into a Datadog or KQL question, and that source
  is skipped for that incident. Adapters are extras, so the
  rules differ from the core plan in one way: if the step
  budget runs out before an adapter step, that step is simply
  skipped and the core diagnosis stands, and an adapter that
  fails is skipped with the failure logged. Adapter answers
  are read as counts and measured values only, exactly like
  the core tools — raw adapter text never becomes a claim.
- **Memory advice is attached last, and changes nothing.**
  An operator may hand the loop a memory of past incidents
  (``memory.py``). After the diagnosis above is decided — never
  before, never as an input to it — the loop asks that memory
  for its report on this incident and attaches it to the
  incident and to this report: similar past incidents, each
  runbook's record, and suggested runbooks, as advice only.
  The advice proposes nothing and runs nothing; any suggested
  fix still goes through the policy engine and a human
  approval like any other. A memory that fails is skipped
  with the failure logged, exactly like a failing adapter —
  advice can never discard a diagnosis.
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
  anything, including attacks. The default backend's claims
  are built from counts, statuses and measured values only;
  raw log text is never copied into a claim, and nothing in a
  report can propose or run an action. Fixes come in a later
  phase, through the policy engine and a human approval.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field

from .adapters import (
    AdapterSources,
    AzureQueryResult,
    KqlQueryRequest,
    KubernetesPodsResult,
    ListPodsRequest,
)
from .logging_config import get_logger
from .memory import IncidentMemory
from .model_backend import (
    BackendContext,
    DeterministicBackend,
    DiagnosisBackend,
)
from .models import (
    DiagnosisClaim,
    Evidence,
    Hypothesis,
    Incident,
    IncidentStatus,
    MemoryReport,
    Target,
)
from .tools import (
    QueryMetricsRequest,
    QueryMetricsResult,
    SearchLogsRequest,
    ToolLayer,
    demo_tool_layer,
)

log = get_logger(__name__)

# The fixed evidence plan: these two metrics, in this order.
ERROR_METRIC = "http_request_errors_total"
REQUEST_METRIC = "http_requests_total"
DEFAULT_MAX_STEPS = 25

# A name that may be built into a fixed adapter question: a plain
# service name — letters, digits, dots, underscores, dashes — and
# nothing else. A target name carrying spaces, quotes or query
# syntax fails this check, and the Datadog and Azure steps are
# skipped for that incident rather than built from it.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class DiagnosisOutcome(str, Enum):
    DIAGNOSED = "diagnosed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


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
    # Which backend produced the claims and causes above:
    # "deterministic" (the default) or "language-model". The
    # shape is the same either way; the name is recorded so a
    # score can always be tied to the backend that earned it.
    backend_name: str = "deterministic"
    # Past-incident advice, attached after the diagnosis was
    # decided when the loop was built with a memory. Advice
    # only: it influenced no claim, cause or action above.
    memory_report: MemoryReport | None = None

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
            f"Backend: {self.backend_name}",
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
        if self.memory_report is not None:
            report = self.memory_report
            lines.append(
                "Memory advice (advice only — it changed no claim, "
                "cause or action):"
            )
            if not report.matches:
                lines.append("- No similar past incident was found.")
            for match in report.matches:
                lines.append(
                    f"- Past incident {match.incident_id} "
                    f"[{match.status.value}] score {match.score}: "
                    f"{match.title}"
                )
            if not report.suggestions:
                lines.append(
                    "- No runbook is suggested: no similar past "
                    "incident ran one."
                )
            for suggestion in report.suggestions:
                lines.append(
                    f"- Suggested runbook {suggestion.runbook_id}: "
                    f"{suggestion.reason}"
                )
        return "\n".join(lines)


class ReasoningLoop:
    """The bounded reasoning loop, over a pluggable backend.

    Gathering is the loop's; reading the gathered evidence is
    the backend's. With no backend given, the deterministic
    backend runs — the default, everywhere this loop is used.
    """

    def __init__(
        self,
        tools: ToolLayer,
        max_steps: int = DEFAULT_MAX_STEPS,
        adapters: AdapterSources | None = None,
        memory: IncidentMemory | None = None,
        backend: DiagnosisBackend | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self._tools = tools
        self._max_steps = max_steps
        self._adapters = adapters
        self._memory = memory
        self._backend: DiagnosisBackend = (
            backend if backend is not None else DeterministicBackend()
        )

    @property
    def backend_name(self) -> str:
        """The name of the backend this loop diagnoses with."""
        return self._backend.name

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

        def run_adapter_once(key: str) -> bool:
            """Count one adapter step. False means: skip this
            source — the budget is gone, but adapters are extras,
            so skipping one never discards the core diagnosis the
            way an unfinished core plan does."""
            nonlocal steps
            if key in seen_queries:
                return True
            if steps >= self._max_steps:
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

        # Extra steps, only for adapters the operator selected.
        # Each is one fixed question. A source that fails, or a
        # target name that may not be built into its question,
        # skips that source only — the diagnosis proceeds with
        # whatever the other sources said.
        adapters = self._adapters
        pods_result: KubernetesPodsResult | None = None
        pods_ev: Evidence | None = None
        datadog_result: QueryMetricsResult | None = None
        datadog_ev: Evidence | None = None
        azure_result: AzureQueryResult | None = None
        azure_ev: Evidence | None = None
        if adapters is not None and not budget_exhausted:
            if adapters.kubernetes is not None and run_adapter_once(
                f"kubernetes pods: {target.namespace}/{target.service}"
            ):
                try:
                    pods_result = adapters.kubernetes.list_pods(
                        ListPodsRequest(
                            namespace=target.namespace, service=target.service
                        )
                    )
                    pods_ev = pods_result.to_evidence()
                    gathered.append(pods_ev)
                    incident.add_evidence(pods_ev)
                except Exception:
                    log.exception(
                        "kubernetes adapter failed for incident %s; "
                        "that source is skipped",
                        incident.id,
                    )
                    pods_result = None
                    pods_ev = None
            if adapters.datadog is not None:
                if not _SAFE_NAME_RE.match(target.service):
                    log.warning(
                        "datadog adapter skipped for incident %s: the "
                        "service name is not a plain name",
                        incident.id,
                    )
                elif run_adapter_once(f"datadog: {ERROR_METRIC}"):
                    try:
                        datadog_result = adapters.datadog.query_metrics(
                            QueryMetricsRequest(
                                query=(
                                    f"sum:{ERROR_METRIC}"
                                    f"{{service:{target.service}}}"
                                )
                            )
                        )
                        datadog_ev = datadog_result.to_evidence()
                        gathered.append(datadog_ev)
                        incident.add_evidence(datadog_ev)
                    except Exception:
                        log.exception(
                            "datadog adapter failed for incident %s; "
                            "that source is skipped",
                            incident.id,
                        )
                        datadog_result = None
                        datadog_ev = None
            if adapters.azure is not None:
                if not _SAFE_NAME_RE.match(target.service):
                    log.warning(
                        "azure adapter skipped for incident %s: the "
                        "service name is not a plain name",
                        incident.id,
                    )
                elif run_adapter_once("azure kql: error rows"):
                    try:
                        azure_result = adapters.azure.query(
                            KqlQueryRequest(
                                query=(
                                    "AppTraces | where "
                                    f"Service == '{target.service}' "
                                    "and Level == 'Error'"
                                )
                            )
                        )
                        azure_ev = azure_result.to_evidence()
                        gathered.append(azure_ev)
                        incident.add_evidence(azure_ev)
                    except Exception:
                        log.exception(
                            "azure adapter failed for incident %s; "
                            "that source is skipped",
                            incident.id,
                        )
                        azure_result = None
                        azure_ev = None

        claims, hypotheses = self._backend.reason(
            BackendContext(
                incident=incident,
                evidence=list(gathered),
                status_result=status_result,
                status_ev=status_ev,
                logs_result=logs_result,
                logs_ev=logs_ev,
                errors_result=errors_result,
                errors_ev=errors_ev,
                pods_result=pods_result,
                pods_ev=pods_ev,
                datadog_result=datadog_result,
                datadog_ev=datadog_ev,
                azure_result=azure_result,
                azure_ev=azure_ev,
            )
        )
        # The same shaping for every backend: causes most
        # likely first, at most three of them.
        hypotheses = sorted(
            hypotheses, key=lambda h: h.confidence, reverse=True
        )[:3]

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

        # Memory advice comes last, on purpose: the outcome,
        # claims and causes above are already decided, so the
        # advice cannot have influenced them, and a memory that
        # fails is skipped rather than discarding the diagnosis.
        memory_report: MemoryReport | None = None
        if self._memory is not None:
            try:
                memory_report = self._memory.report(incident)
                incident.memory_report = memory_report
            except Exception:
                log.exception(
                    "memory failed for incident %s; its advice is "
                    "skipped and the diagnosis stands",
                    incident.id,
                )
                memory_report = None

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
            backend_name=self._backend.name,
            memory_report=memory_report,
        )

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
