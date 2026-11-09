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

## Threat model

| Threat | Mitigation |
|---|---|
| Prompt injection in logs, alert text, or fetched pages ("ignore your instructions…") | Tool output is data, never instructions. Only schema-validated proposals can move the system, and only the policy engine can authorise them. A dedicated injection test set runs in the evaluation harness. |
| The model proposes a dangerous or off-target action | Targets are pinned at incident creation; runbooks are allow-listed and parameter-validated; blast-radius checks run after approval as well as before. |
| Credential leakage into prompts or logs | Secrets are referenced by name and injected at call time only; tool output passes a redaction filter for common secret patterns; audit logs store parameter hashes, not secret values. |
| Runaway reasoning loops burning time and money | Per-incident budgets on steps, wall-clock and tokens; repeated identical queries are refused; on budget exhaustion the agent escalates to a human with its evidence so far. |
| The agent itself is down or misbehaving | It is a normal service: health endpoint, structured logs, and the halt switch. Its own failures get public postmortems in `docs/postmortems/`. |

## What "safe" claims mean here

Any safety claim in this repository must trace to a test: the policy suite, the evaluation harness scores published per release, or a postmortem. If it is not measured, the documentation says "not yet measured" — that honesty is deliberate, and it is the point of building this in the open.
