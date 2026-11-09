# Architecture

Technical design for `sre-agent`. For the plain-English version, read [how-it-works.md](how-it-works.md) first.

## Components

| Component | Responsibility | Key constraint |
|---|---|---|
| Ingestion service | Receive Alertmanager and generic webhook alerts; normalise, deduplicate and group them into incidents | Stateless; all state lives in the incident store |
| Incident store | Persist incidents, timeline events, evidence, hypotheses, proposed actions, approvals, audit records | SQLite in v1 behind a repository interface; Postgres later |
| Reasoning core | Bounded plan → query → hypothesise → verify loop producing an evidence-cited diagnosis | Hard budgets on steps, wall-clock time and model tokens per incident; may conclude "unknown" and escalate |
| Tool layer | Typed, schema-validated, read-only queries: metrics (PromQL), logs, workload state, recent changes | Output is bounded in size, redacted for secret patterns, and always treated as untrusted data |
| Policy engine | Classify proposed actions into autonomy tiers; check blast radius, rate limits and environment pins; write audit records | Pure functions over explicit inputs; fully unit-tested; no model involvement in the decision |
| Runbook executor | Execute allow-listed, parameterised runbooks after policy approval | No shell, no free-form commands; parameters validated against a schema per runbook |
| Memory | Retrieve past incidents and postmortems by similarity; keep per-runbook success statistics | Advisory only — memory can inform a proposal, never approve one |
| Evaluation harness | Inject faults into the demo environment, run the agent, score the outcome | Unsafe action count is a release gate (must be zero) |
| Interfaces | CLI in v1; chat approvals and a small dashboard later | Approval is bound to a specific action on a specific incident; no blanket approvals |

## Data flow

1. Alert → ingestion → incident created with a pinned target (environment, namespace, service).
2. Reasoning core requests evidence through the tool layer; each result is appended to the incident as an evidence record with its query and timestamp.
3. Hypotheses are stored with pointers to supporting and contradicting evidence records.
4. A proposed action is a structured object: runbook id, validated parameters, target, and the diagnosis it follows from.
5. The policy engine evaluates the proposal and returns one of: `deny`, `require_approval`, or `allow` (Tier 3 only). The evaluation and its inputs are audit-logged.
6. Approved actions execute through the runbook executor; results and post-action verification are appended to the incident.
7. On resolution, the postmortem generator drafts from the incident record.

## The reasoning loop, concretely

```
while budget remains:
    context  = incident summary + retrieved memory
    step     = model proposes ONE of:
                 - a tool call (read-only query)
                 - a hypothesis update
                 - a proposed action (goes to policy engine, never direct)
                 - a conclusion (diagnosis, or "unknown — escalate")
    validate step against schemas and budgets
    execute reads immediately; route actions to policy
verify: after any action, re-query the alerting signal before closing
```

The model never talks to the executor. The only path to a mutation is: model proposal → schema validation → policy engine → (approval) → executor. That single choke point is the core of the safety design.

## Repository layout

```
src/sre_agent/
    models.py     incident, evidence, action and audit data models + state machine
    policy.py     autonomy tiers and the policy engine
    config.py     settings (environment-pinned targets, budgets, tier grants)
    agent.py      reasoning loop (lands in Phase 1)
    tools/        read-only tool implementations (Phase 1)
    server.py     webhook ingestion service (Phase 1)
tests/            unit tests; the policy suite is a required CI gate
docs/             this documentation set
PLAN.md           the living plan, refined as the build proceeds
```
