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
- **Two approval surfaces, one flow.** The CLI (`sre-agent approve-demo`, demo targets only): the person at the terminal approves the exact proposal, by name, before anything runs. And, since Phase 7, the server itself (when built with a policy and a runbook backend): a proposal for a diagnosed incident is held by the server as a pending approval, and a named human approves or rejects it over HTTP under exactly the rules above — 15-minute expiry, single use, policy re-checked at approval time. A server proposal cannot name its own target: the target is the one pinned on the incident. A run the server approves moves its incident to "mitigating", never straight to "resolved" — the server has not checked the fix worked.

## Model-proposed actions (built, Phase 9)

- **A model may propose, never approve.** A language model proposes a fix only through the separate proposal channel (`model_proposals.py`), off by default and wired nowhere in the shipped code. Its reply is one JSON object with only a runbook id, parameters and a rationale; a reply carrying any other field — a target, an approver, a decision — is refused whole, so a model proposal cannot be retargeted or self-approved.
- **The same path, literally.** A model proposal is built by the same `build_action` as any other (parameters validated, blast radius counted from the workload's real state, target pinned on the incident), decided by the same policy engine under the same tiers, and written to the same audit log before anything can run. Tests compare a model proposal's decision and audit record field by field with a hand-built identical proposal: they match.
- **Approval still needs a named human.** A model proposal that needs approval becomes the same pending action, with the same 15-minute expiry and single-use grant; the proposer has no approve step and a submission can produce a pending action, never a grant. A model proposal runs without a fresh human yes only where the policy itself allows it outright — a Tier 3 grant a human wrote into the policy in advance.

## Tier 3 in the demo, and kill switch drills (built, Phase 4)

- **The demo grant is named in one place** (`tier3.py`): in the demo environment, in the `shop` namespace only, restart and scale are pre-approved, one action may touch at most three replicas, and rollback is *not* pre-approved — it changes which version runs, so it still needs a human's yes every time. No other environment has a Tier 3 grant.
- **The Tier 3 runner runs only on an ALLOW** (`execute_tier3`): the policy decision is written to the audit log before anything runs, a decision that still needs approval runs nothing here (a human is asked through the Tier 2 flow, not skipped), and a denied action runs nothing.
- **The halt is checked at run time.** Every run asks the policy engine again, so a halt pressed after a proposal but before a run stops that run — the same rule the approval flow follows at approval time.
- **The drills rehearse all of this** (`sre-agent tier3-check`): seven drills — the two pre-approved fixes run and are audited; rollback does not run on its own; a fix touching five copies is refused; a fix outside `shop` is refused; a halt pressed *between* two runs stops the second and changes nothing; and while halted, read-only checks are still allowed. Measured 2026-11-09: 7 of 7 drills pass, 3 fixes run across the set.

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

## Security review of the agent itself (Phase 5)

The agent's own surface was reviewed and red-teamed at the start of Phase 5; the full report is [security-review.md](security-review.md), and its probes run as `sre-agent security-check` (the `security_review` package). Measured 2026-11-09: 10 of 10 probes pass — a made-up runbook is denied, a free-form command smuggled in as a parameter is refused, a retargeted fix is denied, planted secrets stay out of the audit log (hashes only) and are redacted in tool output for the shapes the filter covers, a used approval cannot be replayed, Tier 3 stays inside its grant, management commands and broken queries are refused, no module in the package can run a shell, and alert text cannot close an incident. The review also recorded eight findings the probes cannot prove away — the largest, in plain words: **the webhook server has no authentication**, and ingestion has no size or rate limits. Both are accepted only for a server that runs on one operator's own machine, and both must be fixed before any shared use; the report says so, and the probe command prints the findings on every run so they cannot be quietly forgotten.

## What "safe" claims mean here

Any safety claim in this repository must trace to a test: the policy suite, the evaluation harness scores published per release, or a postmortem. If it is not measured, the documentation says "not yet measured" — that honesty is deliberate, and it is the point of building this in the open.
