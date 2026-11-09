# SRE Agent — Deep Plan

*Project launched 8 November 2026. Owner: Abraham Kakooza. Repository: github.com/KakoozaJerry/sre-agent (public). This plan is refined hourly; the progress log at the bottom is the source of truth for what has actually been done.*

## 1. Vision

An open-source, autonomous Site Reliability Engineering agent that detects production incidents, triages them with evidence, and helps remediate them under strict, human-approved guardrails — then writes the postmortem and learns from it.

Most "AI SRE" demos stop at a chatbot that summarises alerts. This project aims higher: a working agent with a real tool layer, a real safety model, and a real evaluation harness, developed in the open. It is both a portfolio artefact and a research testbed for agentic AI in production operations: agent runtimes, harnesses, evaluation, and control — the same ground as Abraham's agentic AI red/blue teaming research direction.

## 2. The problem

On-call SRE work has a repeatable shape: an alert fires, someone pages through dashboards, correlates metrics, logs and recent deploys, forms a hypothesis, tries a known remediation, and verifies recovery. The slow parts are rarely the fix — they are detection-to-diagnosis time, context gathering across five tools, and the 3 a.m. cognitive load. Industry data consistently shows most incident time is spent diagnosing, not fixing.

An agent is well suited to the diagnosis half and to executing bounded, pre-approved remediations — if, and only if, it can be trusted not to make things worse. That trust problem is the actual product.

## 3. Design principles

1. **Safety before autonomy.** Autonomy is earned in tiers (Section 6). Every mutating action is classified, bounded, reversible where possible, and written to an immutable audit log before it runs.
2. **Evidence or it didn't happen.** Every diagnosis claim must cite the query that supports it (metric, log line, event). The agent never asserts a cause it has not observed.
3. **Telemetry is untrusted input.** Logs, alert text and web pages can contain prompt injection. All tool output is treated as data, never as instructions. This is a first-class threat model, not an afterthought, and it is where the red-teaming research feeds the engineering.
4. **Least privilege by construction.** Read-only credentials by default; write capability is scoped per tool, per environment, and granted per action tier.
5. **Evaluation-driven development.** No capability ships without a chaos-scenario test that measures it. Unsafe-action rate target: zero, gated in CI.
6. **Model-agnostic core.** The reasoning loop talks to a pluggable model backend (hosted APIs or local models) behind one interface, so the harness outlives any single model.
7. **Boring, operable software.** The agent itself must be deployable, observable and debuggable like any production service: structured logs, its own health endpoint, a kill switch.

## 4. Scope

**In scope (v1 line):**
- Alert ingestion from Alertmanager/Prometheus webhooks and a generic JSON webhook.
- Incident lifecycle management (open → triaging → diagnosed → mitigating → resolved → postmortem).
- Read-only diagnosis across metrics (PromQL), logs, Kubernetes state and recent changes.
- An allow-listed runbook library with parameter validation.
- Human approval workflow (CLI first; chat later) for mutating actions.
- A local demo environment where incidents are injected on purpose (chaos scenarios) so the agent can be evaluated end to end.
- Postmortem draft generation from the incident record.

**Out of scope (for now):**
- Managing the agent's own production deployment for real third-party infrastructure (the demo environment is the proving ground; real-cluster use is opt-in, read-only first).
- Replacing paging/on-call tools (PagerDuty etc. are integrations, not competitors).
- Multi-tenant SaaS. This is a self-hosted, single-operator tool first.

## 5. Architecture

```
                ┌────────────────────┐
 alerts ───────▶│ Ingestion (webhooks│
                │  + pollers)        │
                └─────────┬──────────┘
                          ▼
                ┌────────────────────┐      ┌──────────────────┐
                │ Incident store &   │◀────▶│ Memory: past     │
                │ state machine      │      │ incidents,       │
                └─────────┬──────────┘      │ postmortems,     │
                          ▼                 │ runbook stats    │
                ┌────────────────────┐      └──────────────────┘
                │ Reasoning core     │
                │ (plan → query →    │
                │  hypothesise →     │
                │  verify loop)      │
                └────┬───────────┬───┘
              reads  ▼           ▼  proposed actions
        ┌───────────────┐  ┌────────────────────┐
        │ Tool layer    │  │ Policy & guardrail │
        │ metrics/logs/ │  │ engine: classify,  │
        │ k8s/changes   │  │ bound, approve,    │
        └───────────────┘  │ audit              │
                           └─────────┬──────────┘
                                     ▼ approved only
                           ┌────────────────────┐
                           │ Runbook executor   │
                           │ (allow-listed,     │
                           │  parameterised)    │
                           └────────────────────┘
```

