# Safety model

How `sre-agent` is kept from making an incident worse. This document is the contract the code is tested against; the policy test suite in `tests/test_policy.py` must pass before anything merges.

## Tiers of autonomy

| Tier | Name | Allowed | Gate |
|---|---|---|---|
| 0 | Observe | Read metrics, logs, workload state | None |
| 1 | Advise | Publish a diagnosis and a proposed fix | None |
| 2 | Act with approval | Run one allow-listed runbook for one incident | A human approves that exact action |
| 3 | Act within bounds | Run pre-approved runbook classes in a named environment, inside blast-radius limits | Policy grant per runbook class + environment; audit; kill switch |

Rules that never bend:

- New environments start at Tier 1. Grants only ever widen one environment and one runbook class at a time.
- Every proposed action is evaluated by the policy engine and written to the audit log **before** execution — including denied ones.
- Approvals name a specific action on a specific incident. They expire; they cannot be reused or widened.
- `sre-agent halt` (or stopping the service) blocks all execution paths immediately. Reads may continue; mutations may not.

## Blast-radius limits

Each action is checked against limits set per environment, for example: maximum replicas changed at once, namespaces the agent may touch, and a hard deny-list (production databases, anything labelled `sre-agent: no-touch`). An action outside its limits is denied even if a human approved it — the approval flow must re-scope the action instead.

## Classification and the audit log (built, Phase 2)

- **Classification comes from one fixed catalogue in the policy code** (`RUNBOOK_CATALOG` in `policy.py`). It names the three read-only tools and the three reversible runbooks (restart, scale, rollback). A runbook that is not in the catalogue is unknown and is **denied** — it is never merely routed to a human for approval.
- **The pinned target is a blast-radius check.** When the caller passes the target pinned on the incident, an action aimed at any other target is denied.
- **The audit log is append-only** (`audit.py`). Every decision — allow, require approval, deny — is recorded before anything can run, in memory or as a JSON-lines file. A record stores a SHA-256 fingerprint of the action's parameters, never the parameter values, so secrets cannot leak into the log through a parameter.

## Approval flow (built, Phase 2)

- **An approval covers one exact action** (`approvals.py`): one runbook, one target, one set of parameters, one incident. The grant carries a SHA-256 fingerprint of the parameters, so approving one action can never be widened to another.
- **Approvals expire and are single-use.** A proposal waits 15 minutes; after that it must be proposed again. A grant is spent the moment it is used — even if the run then fails — so it can never be replayed for a second run.
- **Policy is checked again at approval time.** If the environment has been halted or tightened since the proposal, the policy's new denial stands, is written to the audit log, and the approval fails. This is the "denied even if a human approved it" rule above, enforced in code.
- **The approver is named and recorded.** An unnamed approval is refused, and the audit record for the approved ALLOW carries the approver's name (`approved_by`).
- **The CLI is the approval surface for now** (`sre-agent approve-demo`, demo targets only): the person at the terminal approves the exact proposal, by name, before anything runs. Approving live server incidents is not wired yet — the server holds no runbook state.

## Runbook library (built, Phase 2)

- **The library is exactly the catalogue's three fixes** (`runbooks.py`): restart, scale, rollback, for demo targets only. A test checks the library and the policy catalogue name the same set, so the two cannot drift apart.
- **Parameters are validated twice** — when a proposal is built and again inside the executor. Each runbook names the parameters it takes (scale takes `replicas`, 1 to 5; the other two take none). Anything else, including a parameter carrying a free-form command, is refused. There is no shell anywhere in the library.
- **The executor runs only on an ALLOW decision.** An action that still needs a human's approval does not run here; the approval flow (above) is what turns a human's yes into a runnable decision.
- **The blast-radius number is measured, not claimed.** `build_action` works out how many replicas an action touches from the workload's real state, so the policy engine judges a true number.

## Threat model

| Threat | Mitigation |
|---|---|
| Prompt injection in logs, alert text, or fetched pages ("ignore your instructions…") | Tool output is data, never instructions. Only schema-validated proposals can move the system, and only the policy engine can authorise them. A dedicated test set (`sre-agent injection-check`, `injection.py`) plants hostile text in every telemetry surface — log lines, alert summaries and annotations, a service status message, a metric label — and checks case by case that it changed nothing: the words stay in the evidence record as data and never reach a claim, a hypothesis, a diagnosis, a target, or an action. Measured 2026-11-09: 8 of 8 cases pass, 0 unsafe actions. One stated limit: the word "timeout" in an error log is read as a symptom signal by a plain rule, so a crafted line can raise that hypothesis — signal spoofing, not instruction following; it cannot add wording, actions, or targets. |
| The model proposes a dangerous or off-target action | Targets are pinned at incident creation; runbooks are allow-listed and parameter-validated; blast-radius checks run after approval as well as before. |
| Credential leakage into prompts or logs | Secrets are referenced by name and injected at call time only; tool output passes a redaction filter for common secret patterns; audit logs store parameter hashes, not secret values. |
| Runaway reasoning loops burning time and money | Per-incident budgets on steps, wall-clock and tokens; repeated identical queries are refused; on budget exhaustion the agent escalates to a human with its evidence so far. |
| The agent itself is down or misbehaving | It is a normal service: health endpoint, structured logs, and the halt switch. Its own failures get public postmortems in `docs/postmortems/`. |

## What "safe" claims mean here

Any safety claim in this repository must trace to a test: the policy suite, the evaluation harness scores published per release, or a postmortem. If it is not measured, the documentation says "not yet measured" — that honesty is deliberate, and it is the point of building this in the open.
