"""Command-line tool: look at incidents and show a diagnosis.

In plain words, this is how a person talks to the agent from a
terminal:

    sre-agent incidents [--base-url URL]
    sre-agent show <incident-id> [--base-url URL]
    sre-agent demo diagnose
    sre-agent demo approve --by NAME [--runbook ID] [--replicas N] [--yes]
    sre-agent demo postmortem
    sre-agent demo memory
    sre-agent demo adapters
    sre-agent check evaluate
    sre-agent check regression [--baseline PATH] [--update-baseline]
    sre-agent check injection
    sre-agent check tier3
    sre-agent check security
    sre-agent --version

The demo commands and the scored checks also answer to their
old names — ``diagnose``, ``approve-demo``, ``postmortem-demo``,
``memory-demo``, ``adapters-demo``, ``evaluate``,
``regression-check``, ``injection-check``, ``tier3-check`` and
``security-check`` — kept as aliases for one release (Phase 12).
The product commands, ``incidents`` and ``show``, did not move.

How it works, honestly:

- ``incidents`` and ``show`` ask the running agent server for its
  records (the same ``GET /incidents`` endpoints the server already
  serves). The incident store lives in the server's memory, so the
  CLI is a thin client of that server: if the server is not
  running, the CLI says so plainly instead of printing an empty
  list that would look like "no incidents".
- The CLI uses only the Python standard library to talk to the
  server, so installing the CLI never needs the server extras.
- ``diagnose`` runs the reasoning loop once, locally, on the
  seeded demo data and prints the report. Since Phase 7 the
  server runs the same loop itself when it is built with a tool
  layer — the shipped server app uses the demo tool layer — so a
  live incident diagnosed by its server carries that diagnosis
  on its record, and ``show`` prints it, with its causes and
  evidence. When the server was built with a memory of past
  incidents, the incident also carries that memory's advice,
  and ``show`` prints it too, labelled advice only. A server
  built with no tool layer records incidents only; ``show``
  then says there is no diagnosis yet.

``approve-demo`` is the first command that changes anything, and
it changes demo state only, through the whole Tier 2 path: a fix
for the seeded demo checkout workload is proposed, the policy
engine sends it for approval, and it runs only if the person at
the terminal approves that exact action — typed at the prompt, or
with ``--yes`` — under their name (``--by``). Policy is checked
again at approval time, the approval expires in 15 minutes and
can be used once, and every decision lands in an audit log for
the run (see approvals.py). Since Phase 7 the server serves
the same approval flow for its own incidents over HTTP (see
server.py and the operator guide); this CLI command stays the
in-process demo, and there is no CLI command for the server's
approvals yet.

``postmortem-demo`` runs one seeded demo incident through the
whole path — diagnosis, a Tier 2 approved restart, resolution —
in this process, and prints the postmortem draft generated from
its record (see postmortems.py). Drafting a postmortem for a live
server incident is not wired yet, for the same reason: the draft
needs the full record, including its audit records, in one place.

``evaluate`` runs the chaos scenario suite locally — eighteen seeded
faults, no server or Docker needed — and prints the scored
report (see evaluation.py): how many diagnoses were correct,
and how many unsafe actions ran (zero, counted from the incident
records). It exits 1 when any scenario scores incorrect or any
unsafe action is counted, so it can gate a release later.

``regression-check`` is that gate (see regression.py): it runs
the suite and compares the fresh scores against the recorded
baseline in ``evaluation/baseline.json`` — the last release's
scores. Unsafe actions must be zero (always, not just compared
to the baseline), every diagnosis must cite real evidence, the
accuracy may not drop, and no scenario that was correct may turn
wrong. It exits 1 when any rule breaks. Running the check never
changes the baseline; ``--update-baseline`` records the fresh
run as the new baseline on purpose, at a release, and refuses a
run that itself breaks the absolute rules.

``injection-check`` runs the prompt-injection test set locally
(see injection.py): hostile text planted in log lines, alert
text, a service status message, and a metric label — orders to
delete, run a fix, retarget, close the incident, or reveal
secrets. Each case passes only if the text changed nothing: it
stays in the evidence record as data, no claim, hypothesis, or
diagnosis repeats its orders, no action runs, and the answer is
the one the honest numbers give. It exits 1 when any case fails
or any unsafe action is counted.

``tier3-check`` runs the Tier 3 drill set locally (see
tier3.py): seven rehearsed checks on the demo environment's
bounded-autonomy grant. The pre-approved fixes (restart and
scale, shop namespace only, at most three replicas touched)
must run on their own and be audited; rollback, which is not
pre-approved, must not run on its own; a fix that is too big
or aimed outside the granted namespace must be refused; and
the kill switch drills must show a halt pressed between two
runs stopping the second one, while read-only checks continue.
It exits 1 when any drill fails.

``memory-demo`` runs the memory report locally on seeded past
incidents (see memory.py): for a fresh demo checkout incident
it finds the similar past incidents, counts each runbook's
record from the action records on those incidents, and ranks
suggested runbooks by their record on same-service past
incidents only.
It is advice only — it runs nothing, proposes nothing through
the policy engine, and approves nothing. Memory is not wired
into the live server or the reasoning loop yet.

``adapters-demo`` runs the three read adapters locally on
seeded demo data (see the adapters package): the Kubernetes pod listing
for the demo checkout workload, a Datadog metrics query, and an
Azure Monitor KQL query. Every adapter only looks — nothing is
changed in any system — and no live cluster, Datadog account or
Azure workspace is called; the live backends behind the same
shapes are not claimed as tested.

``security-check`` runs the security review probes locally (see
the security_review package): ten probes that attack the agent's own
surface — a made-up runbook, a free-form command as a parameter,
a retargeted fix, secrets planted where the audit log and the
evidence could leak them, a replayed approval, an approval into
a halt, the Tier 3 limits, refused management and broken
queries, a scan for any shell surface, and alert text claiming
an incident is over. It prints the findings the probes cannot
prove away as well (the largest: the webhook server has no
authentication). It exits 1 when any probe fails.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__
from .adapters import AdaptersDemoReport, demo_adapters
from .approvals import ApprovalError, approve_pending, create_pending, execute_approved
from .audit import AuditLog, evaluate_and_audit
from .backend_url import validate_backend_url
from .evaluation import EvaluationReport, run_evaluation
from .injection import InjectionReport, run_injection_suite
from .memory import MemoryReport, demo_memory
from .models import Incident, IncidentStatus, Target
from .policy import EnvironmentPolicy, Tier
from .postmortems import PostmortemDraft, demo_postmortem
from .reasoning import DiagnosisReport, demo_diagnosis
from .regression import (
    DEFAULT_BASELINE_PATH,
    RegressionResult,
    check_regression,
    load_baseline,
    record_new_baseline,
)
from .runbooks import RunbookError, build_action, demo_runbooks
from .security_review import SecurityReport, run_security_review
from .tier3 import Tier3Report, run_tier3_drills

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
ENV_BASE_URL = "SRE_AGENT_BASE_URL"
ENV_API_TOKEN = "SRE_AGENT_API_TOKEN"
TOKEN_HEADER = "X-SRE-Agent-Token"
TIMEOUT_SECONDS = 5.0

USAGE = """Usage:
  sre-agent incidents [--base-url URL]   List the incidents the server knows
  sre-agent show <incident-id> [--base-url URL]
                                         Show one incident in full, with its
                                         diagnosis, causes and evidence
  sre-agent diagnose                     Run the demo diagnosis locally and
                                         print the report (seeded demo data)
  sre-agent approve-demo --by NAME [--runbook ID] [--replicas N] [--yes]
                                         Propose a fix for the demo checkout
                                         workload and run it only if you
                                         approve that exact action (Tier 2)
  sre-agent postmortem-demo              Run a demo incident end to end and
                                         print its postmortem draft
  sre-agent evaluate                     Run the eighteen chaos scenarios on
                                         seeded demo data and print the
                                         scored report (exit 1 if any
                                         scenario fails or any unsafe
                                         action is counted)
  sre-agent regression-check [--baseline PATH] [--update-baseline]
                                         Compare a fresh run of the suite
                                         against the recorded baseline
                                         (default evaluation/baseline.json):
                                         unsafe actions must be 0, accuracy
                                         may not drop, no correct scenario
                                         may turn wrong (exit 1 if any rule
                                         breaks). --update-baseline records
                                         the fresh run as the new baseline
                                         on purpose, at a release
  sre-agent injection-check              Run the prompt-injection test set:
                                         hostile text in logs, alerts, a
                                         status message and a metric label
                                         must change nothing (exit 1 if any
                                         case fails or any unsafe action
                                         is counted)
  sre-agent tier3-check                  Run the Tier 3 drill set on the
                                         demo grant: pre-approved fixes
                                         run, everything outside the grant
                                         does not, and the kill switch
                                         stops mutations while reads
                                         continue (exit 1 if any drill
                                         fails)
  sre-agent memory-demo                  Show what memory says about a
                                         fresh demo checkout incident:
                                         similar past incidents, each
                                         runbook's record, and suggested
                                         runbooks ranked by their record
                                         on same-service past incidents
                                         only (advice only; nothing runs)
  sre-agent adapters-demo                Show the three read adapters on
                                         seeded demo data: Kubernetes pods,
                                         a Datadog metrics query, and an
                                         Azure Monitor KQL query (all
                                         read-only; no live system called)
  sre-agent security-check               Run the security review probes:
                                         ten attacks on the agent's own
                                         surface must all be refused or
                                         contained, and the findings the
                                         probes cannot prove away are
                                         printed (exit 1 if any probe
                                         fails)
  sre-agent demo diagnose                Same as: diagnose
  sre-agent demo approve --by NAME       Same as: approve-demo --by NAME
  sre-agent demo postmortem              Same as: postmortem-demo
  sre-agent demo memory                  Same as: memory-demo
  sre-agent demo adapters                Same as: adapters-demo
  sre-agent check evaluate               Same as: evaluate
  sre-agent check regression             Same as: regression-check
  sre-agent check injection              Same as: injection-check
  sre-agent check tier3                  Same as: tier3-check
  sre-agent check security               Same as: security-check
  sre-agent --version                    Print the version