Components:

- **Ingestion.** FastAPI service receiving Alertmanager webhook payloads and generic alerts; normalises them into incidents; deduplicates and groups repeat firings.
- **Incident store.** An in-memory store behind a small interface first — Phase 1 taught that webhooks can be built and tested honestly without a database, and the webhook shapes must not change when the storage does. SQLite next (single file, zero ops), Postgres later. The incident record is the spine: timeline, evidence, hypotheses, actions, approvals.
- **Reasoning core.** A bounded loop with explicit state: gather context → rank hypotheses with supporting evidence → decide next query or propose action → verify outcome. Hard step and token budgets per incident; the loop must be able to say "I don't know" and escalate.
- **Tool layer.** Typed, schema-validated tools: `query_metrics` (PromQL), `search_logs`, `get_pod_status`, `get_recent_deploys`, `describe_service`. Each tool is read-only in v1 and returns bounded, redacted output.
- **Policy & guardrail engine.** Classifies every proposed action by tier (Section 6), checks blast radius (namespace, replica count, environment), enforces rate limits and maintenance windows, routes approvals, and writes the audit record *before* execution.
- **Runbook executor.** Runs only allow-listed, parameterised runbooks (restart workload, scale replicas, roll back deployment, clear disk pressure in demo). No free-form shell. Ever.
- **Memory.** Postmortems and incident outcomes feed retrieval for future diagnoses ("last time this alert fired, the cause was X"), with success statistics per runbook.
- **Evaluation harness.** A scenario suite that injects faults into the demo environment (pod kill, bad deploy, latency injection, disk fill, config error), runs the agent, and scores: time to diagnosis, diagnosis correctness, remediation correctness, unsafe actions (must be zero), escalation appropriateness.
- **Interfaces.** CLI (`sre-agent incidents`, `sre-agent approve <id>`) in v1; Slack/Teams approval buttons and a small web dashboard later.

## 6. Safety model — tiers of autonomy

| Tier | Name | What the agent may do | Gate |
|------|------|----------------------|------|
| 0 | Observe | Read metrics, logs, cluster state | None |
| 1 | Advise | Publish diagnosis and proposed remediation | None |
| 2 | Act with approval | Execute an allow-listed runbook for one incident | Human approves that specific action |
| 3 | Act within bounds | Execute pre-approved runbook classes in a named environment, inside blast-radius limits | Policy pre-approval + audit + instant kill switch |

Defaults: new environments start at Tier 1. Tier 3 is per-runbook-class, per-environment, never global. Every action, approved or not, is audit-logged with its evidence and reasoning. A single `sre-agent halt` (or SIGTERM) stops all execution paths.

**Threat model highlights:**
- Prompt injection through log lines, alert annotations, or fetched runbook text: mitigated by treating tool output as data, structured tool schemas, and never letting model output become a command without policy validation.
- Credential leakage: secrets are referenced by name, never embedded in prompts or logs; tool outputs are redacted for common secret patterns.
- Runaway loops: per-incident step, time and cost budgets; repeated identical queries are cut off.
- Wrong-target actions: environment and namespace are pinned in the incident record at ingestion; actions cannot retarget mid-flight.

## 7. Technology choices

