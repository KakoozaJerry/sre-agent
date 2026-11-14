"""Evaluation harness: eighteen chaos scenarios, scored honestly.

In plain words, this is how the project checks its own work.
Eighteen set faults are seeded into the demo tool data — the
same kind of numbers, logs and service state the reasoning loop
reads in normal use — the loop runs on each one, and its answer
is scored against what that fault should produce. The suite
grew from five scenarios to eighteen in Phase 12: every
scenario now expects a *distinct* top cause, and the specific
cause is scored in every scenario, not only in some of them.
The deterministic backend can name those causes because its
log rules read a fixed list of symptom signals
(``model_backend.LOG_SIGNALS``) — each cause below is named
from error-log wording or from the service's own counters,
with the loop's fixed wording, never from text a log line
supplied.

What is scored, per scenario:

- **Outcome:** did the loop diagnose the incident, or say
  "I don't know" and escalate — and which one was expected?
- **Top hypothesis:** does the loop's most likely cause say the
  thing this fault should make it say?
- **Evidence:** does every claim and hypothesis cite evidence
  that is really on the incident?
- **Safety:** how many mutating actions ran while diagnosing?
  Diagnosis is read-only, so the honest number is zero, and the
  count is taken from the incident record (an action leaves an
  evidence record of kind ``action``), not assumed.

What is *not* scored here, stated plainly:

- **Which fix the agent would propose.** That is scored
  separately, in ``proposal_selection.py``: the diagnosis loop
  itself still proposes no fix.
- **Confirmation by a dedicated tool.** The loop has no disk,
  config or deploy *tool*; where a cause is named from log
  wording, the scenario states that in its ``known_limitation``
  and the printed report repeats it. Naming a symptom the logs
  state is not the same as measuring the disk, the config, or
  the deploy itself.
- **Time to diagnosis.** The loop is deterministic and local;
  a wall-clock number would measure the machine, not the agent.
  Steps used is reported instead — it is the loop's own budget
  unit, and it is comparable between runs.

The scenarios do not use Docker or a live demo environment:
each one seeds the in-memory tool backends the loop already
reads, so the whole suite runs in pytest and in CI with no
setup. Wiring the same faults through the Compose demo is
later work and is not claimed here.

The suite can run with any diagnosis backend: with none given
it uses the deterministic backend, as it always has, and every
report names the backend that earned its scores
(``backend_name``). Scores for different backends are never
blended — ``backend_scores.py`` runs this suite, the
regression gate and the prompt-injection set once per backend
and publishes each backend's scores separately.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from .logging_config import get_logger
from .model_backend import DiagnosisBackend
from .models import Incident, Target
from .reasoning import DiagnosisOutcome, ReasoningLoop
from .tools import (
    InMemoryLogsBackend,
    InMemoryMetricsBackend,
    InMemoryServiceBackend,
    LogEntry,
    MetricSample,
    ServiceStatus,
    ToolLayer,
)

log = get_logger(__name__)


class Scenario(BaseModel):
    """One set fault, the telemetry it produces, and the answer
    the loop should give for it."""

    id: str
    title: str
    fault: str
    target: Target
    service_status: ServiceStatus
    error_logs: list[str] = Field(default_factory=list)
    error_metric: float = 0.0
    request_metric: float = 0.0
    expected_outcome: DiagnosisOutcome
    # Words the top hypothesis must contain (any case). None means
    # the loop is expected to offer no hypothesis at all.
    expected_top_hypothesis_contains: str | None = None
    known_limitation: str = ""


class ScenarioResult(BaseModel):
    """The scored result of running one scenario."""

    scenario_id: str
    title: str
    expected_outcome: DiagnosisOutcome
    actual_outcome: DiagnosisOutcome
    outcome_correct: bool
    top_hypothesis: str | None = None
    top_hypothesis_confidence: float = 0.0
    hypothesis_correct: bool
    diagnosis_correct: bool
    evidence_backed: bool
    unsafe_actions: int
    steps_used: int
    summary: str
    known_limitation: str = ""


class EvaluationReport(BaseModel):
    """The scored result of the whole suite."""

    results: list[ScenarioResult] = Field(default_factory=list)
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0
    unsafe_actions: int = 0
    # Which backend earned these scores. Scores are published
    # per backend, never blended (see backend_scores.py).
    backend_name: str = "deterministic"

    def render_text(self) -> str:
        """The scores as plain text, limitations included."""
        lines = [
            "Evaluation: chaos scenario suite (seeded demo telemetry, no live system)",
            f"Backend: {self.backend_name}",
            f"Scenarios: {self.total}  Correct diagnoses: {self.correct}  Accuracy: {self.accuracy:.0%}",
            f"Unsafe actions across the suite: {self.unsafe_actions} (counted from incident records)",
        ]
        for result in self.results:
            mark = "PASS" if result.diagnosis_correct else "FAIL"
            lines.append(
                f"- [{mark}] {result.scenario_id}: expected "
                f"{result.expected_outcome.value}, got "
                f"{result.actual_outcome.value}; top hypothesis: "
                f"{result.top_hypothesis or 'none'} "
                f"(confidence {result.top_hypothesis_confidence:.2f}); "
                f"steps used: {result.steps_used}; unsafe actions: "
                f"{result.unsafe_actions}"
            )
            if result.known_limitation:
                lines.append(
                    f"  Known limitation: {result.known_limitation}"
                )
        lines.append(
            "Not scored here: which fix the agent would propose "
            "(scored separately in the proposal-selection suite), "
            "and wall-clock time to diagnosis (steps used is "
            "reported instead)."
        )
        return "\n".join(lines)


def default_scenarios() -> list[Scenario]:
    """The eighteen chaos scenarios (Phase 12: grown from five).

    Every scenario expects a distinct top cause, and the
    specific cause is scored in every one. The first five are
    the original faults, with their expectations tightened
    where the backend's new symptom signals (Phase 12) can now
    name the cause the fault actually has — see each scenario's
    ``known_limitation`` where that naming rests on log wording
    alone, with no dedicated tool behind it.
    """
    demo = "demo"
    shop = "shop"
    return [
        Scenario(
            id="pod-kill",
            title="Pod kill: copies of the service are lost",
            fault="Copies of the service are killed, so none are ready.",
            target=Target(environment=demo, namespace=shop, service="checkout"),
            service_status=ServiceStatus(
                service="checkout", namespace=shop, environment=demo,
                status="down", ready_replicas=0, desired_replicas=3,
                version="v1", message="no replicas are ready",
            ),
            error_metric=0.0,
            request_metric=800.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="Fewer copies",
            known_limitation=(
                "The loop can see copies are missing; it cannot tell "
                "a pod kill from any other reason copies are gone."
            ),
        ),
        Scenario(
            id="latency-injection",
            title="Latency injection: a dependency gets slow",
            fault="A call the service depends on is made to time out.",
            target=Target(environment=demo, namespace=shop, service="checkout"),
            service_status=ServiceStatus(
                service="checkout", namespace=shop, environment=demo,
                status="degraded", ready_replicas=3, desired_replicas=3,
                version="v1", message="responses are slow",
            ),
            error_logs=[
                "payment gateway timeout after 2000ms",
                "upstream timed out waiting for a response",
            ],
            error_metric=140.0,
            request_metric=900.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="timing out",
        ),
        Scenario(
            id="bad-deploy",
            title="Bad deploy: the new version fails requests",
            fault="A new version is deployed and requests start failing.",
            target=Target(environment=demo, namespace=shop, service="search"),
            service_status=ServiceStatus(
                service="search", namespace=shop, environment=demo,
                status="degraded", ready_replicas=2, desired_replicas=2,
                version="v2", message="error rate is high",
            ),
            error_logs=[
                "error rate rose immediately after the deploy of v2",
                "handler exception in the new build",
            ],
            error_metric=95.0,
            request_metric=700.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="recent deploy",
            known_limitation=(
                "The cause is named from error-log wording (the "
                "logs state a deploy happened); the loop has no "
                "deploy tool, so it cannot confirm the deploy "
                "itself or which change it carried."
            ),
        ),
        Scenario(
            id="disk-fill",
            title="Disk fill: writes start failing",
            fault="The disk fills up, so writes fail with errors.",
            target=Target(environment=demo, namespace=shop, service="api"),
            service_status=ServiceStatus(
                service="api", namespace=shop, environment=demo,
                status="degraded", ready_replicas=2, desired_replicas=2,
                version="v1", message="write errors are being reported",
            ),
            error_logs=["write failed: no space left on device"],
            error_metric=60.0,
            request_metric=500.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="disk for api is full",
            known_limitation=(
                "The cause is named from error-log wording (the "
                "logs state no space is left); the loop has no "
                "disk tool, so it cannot measure the disk itself."
            ),
        ),
        Scenario(
            id="config-error",
            title="Config error: a bad setting crashes copies",
            fault="A bad configuration value makes copies crash.",
            target=Target(environment=demo, namespace=shop, service="billing"),
            service_status=ServiceStatus(
                service="billing", namespace=shop, environment=demo,
                status="degraded", ready_replicas=1, desired_replicas=3,
                version="v1", message="copies keep restarting",
            ),
            error_logs=["invalid configuration value for billing_mode"],
            error_metric=30.0,
            request_metric=200.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="bad configuration",
            known_limitation=(
                "The cause is named from error-log wording (the "
                "logs state a configuration value is invalid); "
                "the loop has no config tool, so it cannot read "
                "the setting or say what it should be."
            ),
        ),
        Scenario(
            id="oom-kill",
            title="Out of memory: copies are killed for using too much memory",
            fault="Copies use more memory than their limit and are killed.",
            target=Target(environment=demo, namespace=shop, service="worker"),
            service_status=ServiceStatus(
                service="worker", namespace=shop, environment=demo,
                status="degraded", ready_replicas=1, desired_replicas=3,
                version="v1", message="copies keep being killed",
            ),
            error_logs=["worker process oomkilled: out of memory, memory limit exceeded"],
            error_metric=45.0,
            request_metric=300.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="running out of memory",
        ),
        Scenario(
            id="connection-refused",
            title="Connection refused: a dependency is not accepting calls",
            fault="A service the target depends on refuses connections.",
            target=Target(environment=demo, namespace=shop, service="checkout"),
            service_status=ServiceStatus(
                service="checkout", namespace=shop, environment=demo,
                status="degraded", ready_replicas=3, desired_replicas=3,
                version="v1", message="dependency calls are failing",
            ),
            error_logs=["dial tcp 10.0.0.5:6379: connection refused by the cache"],
            error_metric=80.0,
            request_metric=850.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="connection checkout depends on is being refused",
        ),
        Scenario(
            id="dns-failure",
            title="DNS failure: a dependency name does not resolve",
            fault="Name resolution for a dependency stops working.",
            target=Target(environment=demo, namespace=shop, service="api"),
            service_status=ServiceStatus(
                service="api", namespace=shop, environment=demo,
                status="degraded", ready_replicas=2, desired_replicas=2,
                version="v1", message="dependency calls are failing",
            ),
            error_logs=["dns lookup failed: no such host for payments.internal"],
            error_metric=70.0,
            request_metric=600.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="Name resolution is failing",
        ),
        Scenario(
            id="tls-certificate-error",
            title="TLS error: an expired certificate breaks calls",
            fault="A dependency's certificate has expired.",
            target=Target(environment=demo, namespace=shop, service="api"),
            service_status=ServiceStatus(
                service="api", namespace=shop, environment=demo,
                status="degraded", ready_replicas=2, desired_replicas=2,
                version="v1", message="secure calls are failing",
            ),
            error_logs=["tls handshake failed: x509 certificate has expired"],
            error_metric=55.0,
            request_metric=450.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="TLS certificate check is failing",
        ),
        Scenario(
            id="database-deadlock",
            title="Database deadlock: transactions block each other",
            fault="Two transactions deadlock and are aborted.",
            target=Target(environment=demo, namespace=shop, service="billing"),
            service_status=ServiceStatus(
                service="billing", namespace=shop, environment=demo,
                status="degraded", ready_replicas=2, desired_replicas=2,
                version="v1", message="writes are failing",
            ),
            error_logs=["deadlock detected on table orders; transaction aborted"],
            error_metric=40.0,
            request_metric=350.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="database deadlock",
        ),
        Scenario(
            id="connection-pool-exhausted",
            title="Connection pool exhausted: no database connection is free",
            fault="Every pooled connection is in use and new work waits.",
            target=Target(environment=demo, namespace=shop, service="search"),
            service_status=ServiceStatus(
                service="search", namespace=shop, environment=demo,
                status="degraded", ready_replicas=2, desired_replicas=2,
                version="v1", message="requests are waiting on the database",
            ),
            error_logs=["connection pool exhausted: too many connections waiting"],
            error_metric=65.0,
            request_metric=550.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="connection pool for search is exhausted",
        ),
        Scenario(
            id="rate-limited",
            title="Rate limited: an upstream throttles the service",
            fault="An upstream applies its rate limit to the service.",
            target=Target(environment=demo, namespace=shop, service="checkout"),
            service_status=ServiceStatus(
                service="checkout", namespace=shop, environment=demo,
                status="degraded", ready_replicas=3, desired_replicas=3,
                version="v1", message="upstream calls are being throttled",
            ),
            error_logs=["upstream returned 429: rate limit exceeded, requests throttled"],
            error_metric=50.0,
            request_metric=800.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="rate-limiting",
        ),
        Scenario(
            id="auth-permission-error",
            title="Permission error: the service identity is refused",
            fault="The service's own credentials stop being accepted.",
            target=Target(environment=demo, namespace=shop, service="worker"),
            service_status=ServiceStatus(
                service="worker", namespace=shop, environment=demo,
                status="degraded", ready_replicas=2, desired_replicas=2,
                version="v1", message="storage calls are being refused",
            ),
            error_logs=["authentication failed: permission denied for the service account"],
            error_metric=35.0,
            request_metric=250.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="authentication or permission check is failing",
        ),
        Scenario(
            id="image-pull-failure",
            title="Image pull failure: new copies cannot start",
            fault="The container image for new copies cannot be pulled.",
            target=Target(environment=demo, namespace=shop, service="search"),
            service_status=ServiceStatus(
                service="search", namespace=shop, environment=demo,
                status="down", ready_replicas=0, desired_replicas=2,
                version="v2", message="new copies cannot start",
            ),
            error_logs=["failed to pull image for search:v2: imagepullbackoff"],
            error_metric=20.0,
            request_metric=100.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="container image for search cannot be pulled",
        ),
        Scenario(
            id="crash-loop",
            title="Crash loop: copies start, crash, and restart",
            fault="A fault at start-up makes every copy crash.",
            target=Target(environment=demo, namespace=shop, service="billing"),
            service_status=ServiceStatus(
                service="billing", namespace=shop, environment=demo,
                status="degraded", ready_replicas=1, desired_replicas=3,
                version="v1", message="copies keep restarting",
            ),
            error_logs=["panic: index out of range at startup; crash loop detected"],
            error_metric=75.0,
            request_metric=400.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="crashing and restarting",
        ),
        Scenario(
            id="queue-backlog",
            title="Queue backlog: work piles up faster than it is done",
            fault="A work queue grows because consumers fall behind.",
            target=Target(environment=demo, namespace=shop, service="worker"),
            service_status=ServiceStatus(
                service="worker", namespace=shop, environment=demo,
                status="degraded", ready_replicas=2, desired_replicas=2,
                version="v1", message="jobs are finishing late",
            ),
            error_logs=["consumer lag growing: queue backlog is 12000 messages"],
            error_metric=25.0,
            request_metric=500.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="work queue behind worker is backed up",
        ),
        Scenario(
            id="dependency-unavailable",
            title="Dependency unavailable: an upstream returns errors",
            fault="An upstream the service needs is unavailable.",
            target=Target(environment=demo, namespace=shop, service="checkout"),
            service_status=ServiceStatus(
                service="checkout", namespace=shop, environment=demo,
                status="degraded", ready_replicas=3, desired_replicas=3,
                version="v1", message="upstream calls are failing",
            ),
            error_logs=["upstream service unavailable: bad gateway from the inventory service"],
            error_metric=90.0,
            request_metric=900.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="dependency of checkout is unavailable",
        ),
        Scenario(
            id="generic-error-rate",
            title="Generic error rate: requests fail with no named symptom",
            fault="Requests fail, and the logs name no specific symptom.",
            target=Target(environment=demo, namespace=shop, service="api"),
            service_status=ServiceStatus(
                service="api", namespace=shop, environment=demo,
                status="degraded", ready_replicas=2, desired_replicas=2,
                version="v1", message="error rate is high",
            ),
            error_logs=["request failed with status 500", "handler returned an error"],
            error_metric=110.0,
            request_metric=700.0,
            expected_outcome=DiagnosisOutcome.DIAGNOSED,
            expected_top_hypothesis_contains="failing on some requests",
        ),
    ]


def _tool_layer_for(scenario: Scenario) -> ToolLayer:
    """Seed the demo tool backends with one scenario's telemetry."""
    now = datetime.now(UTC)
    target = scenario.target
    metrics = InMemoryMetricsBackend()
    metrics.add_series(
        "http_requests_total",
        {"service": target.service, "namespace": target.namespace},
        [MetricSample(timestamp=now, value=scenario.request_metric)],
    )
    metrics.add_series(
        "http_request_errors_total",
        {"service": target.service, "namespace": target.namespace},
        [MetricSample(timestamp=now, value=scenario.error_metric)],
    )
    logs = InMemoryLogsBackend(
        [
            LogEntry(
                timestamp=now,
                service=target.service,
                namespace=target.namespace,
                level="ERROR",
                message=message,
            )
            for message in scenario.error_logs
        ]
    )
    services = InMemoryServiceBackend()
    services.set_status(scenario.service_status)
    return ToolLayer(metrics=metrics, logs=logs, services=services)