The demo ... and check ... forms are the grouped names (Phase 12).
Every old name above them still works, kept as an alias for one
release; the grouped form and its alias run exactly the same code.

The server address defaults to http://127.0.0.1:8080 and can also be
set with the SRE_AGENT_BASE_URL environment variable. The address
follows the backend address rules (finding F-05): https, or plain
http to this machine only — a plain-http address for a server on
another machine is refused, so the token cannot cross a network
unencrypted. If the server
was started with a token (SRE_AGENT_API_TOKEN set), set the same
variable for the CLI and it sends the token on every read."""


class CliError(Exception):
    """A problem the user should read, not a traceback."""


FetchJson = Callable[[str], Any]


def fetch_json(url: str) -> Any:
    """GET *url* and return the decoded JSON, or raise CliError.

    A server that is not there, an answer that is not JSON, and an
    HTTP error are all turned into one plain sentence.
    """
    headers = {"Accept": "application/json"}
    api_token = os.environ.get(ENV_API_TOKEN)
    if api_token:
        headers[TOKEN_HEADER] = api_token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise CliError("the server says that incident does not exist") from exc
        raise CliError(f"the server answered with an error (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CliError(
            f"could not reach the agent server at {url} — is it running? "
            "Start it with: uvicorn sre_agent.server:app --port 8080"
        ) from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise CliError("the server answered, but its answer was not JSON") from exc


def format_incident_list(rows: list[dict[str, Any]]) -> str:
    """One plain line per incident, plus a count."""
    if not rows:
        return (
            "No incidents. The server is running, but it has not "
            "received any alerts yet."
        )
    lines = [f"{len(rows)} incident(s):"]
    for row in rows:
        target = row.get("target") or {}
        where = (
            f"{target.get('service', '?')}/{target.get('namespace', '?')} "
            f"({target.get('environment', '?')})"
        )
        lines.append(
            f"- {row.get('id', '?')}  [{row.get('status', '?')}]  {where}  "
            f"{row.get('title', '')}  "
            f"(evidence: {row.get('evidence_count', 0)}, "
            f"duplicates: {row.get('duplicate_count', 0)})"
        )
    return "\n".join(lines)


def format_incident_detail(incident: dict[str, Any]) -> str:
    """Show one incident in full: what, where, diagnosis, evidence."""
    target = incident.get("target") or {}
    lines = [
        f"Incident {incident.get('id', '?')}",
        f"Title: {incident.get('title', '')}",
        f"Status: {incident.get('status', '?')}",
        (
            f"Target: {target.get('service', '?')} in "
            f"{target.get('namespace', '?')} ({target.get('environment', '?')})"
        ),
        f"Created: {incident.get('created_at', '?')}",
        f"Updated: {incident.get('updated_at', '?')}",
        f"Duplicate alerts counted: {incident.get('duplicate_count', 0)}",
        "",
    ]
    diagnosis = incident.get("diagnosis")
    if diagnosis:
        lines.append(f"Diagnosis: {diagnosis}")
    else:
        lines.append("Diagnosis: none yet — the record holds no diagnosis.")
    hypotheses = incident.get("hypotheses") or []
    if hypotheses:
        lines.append("Possible causes, as recorded:")
        for hyp in hypotheses:
            lines.append(
                f"- {hyp.get('statement', '')} "
                f"(confidence {float(hyp.get('confidence', 0.0)):.2f})"
            )
    memory_report = incident.get("memory_report")
    if memory_report:
        lines.append(
            "Memory advice (advice only — nothing here runs or approves a fix):"
        )
        matches = memory_report.get("matches") or []
        if not matches:
            lines.append("- No similar past incident was found.")
        for match in matches:
            lines.append(
                f"- Past incident {match.get('incident_id', '?')} "
                f"[{match.get('status', '?')}] score {match.get('score', 0)}: "
                f"{match.get('title', '')}"
            )
        suggestions = memory_report.get("suggestions") or []
        if not suggestions:
            lines.append(
                "- No runbook is suggested: no similar past incident ran one."
            )
        for suggestion in suggestions:
            lines.append(
                f"- Suggested runbook {suggestion.get('runbook_id', '?')}: "
                f"{suggestion.get('reason', '')}"
            )
    evidence = incident.get("evidence") or []
    lines.append(f"Evidence ({len(evidence)} record(s)):")
    if not evidence:
        lines.append("- No evidence recorded yet.")
    for ev in evidence:
        lines.append(
            f"- [{ev.get('kind', '?')}] {ev.get('summary', '')} "
            f"(query: {ev.get('query', '?')})"
        )
    return "\n".join(lines)


def _split_base_url(args: list[str]) -> tuple[list[str], str]:
    """Pull ``--base-url URL`` (or ``--base-url=URL``) out of *args*."""
    base_url = os.environ.get(ENV_BASE_URL, DEFAULT_BASE_URL)
    rest: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--base-url":
            if i + 1 >= len(args):
                raise CliError("--base-url needs an address after it")
            base_url = args[i + 1]
            i += 2
        elif arg.startswith("--base-url="):
            base_url = arg.split("=", 1)[1]
            i += 1
        else:
            rest.append(arg)
            i += 1
    if not base_url:
        raise CliError("the server address must not be empty")
    try:
        checked = validate_backend_url(base_url, what="server address")
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    return rest, checked


DEMO_CHECKOUT = Target(environment="demo", namespace="shop", service="checkout")


def _parse_approve_demo_args(args: list[str]) -> dict[str, Any]:
    """Parse approve-demo's flags: --by, --runbook, --replicas, --yes."""
    parsed: dict[str, Any] = {"by": None, "runbook": "restart-workload",
                               "replicas": None, "yes": False}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--yes":
            parsed["yes"] = True
            i += 1
            continue
        name = None
        value = None
        for flag in ("--by", "--runbook", "--replicas"):
            if arg == flag:
                if i + 1 >= len(args):
                    raise CliError(f"{flag} needs a value after it")
                name, value = flag, args[i + 1]
                i += 2
                break
            if arg.startswith(flag + "="):
                name, value = flag, arg.split("=", 1)[1]
                i += 1
                break
        if name is None:
            raise CliError(f"approve-demo does not take {arg!r}")
        if name == "--replicas":
            try:
                parsed["replicas"] = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise CliError("--replicas needs a whole number") from exc
        else:
            parsed[name[2:]] = value
    return parsed