- **Language:** Python 3.12+ (Abraham's strongest SRE/automation language; fast iteration; rich k8s/observability ecosystem).
- **Service:** FastAPI + uvicorn; pydantic models throughout; httpx for tool calls.
- **Storage:** SQLite via SQLModel for v1; interface kept swappable.
- **Demo environment:** Docker Compose — a small sample service, Prometheus, Alertmanager, and a chaos script set. Kubernetes (kind) arrives with the first k8s tools.
- **Testing:** pytest; scenario tests run the agent against recorded/synthetic telemetry before live chaos.
- **CI:** GitHub Actions — lint (ruff), unit tests, and the safety test suite (policy engine tests) as a required gate. Type checking (mypy) is in the dev tools and runs locally for now; it is not in CI yet, because the workflow file can only be changed through the GitHub website (the app connector cannot write workflow files — learned 2026-11-08). It joins CI the next time that file is edited there.
- **Packaging:** pyproject.toml, console entry point `sre-agent`; MIT licence.

## 8. Roadmap

**Phase 0 — Foundation (week 1)**
- [x] Repository, licence, README with vision and safety stance
- [x] This plan committed to the repo (`PLAN.md`)
- [x] Architecture and safety-model docs
- [x] Project skeleton: package layout, config, logging, CI green on empty tests
- [x] Incident data model + state machine with tests

**Phase 1 — Read-only diagnosis (weeks 2–3)**
- [x] Alertmanager webhook ingestion + generic webhook; dedupe/grouping
- [ ] Tool layer v1: PromQL query, log search (Loki or file-based in demo), service status
- [ ] Reasoning loop v1 producing an evidence-cited diagnosis report
- [ ] Demo environment v1: sample service + Prometheus + Alertmanager in Compose
- [ ] CLI: list incidents, show diagnosis

**Phase 2 — Approved remediation (weeks 4–5)**
- [ ] Policy engine: action classification, blast-radius checks, audit log
- [ ] Runbook library v1: restart, scale, rollback (demo environment targets)
- [ ] Approval flow in CLI; Tier 2 end-to-end
- [ ] Postmortem draft generation

**Phase 3 — Evaluation harness (weeks 6–7)**
- [ ] Chaos scenario suite (5 scenarios) with scoring
- [ ] Regression gate: unsafe actions = 0, diagnosis accuracy tracked per release
- [ ] Prompt-injection test set for telemetry inputs

**Phase 4 — Bounded autonomy (weeks 8–10)**
- [ ] Tier 3 policies for the demo environment; kill switch drills
- [ ] Memory: retrieval over past incidents; per-runbook success stats influence proposals
- [ ] Kubernetes tools via kind cluster; Datadog and Azure Monitor (KQL) read adapters

**Phase 5 — Hardening and release (weeks 11–12)**
- [ ] Security review of the agent itself; red-team report published in repo
- [ ] Docs: operator guide, runbook authoring guide
- [ ] v0.1.0 release with demo scenario walkthrough

## 9. Success metrics

- Diagnosis accuracy on the scenario suite ≥ 80% by end of Phase 3.
- Zero unsafe actions across all tests, always (hard gate).
- Median time-to-diagnosis in demo scenarios < 2 minutes.
- Every mutating action in the audit log with evidence and approver.
- The repo is runnable by a stranger in under 15 minutes (`docker compose up` → inject fault → watch the agent work).

## 10. Documentation standard

Documentation is a product feature of this project, not an afterthought (Abraham's direction, 8 November 2026):

- **Plain and simple wins.** Every document is written so a competent engineer who has never seen the project understands it on first read. Jargon is explained the first time it appears. If a section needs a diagram, it gets one (ASCII is fine).
- **Every change ships with its explanation.** Each phase item is only "done" when the README or docs explain what was built, why, and how to use it, in plain language.
- **A running plain-English log.** `docs/progress.md` in the repo records what changed, in ordinary words, one short entry per work session — so Abraham (or any reader) can follow the whole build without reading code.
- **Decisions are written down.** Any design choice that changes direction gets a short decision note in `docs/decisions/` — what was decided, why, and what it replaced.

## 11. Open-source strategy — proving reliability in public

The repository itself is the reliability evidence. The strategy (Abraham's direction, 8 November 2026):

- **Build in public.** Public roadmap (this plan), public progress log, small frequent commits with honest messages. Nothing is claimed that the evaluation harness has not measured.
- **Practise what it preaches.** The project holds itself to SRE standards: CI must be green to merge, releases carry published evaluation scores, and the project's own failures get short public postmortems in `docs/postmortems/` — an SRE tool that hides its own incidents would prove the opposite of reliability.
- **Low-friction adoption.** One-command demo, a `CONTRIBUTING.md`, labelled good-first-issues, and responsive issue handling. Adoption by strangers is the strongest reliability signal there is.
- **Steady cadence over big launches.** A visible, sustained commit and release rhythm reads as dependable; bursty hype does not.

## 12. Income path (long-term)

Income is a long-term goal, not a launch goal (Abraham's direction, 8 November 2026). The open-source core stays free under MIT — that is what builds the audience and the proof. The realistic sequence:

1. **Audience and proof (months 0–6).** GitHub stars, users running the demo, evaluation results worth citing. GitHub Sponsors switched on once there is something worth sponsoring.
2. **Services around the core (months 6–12).** Paid work that does not gate the code: implementation help for teams adopting the agent, safety evaluations of other people's agents (directly powered by the red-teaming research), and training/workshops on agent guardrails.
3. **Open-core paid tier (year 2, only if adoption justifies it).** The core stays open; a paid layer could add enterprise integrations (SSO, audit export, Datadog/Azure enterprise adapters), a hosted control plane, and support with response-time commitments. Nothing in the free core gets taken away to force the upgrade.

No revenue figures are promised anywhere in this plan; each stage is gated on the previous stage actually happening.

## 13. Resume and career value

This project is designed to be lifted straight onto a resume and into interviews:

- It demonstrates the exact stack of the moment: agent runtime and harness design, tool-use safety, evaluation harnesses, and production SRE judgement.
- Candidate resume bullets (to be finalised from measured results, never aspirations): "Designed and built an open-source autonomous SRE agent…", with the evaluation numbers from Section 9 once they exist.
- It gives interviewers something real to open: the repo, the safety model, the postmortems.

## 14. Risks and open questions

- **Model access and cost.** Live reasoning needs a model API key or a local model. The codebase will run fully in a deterministic "mock model" mode so contributors and CI need no key; Abraham decides which real backend to wire first.
- **Scope creep.** The roadmap is deliberately narrow (demo environment, five scenarios). New integrations wait until Phase 4.
- **Trust claims.** All public claims about capability must trace to the evaluation harness output, not aspiration. The README states plainly what the agent cannot yet do.
- **Time.** Built in hourly refinement passes alongside Abraham's other commitments; phases are sized so each pass lands something verifiable.

## 15. Operating loop

Every hour, a refinement pass: (1) compare repo state against this plan, (2) refine one section of the plan where reality has taught something, (3) action the next unchecked roadmap item that can be completed and verified in the pass, (4) commit with a clear message, (5) append to the progress log below. Progress is reported to Abraham when a pass lands something meaningful — a phase item checked off, a test suite green, a design decision changed — not as hourly noise.

## 16. Progress log

- **2026-11-08 21:11 ET** — Hourly pass: Phase 1 started; first roadmap item checked off. Alert ingestion is in (`ingestion.py` + `server.py`): an Alertmanager webhook and a simple generic webhook, both turning alerts into incidents in an in-memory store. The same alert firing again is counted as a duplicate, not a new incident; a different alert for the same target and alert name joins the open incident as evidence; a resolved alert closes the incident so the next firing starts fresh. FastAPI moved into the main dependencies, since the server is now real code, and httpx into the dev tools for endpoint tests. 14 new tests — 33 pass locally, ruff and mypy clean, safety suite included. Plan refined in one place (Section 5): the incident store is memory-first behind a small interface, SQLite next; the webhook shapes will not change when the storage does.
- **2026-11-08 20:04 ET** — Hourly pass: Phase 0 completed and checked off. Added the one missing skeleton piece, structured logging (`logging_config.py`: one setup, JSON lines, unknown levels rejected, safe to call twice), plus a `log_level` setting and 5 new tests — 19 tests pass locally, ruff clean, safety suite included. Plan refined in one place (Section 7): it claimed CI runs mypy type checking; it does not — CI runs ruff + tests, mypy is local-only until the workflow file is next edited on the website. That correction is now written into the plan.
- **2026-11-08 19:20 ET** — Repo fully populated (17 files) and CI green on run #4 (lint + 14 tests, incl. the safety suite) after fixing the 3 ruff errors CI caught. push_files (git trees API) is refused by the app; per-file Contents API works; the workflow file went in via the web UI.
- **2026-11-08 19:07 ET** — Public repo created at github.com/KakoozaJerry/sre-agent (via browser). First file set built and verified locally (14 tests pass) but the GitHub connector cannot write to the new repo until Abraham adds it to the Muse GitHub App's repository access — flagged to him as a one-time settings tap.
- **2026-11-08 19:02 ET** — Abraham's direction added: documentation plain and simple, income as a long-term goal via an open-core path, and an open-source strategy that proves reliability in public. Plan updated (Sections 10–13).
- **2026-11-08 19:00 ET** — Project launched at Abraham's direction. Deep plan v1 written. Goal created ("Open-source SRE agent build"). Public GitHub repository creation requested. Hourly refinement loop being set up.