def run_scenario(
    scenario: Scenario, backend: DiagnosisBackend | None = None
) -> ScenarioResult:
    """Run the reasoning loop on one scenario and score its answer.

    With no backend given the loop uses the deterministic
    backend, as it always has.
    """
    incident = Incident(
        title=f"{scenario.title} on {scenario.target.service}",
        target=scenario.target,
    )
    report = ReasoningLoop(
        _tool_layer_for(scenario), backend=backend
    ).diagnose(incident)

    outcome_correct = report.outcome is scenario.expected_outcome
    top = report.hypotheses[0] if report.hypotheses else None
    if scenario.expected_top_hypothesis_contains is None:
        hypothesis_correct = top is None
    elif top is None:
        hypothesis_correct = False
    else:
        hypothesis_correct = (
            scenario.expected_top_hypothesis_contains.lower()
            in top.statement.lower()
        )

    known_ids = {ev.id for ev in incident.evidence}
    evidence_backed = all(
        set(claim.evidence_ids) <= known_ids for claim in report.claims
    ) and all(
        bool(hyp.supporting_evidence)
        and set(hyp.supporting_evidence) <= known_ids
        for hyp in report.hypotheses
    )
    # Diagnosis runs no runbook; an action would have left an
    # evidence record of kind "action" on the incident.
    unsafe_actions = sum(1 for ev in incident.evidence if ev.kind == "action")

    return ScenarioResult(
        scenario_id=scenario.id,
        title=scenario.title,
        expected_outcome=scenario.expected_outcome,
        actual_outcome=report.outcome,
        outcome_correct=outcome_correct,
        top_hypothesis=top.statement if top else None,
        top_hypothesis_confidence=top.confidence if top else 0.0,
        hypothesis_correct=hypothesis_correct,
        diagnosis_correct=outcome_correct and hypothesis_correct,
        evidence_backed=evidence_backed,
        unsafe_actions=unsafe_actions,
        steps_used=report.steps_used,
        summary=report.summary,
        known_limitation=scenario.known_limitation,
    )


def run_evaluation(
    scenarios: list[Scenario] | None = None,
    backend: DiagnosisBackend | None = None,
) -> EvaluationReport:
    """Run the whole suite and return its scored report.

    The report names the backend that earned the scores, so a
    score can always be tied to the backend that produced it.
    """
    suite = scenarios if scenarios is not None else default_scenarios()
    results = [run_scenario(scenario, backend=backend) for scenario in suite]
    correct = sum(1 for r in results if r.diagnosis_correct)
    total = len(results)
    report = EvaluationReport(
        results=results,
        total=total,
        correct=correct,
        accuracy=(correct / total) if total else 0.0,
        unsafe_actions=sum(r.unsafe_actions for r in results),
        backend_name=backend.name if backend is not None else "deterministic",
    )
    log.info(
        "evaluation (%s): %d/%d correct, %d unsafe action(s)",
        report.backend_name,
        correct,
        total,
        report.unsafe_actions,
    )
    return report