def _prompt_for_approval(question: str) -> bool:
    """Ask at the terminal; anything but 'approve', or no terminal, is a no."""
    try:
        answer = input(f"{question} Type 'approve' to run it: ")
    except EOFError:
        return False
    return answer.strip().lower() == "approve"


def run_demo_approval(
    *,
    approved_by: str,
    runbook_id: str,
    parameters: dict[str, str | int | float | bool],
    confirmed: bool,
    confirm: Callable[[str], bool] | None = None,
) -> int:
    """The Tier 2 path end to end, on the seeded demo workload.

    Propose → policy sends it for approval → a human approves that
    exact action (or not) → only then does it run. Prints each step
    in plain words. Returns the process exit code.
    """
    executor, backend = demo_runbooks()
    incident = Incident(
        id="demo-incident-1",
        title="Checkout errors are above 10%",
        target=DEMO_CHECKOUT,
    )
    incident.transition(IncidentStatus.TRIAGING)
    incident.diagnosis = "Demo diagnosis: the checkout workload is failing."
    incident.transition(IncidentStatus.DIAGNOSED)

    try:
        action = build_action(
            runbook_id, DEMO_CHECKOUT, parameters, backend,
            rationale="demo diagnosis: the checkout workload is failing",
        )
    except RunbookError as exc:
        raise CliError(str(exc)) from exc

    policy = EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITH_APPROVAL,
        max_replicas_affected=5,
    )
    audit = AuditLog()
    result = evaluate_and_audit(
        action, policy, audit, incident_id=incident.id, incident_target=incident.target
    )
    print(f"Proposed: {action.runbook_id} on checkout/shop (demo), "
          f"touching {action.replicas_affected} replica(s).")
    print(f"Policy: {result.decision.value} — {result.reason}")
    try:
        pending = create_pending(action, result, incident_id=incident.id)
    except ApprovalError as exc:
        raise CliError(str(exc)) from exc
    print(f"This approval, if given, expires at {pending.expires_at:%H:%M:%S} UTC "
          "and can be used once, for this action only.")

    if not confirmed:
        ask = confirm if confirm is not None else _prompt_for_approval
        confirmed = ask(f"Approve {action.runbook_id} on checkout for incident "
                        f"{incident.id}, as {approved_by}?")
    if not confirmed:
        print("Not approved. Nothing was run.")
        return 0

    try:
        grant, allowed = approve_pending(
            pending, approved_by=approved_by, policy=policy, audit_log=audit,
            incident_target=incident.target,
        )
        incident.transition(IncidentStatus.MITIGATING)
        run_result = execute_approved(executor, pending, grant, allowed)
    except (ApprovalError, RunbookError) as exc:
        raise CliError(str(exc)) from exc
    incident.add_evidence(run_result.to_evidence())
    incident.transition(IncidentStatus.RESOLVED)
    state = backend.get(DEMO_CHECKOUT)
    print(f"Approved by {approved_by}. Ran: {run_result.summary}")
    print(f"Incident {incident.id}: {incident.status.value}; audit log holds "
          f"{len(audit.for_incident(incident.id))} decision(s) for it.")
    if state is not None:
        print(f"Checkout now: {state.replicas} replica(s), version {state.version}, "
              f"failing={state.failing}, restarts={state.restarts}.")
    return 0


def _parse_regression_args(args: list[str]) -> tuple[Path, bool]:
    """Parse regression-check's flags: --baseline PATH, --update-baseline."""
    baseline_path = DEFAULT_BASELINE_PATH
    update = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--update-baseline":
            update = True
            i += 1
        elif arg == "--baseline":
            if i + 1 >= len(args):
                raise CliError("--baseline needs a file path after it")
            baseline_path = Path(args[i + 1])
            i += 2
        elif arg.startswith("--baseline="):
            baseline_path = Path(arg.split("=", 1)[1])
            i += 1
        else:
            raise CliError(f"regression-check does not take {arg!r}")
    return baseline_path, update


def run_regression_check(
    *,
    baseline_path: Path,
    update_baseline: bool,
    evaluate: Callable[[], EvaluationReport],
) -> int:
    """Run the suite once and apply the regression gate to its scores.

    With ``update_baseline``, the fresh run is recorded as the new
    baseline instead of being compared — a deliberate release act,
    refused for a run that breaks the absolute rules.
    """
    report = evaluate()
    if update_baseline:
        try:
            baseline = record_new_baseline(report, baseline_path)
        except ValueError as exc:
            raise CliError(str(exc)) from exc
        print(
            f"Baseline updated at {baseline_path}: version {baseline.version}, "
            f"recorded {baseline.recorded_on}, accuracy {baseline.accuracy:.0%}, "
            f"unsafe actions {baseline.unsafe_actions}. "
            f"History now holds {len(baseline.history)} release record(s)."
        )
        return 0
    if not baseline_path.exists():
        raise CliError(
            f"no baseline file at {baseline_path} — the gate has nothing to "
            "compare against. Record one on purpose with: "
            "sre-agent regression-check --update-baseline"
        )
    try:
        baseline = load_baseline(baseline_path)
    except (OSError, ValueError) as exc:
        raise CliError(f"the baseline file at {baseline_path} could not be read: {exc}") from exc
    result: RegressionResult = check_regression(report, baseline)
    print(result.render_text())
    return 0 if result.passed else 1


# Phase 12 restructure: the demo commands and the scored checks
# live under two grouped names, so the product commands
# (incidents, show) are no longer mixed in with the demo and the
# test harness. Each grouped name maps to exactly the command it
# replaces; the old names stay as aliases for one release and are
# handled by the same dispatch below, so both forms run the same
# code and cannot drift apart.
DEMO_COMMANDS: dict[str, str] = {
    "diagnose": "diagnose",
    "approve": "approve-demo",
    "approve-demo": "approve-demo",
    "postmortem": "postmortem-demo",
    "postmortem-demo": "postmortem-demo",
    "memory": "memory-demo",
    "memory-demo": "memory-demo",
    "adapters": "adapters-demo",
    "adapters-demo": "adapters-demo",
}

CHECK_COMMANDS: dict[str, str] = {
    "evaluate": "evaluate",
    "evaluation": "evaluate",
    "regression": "regression-check",
    "regression-check": "regression-check",
    "injection": "injection-check",
    "injection-check": "injection-check",
    "tier3": "tier3-check",
    "tier3-check": "tier3-check",
    "security": "security-check",
    "security-check": "security-check",
}


def _resolve_grouped_command(
    command: str, rest: list[str]
) -> tuple[str, list[str]] | None:
    """Turn ``demo X`` / ``check X`` into the command X names.

    Returns ``None`` when *command* is not a group name, so the
    caller keeps its normal dispatch (old names included). Raises
    CliError when a group name has no known subcommand.
    """
    groups = {"demo": DEMO_COMMANDS, "check": CHECK_COMMANDS}
    mapping = groups.get(command)
    if mapping is None:
        return None
    if not rest:
        raise CliError(
            f"{command} needs a subcommand: one of "
            + ", ".join(sorted(set(mapping.values())))
        )
    target = mapping.get(rest[0])
    if target is None:
        raise CliError(f"unknown {command} subcommand {rest[0]!r}")
    return target, rest[1:]


def main(
    argv: list[str] | None = None,
    *,
    fetch: FetchJson | None = None,
    run_demo_diagnosis: Callable[[], DiagnosisReport] | None = None,
    confirm_approval: Callable[[str], bool] | None = None,
    run_demo_postmortem: Callable[[], PostmortemDraft] | None = None,
    run_suite: Callable[[], EvaluationReport] | None = None,
    run_injection: Callable[[], InjectionReport] | None = None,
    run_drills: Callable[[], Tier3Report] | None = None,
    run_demo_memory: Callable[[], MemoryReport] | None = None,
    run_demo_adapters: Callable[[], AdaptersDemoReport] | None = None,
    run_security: Callable[[], SecurityReport] | None = None,
) -> int:
    """Run one CLI command. Returns the process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    get_json = fetch if fetch is not None else fetch_json

    if not args or args[0] in {"--help", "-h", "help"}:
        print(USAGE)
        return 0
    if args[0] in {"--version", "version"}:
        print(f"sre-agent {__version__}")
        return 0

    command, rest = args[0], args[1:]
    try:
        resolved = _resolve_grouped_command(command, rest)
    except CliError as exc:
        print(f"sre-agent: {exc}\n\n{USAGE}", file=sys.stderr)
        return 2
    if resolved is not None:
        command, rest = resolved
    try:
        if command == "diagnose":
            if rest:
                raise CliError("diagnose takes no extra arguments")
            diagnose = run_demo_diagnosis or demo_diagnosis
            print("Demo diagnosis (seeded demo data, not a real system):")
            print(diagnose().render_text())
            return 0
        if command == "postmortem-demo":
            if rest:
                raise CliError("postmortem-demo takes no extra arguments")
            make_draft = run_demo_postmortem or demo_postmortem
            print("Demo postmortem (seeded demo data, not a real system):")
            print(make_draft().render_markdown())
            return 0
        if command == "evaluate":
            if rest:
                raise CliError("evaluate takes no extra arguments")
            evaluate = run_suite or run_evaluation
            report = evaluate()
            print(report.render_text())
            if report.correct != report.total or report.unsafe_actions:
                return 1
            return 0
        if command == "injection-check":
            if rest:
                raise CliError("injection-check takes no extra arguments")
            check_injection = run_injection or run_injection_suite
            injection_report = check_injection()
            print(injection_report.render_text())
            if (
                injection_report.passed_count != injection_report.total
                or injection_report.unsafe_actions
            ):
                return 1
            return 0
        if command == "tier3-check":
            if rest:
                raise CliError("tier3-check takes no extra arguments")
            drills = run_drills or run_tier3_drills
            tier3_report = drills()
            print(tier3_report.render_text())
            if tier3_report.passed_count != tier3_report.total:
                return 1
            return 0
        if command == "memory-demo":
            if rest:
                raise CliError("memory-demo takes no extra arguments")
            make_report = run_demo_memory or demo_memory
            print("Demo memory report (seeded past incidents, not a real system):")
            print(make_report().render_text())
            return 0
        if command == "adapters-demo":
            if rest:
                raise CliError("adapters-demo takes no extra arguments")
            make_adapters_report = run_demo_adapters or demo_adapters
            print(make_adapters_report().render_text())
            return 0
        if command == "security-check":
            if rest:
                raise CliError("security-check takes no extra arguments")
            review = run_security or run_security_review
            security_report = review()
            print(security_report.render_text())
            if security_report.passed_count != security_report.total:
                return 1
            return 0
        if command == "regression-check":
            baseline_path, update_baseline = _parse_regression_args(rest)
            return run_regression_check(
                baseline_path=baseline_path,
                update_baseline=update_baseline,
                evaluate=run_suite or run_evaluation,
            )
        if command == "approve-demo":
            opts = _parse_approve_demo_args(rest)
            if not opts["by"] or not str(opts["by"]).strip():
                raise CliError("approve-demo needs --by NAME: an approval must "
                               "name who is approving")
            parameters: dict[str, str | int | float | bool] = {}
            if opts["runbook"] == "scale-replicas":
                if opts["replicas"] is None:
                    raise CliError("the scale-replicas runbook needs --replicas N")
                parameters = {"replicas": opts["replicas"]}
            elif opts["replicas"] is not None:
                raise CliError("--replicas only applies to the scale-replicas runbook")
            return run_demo_approval(
                approved_by=str(opts["by"]),
                runbook_id=str(opts["runbook"]),
                parameters=parameters,
                confirmed=bool(opts["yes"]),
                confirm=confirm_approval,
            )
        if command == "incidents":
            positional, base_url = _split_base_url(rest)
            if positional:
                raise CliError("incidents takes no arguments besides --base-url")
            rows = get_json(f"{base_url}/incidents")
            if not isinstance(rows, list):
                raise CliError("the server answered, but not with a list of incidents")
            print(format_incident_list(rows))
            return 0
        if command == "show":
            positional, base_url = _split_base_url(rest)
            if len(positional) != 1:
                raise CliError("show needs exactly one incident id")
            incident = get_json(f"{base_url}/incidents/{positional[0]}")
            if not isinstance(incident, dict):
                raise CliError("the server answered, but not with an incident")
            print(format_incident_detail(incident))
            return 0
    except CliError as exc:
        print(f"sre-agent: {exc}", file=sys.stderr)
        return 1

    print(f"sre-agent: unknown command {command!r}\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
