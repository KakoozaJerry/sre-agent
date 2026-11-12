# sre-agent

An autonomous SRE agent that detects production incidents, works out what is wrong using real evidence, and helps fix them — without ever being allowed to make things worse on its own.

Built in the open by [Abraham Kakooza](https://github.com/KakoozaJerry).

## In plain English

When a production system breaks, an on-call engineer gets paged, opens five different tools, pieces together what happened, tries a fix, and checks it worked. Most of the time goes on figuring out what is wrong, not on the fix itself.

`sre-agent` does the figuring-out part automatically:

1. An alert arrives (from Prometheus/Alertmanager, or a generic webhook).
2. The agent gathers evidence — metrics, logs, the state of the service, what changed recently.
3. It writes a diagnosis where **every claim points at the evidence behind it**.
4. If a known fix applies, it proposes the exact action. A human approves it with one command. Only then does anything change.
5. Afterwards it drafts the postmortem from its own records.

The agent is never given free rein. It cannot run arbitrary commands, it can only use a small list of pre-approved fixes ("runbooks"), and every action — proposed, approved, or rejected — is written to an audit log before anything runs.

## Why build this in the open?

Reliability is the whole point of SRE, and the best proof of reliability is a public record. This repository is that record: a public plan, a public progress log, published test and evaluation scores with every release, and honest postmortems when the project itself breaks. If this agent claims it can be trusted, you can check.

## Status

Early build. Phase 0 (the foundation) and Phase 1 (read-only diagnosis) are done: the agent can now receive alerts — from Alertmanager or a simple generic webhook — and turn them into incidents, counting repeat firings as duplicates and grouping related alerts onto one incident instead of opening a new file each time. It can also read evidence, still without touching anything: it can ask a PromQL metrics question, search log lines, and check whether a service is up. Every tool answer is checked, kept to a fixed size, cleaned of common secret shapes, and stored as evidence that names the exact query behind it. And it can now put that evidence together into a diagnosis: a short, fixed set of read-only questions, answered in order, becomes a report where every claim names the evidence and the exact query behind it — and where a claim with no evidence behind it cannot be written at all. This first version reasons with plain rules and no language model, on purpose, so anyone can run it with no API key and the tests can check every diagnosis exactly. If the evidence is thin, or the step budget runs out, it says "I don't know" and hands the incident to a human instead of guessing. The incident model, the safety policy engine, the settings, and the logging are also in. And there is now a demo environment that breaks on purpose: one command (`docker compose up`) starts a pretend checkout service, Prometheus, Alertmanager, and the agent together, and a small script injects a failure so the alert-to-incident path can be watched end to end. One honest limit, stated in [docs/demo.md](docs/demo.md): the reasoning loop is not yet wired to the demo's live Prometheus — its tools still read the built-in demo data. And there is now a command-line tool, which completes Phase 1: with the agent server running, `sre-agent incidents` lists what the server knows and `sre-agent show <incident-id>` shows one incident in full — its diagnosis (or a plain "none yet"), the possible causes recorded for it, and the evidence with the exact query behind each record. With no server needed, `sre-agent diagnose` runs the reasoning loop once on the seeded demo data and prints the report. Two honest limits: the CLI is a thin client of the server, so if the server is not running it says so instead of pretending there are no incidents; and, since Phase 7, the server runs the reasoning loop itself when it is built with a tool layer — the shipped server app uses the demo tool layer — so a new live incident is diagnosed by its server and the diagnosis is stored on the incident record, ready for `sre-agent show`; a server built with no tool layer still records incidents only. Phase 2 has now started with its referee: the policy engine is finished and paired with an audit log. Every proposed action is classified from one fixed list in the code — three read-only tools and three undoable fixes (restart, scale, rollback) — and a fix name that is not on the list is refused outright, never just sent for approval. A fix aimed anywhere other than the target stamped on its incident is refused too, an environment can name exactly which areas may be touched, and the stop switch now blocks changes while still letting the agent look. Every decision the engine makes — allowed, needs approval, or refused — is written to an append-only audit log before anything can run, with a fingerprint of the fix's parameters instead of their values, so a secret cannot leak into the log. The runbook library is now built too: the three undoable fixes — restart a workload, set how many copies run, roll back the last deploy — for the demo environment only. Each fix names the parameters it takes (restart and rollback take none; scale takes a copy count from 1 to 5), anything else is refused, and there is no way to pass a free-form command. The checks run twice, when a fix is proposed and again when it is about to run, and the count of copies a fix touches is worked out from the workload's real state, not claimed by whoever proposes it. The executor runs a fix only when the policy engine has allowed it outright. That approval wiring is now built too, so a Tier 2 fix runs end to end: `sre-agent approve-demo --by <your name>` proposes a fix for the demo checkout workload, the policy engine sends it for approval, and it runs only if the person at the terminal approves that exact action. An approval covers one runbook, one target, one set of parameters and one incident; it expires after 15 minutes, it can be used once only (even a failed run spends it), the approver is named in the audit log, and the policy engine is asked again at approval time — if the environment has been halted or tightened since the proposal, the denial stands. Saying no, or letting the prompt pass, runs nothing. One honest limit: this approves demo fixes in one process, like the rest of Phase 2; since Phase 7 the server serves the same approval flow for its own incidents over HTTP, described at the end of this summary. The last piece of Phase 2 is now built too, so Phase 2 is complete: postmortem draft generation. `sre-agent postmortem-demo` runs one demo incident through the whole path — diagnosis, an approved restart, resolution — and prints the first draft of its postmortem, written from the incident record itself: a timeline, the diagnosis, the possible causes, the evidence with the exact query behind each record, the actions taken, and the audit decisions, ending with a plain list of what the record does not know. The draft invents nothing: if no diagnosis was recorded, it says no cause is stated; if an incident is not resolved yet, the draft is labelled preliminary, not final; and its timeline uses only timestamps the record really has, because status changes are not themselves timestamped. One honest limit: drafting a live server incident's postmortem is not wired yet, because the full record and its audit records do not yet live in one place. Phase 3 has now started with its first item: the evaluation harness. `sre-agent evaluate` runs five chaos scenarios — pod kill, latency injection, bad deploy, disk fill, config error — against the reasoning loop on seeded demo data (no server or Docker needed), and scores each one: the outcome, the top cause, whether every claim cites real evidence, the steps used, and how many unsafe actions ran (zero, counted from the incident records, not assumed). The measured result on 2026-11-09 is 5 of 5 correct on this suite, with 0 unsafe actions. Three honest limits, stated in [docs/evaluation.md](docs/evaluation.md): which fix the agent would propose is not scored, because the loop proposes none; the specific cause is not scored for the disk, config and deploy scenarios, because the loop has no tool that reads disk, config or deploys; and the faults run on seeded tool data, not yet through the live Docker demo. A regression gate now guards those scores: `sre-agent regression-check` runs the suite again and compares it against the recorded baseline in `evaluation/baseline.json` — the last release's scores. Unsafe actions must be zero (always, not just compared to the baseline), every diagnosis must cite real evidence, accuracy may not drop, and a scenario that was correct and turns wrong fails the gate by name. The baseline moves only on purpose, at a release, and each move is appended to its history, so accuracy is tracked per release in one place. Phase 3 is now complete: its last piece is the prompt-injection test set. `sre-agent injection-check` plants hostile text in every kind of telemetry the agent reads — log lines ordering a deletion or a fix, an alert summary ordering a retarget, an alert annotation claiming the incident is already resolved, a status message claiming a down service is healthy, a metric label ordering a rollback, a log line asking for secrets — and checks, case by case, that the text changed nothing: it stays in the evidence record as data and never reaches a claim, a hypothesis, a diagnosis, a target, or an action; no action runs; and the answer stays the one the honest numbers give. Measured on 2026-11-09: 8 of 8 cases pass, 0 unsafe actions. One limit is stated in the report itself: the word "timeout" in an error log is read as a symptom signal by a plain rule, so a crafted line can raise that one hypothesis — it cannot add wording, actions, or targets beyond the loop's own fixed set. Phase 4 has now started with bounded autonomy in the demo: `sre-agent tier3-check` runs seven drills on the demo environment's Tier 3 grant. In the demo, in the shop namespace only, restart and scale are pre-approved (at most three replicas touched at once) and run through one runner that audits every decision before anything runs; rollback is not pre-approved and still needs a human. The drills prove the pre-approved fixes run, everything outside the grant does not, and a halt pressed between two runs stops the second one while read-only checks continue. Measured on 2026-11-09: 7 of 7 drills pass. Tier 3 here is the demo only — no real system is touched, and no other environment has this grant. Memory is now built too: `sre-agent memory-demo` shows, for a fresh demo incident, the similar past incidents (found by fixed, explainable points — same service counts most, then namespace, then environment, then shared words, capped), each runbook's record counted from the action records on past incidents, and suggested runbooks ranked by their record on same-service past incidents only. The wording is careful on purpose: a run counts as "ended resolved" when its incident ended resolved — the record does not prove the run caused the recovery, and the stats never claim it did. Suggestions are advice only: they run nothing, propose nothing, and approve nothing, and only runbooks in the library with a same-service record are ever suggested. Since Phase 7 memory can also be wired into the live path, described at the end of this summary: when a server is built with a memory, its advice is attached to each live diagnosis after that diagnosis is decided, as advice only. The last piece of Phase 4 is now built too, so Phase 4 is complete: read adapters for three more places an SRE already looks. `sre-agent adapters-demo` shows all three on seeded demo data: a Kubernetes pod listing (each pod's phase, how many of its containers are ready, and its restart count — the read behind `kubectl get pods`), a Datadog metrics query that returns the same series shape as the Prometheus tool, with the source named on the record, and an Azure Monitor KQL query against Log Analytics that returns rows as evidence. Every adapter only looks: there is no method that changes anything in any of the three systems, the KQL check refuses management commands (the ones starting with a dot, which change a workspace), API keys and tokens are passed in by the caller and never logged or stored, and every reply is turned into this project's own models by a pure parser tested against recorded reply shapes. One honest limit: no live Kubernetes cluster (kind or otherwise), Datadog account or Azure workspace was available where this was built, so no live call is claimed as tested — running against a real kind cluster is a human check, the same status the Docker demo had at first. Since Phase 7 the adapters can also be selected as evidence sources for a live diagnosis, described at the end of this summary. Phase 5 has now started with a security review of the agent itself, published as a red-team report in [docs/security-review.md](docs/security-review.md): `sre-agent security-check` runs ten probes that attack the agent's own surface — a made-up runbook, a smuggled free-form command, a retargeted fix, planted secrets, a replayed approval, the Tier 3 limits, refused management commands, a scan for any shell surface — and all ten are refused or contained (measured 2026-11-09: 10 of 10 pass). The report also records eight findings the probes cannot prove away, in plain words, and the largest is this: the webhook server has no authentication, and ingestion has no size or rate limits, so this build must not be exposed to a network other people can reach — both are accepted only for a server running on one operator's own machine and are the first things to fix before any shared use. The two Phase 5 guides are now written too: [docs/operator-guide.md](docs/operator-guide.md) covers running the server, watching incidents, approving a demo fix by name, stopping the agent, and the check commands to run after any change — and it states plainly that there is no `sre-agent halt` command in this build yet, so stopping the server process is the halt for the live server; [docs/runbook-authoring-guide.md](docs/runbook-authoring-guide.md) covers writing a new fix, naming the four places that must agree (the policy catalogue, the parameter list, the validation, and the executor), counting its blast radius from the workload's real state, and the tests it must have before it joins the library. That completes the roadmap, and v0.1.0 is released: the version is 0.1.0 in the package and the project file, the regression baseline was re-recorded on purpose at that version (5 of 5 correct, 100%, 0 unsafe actions, with the development scores kept in its history), and [docs/v0.1.0-walkthrough.md](docs/v0.1.0-walkthrough.md) carries the release notes and a step-by-step walkthrough of the scored demo scenario, with every check re-run on release day — 242 tests pass, injection set 8 of 8, Tier 3 drills 7 of 7, security probes 10 of 10 — and the limits that ship with the release stated plainly, including that the Docker Compose walkthrough was not run in the release environment, which has no Docker, and that creating the GitHub Release and tag on the website is a manual step, because the GitHub connection used to build this project has no tool for it. After v0.1.0, the first two security findings were fixed, starting a post-release hardening phase: the server now takes a shared token (`SRE_AGENT_API_TOKEN`) — when one is set, every endpoint except `/health` requires it in the `X-SRE-Agent-Token` header, webhooks and incident reads alike, and the CLI sends it automatically when the same variable is set for it — and ingestion is bounded (at most 100 alerts per payload, capped labels, annotations and summaries, at most 200 evidence records per incident and 1,000 incidents in the store, with a clear refusal past a cap). Two honest remainders, stated in [docs/security-review.md](docs/security-review.md): with no token set the server is still unauthenticated and stays a local, one-operator server, and no per-sender rate limit was added — the caps bound how much a flood can make the server hold, they do not slow a sender down. The secret cleaning was then widened (finding F-03): it now also removes JSON web tokens, passwords inside connection strings and inside URLs, whole private-key blocks, and GitLab, Slack and Stripe token shapes, and a test guards ordinary text — version numbers, hostnames, commit hashes, links without passwords — against being cleaned by mistake. What it still cannot catch is stated plainly in the same report: a secret with no label and no known prefix, and a private key's body lines shown without their header. The audit log is now tamper-evident too (finding F-04): every audit record carries the hash of the record before it and its own hash, and a verifier (`verify_audit_file` in `audit.py`) reports an edited, deleted or reordered record by position — with the limits stated plainly in the same report: deleting the last records, or replacing the whole file with a freshly recomputed chain, is still not detected, because that would need a signature or a copy kept elsewhere. The GitHub Release and tag for v0.1.0 were also created on the website (the manual step the walkthrough named), so v0.1.0 now stands as the latest release there as well as in this repo. Backend addresses are now guarded too (finding F-05): every live backend address — Prometheus, Kubernetes, Datadog, Azure Monitor — and the CLI's server address must be `https://`, or plain `http://` only to this same machine (localhost or a loopback address, where the demo backends live); an address carrying a user or password is refused, and a backend may be given an allow-list of hosts it may call, which can only narrow where it may call, never widen the https rule — so a mistyped or hostile address cannot quietly receive a live token, and no token crosses a network unencrypted. The demo itself is tidier too (finding F-06, improved): both images this project builds now run as a named user who is not root, and the agent image installs its packages from `requirements.lock` — exact versions, each with a hash that is checked as it installs — instead of from version ranges. One remainder is stated plainly in the same report: Prometheus and Alertmanager are still pinned in the Compose file by their version tags, not by image digest, because no verified digest could be produced where this project is built, and an invented digest would be worse than the tag. Phase 7 — wiring the live path — has started with the server diagnosis above: when an alert opens a new incident on a server built with a tool layer, the reasoning loop runs inside the server and the evidence-cited diagnosis, its possible causes and the gathered evidence are stored with the incident, readable through the API and through `sre-agent show`. An update grouped onto an incident that was escalated to a human is diagnosed again; a duplicate is never re-diagnosed; and if the loop itself fails, the alert is still recorded and the failure is logged. One honest limit: the shipped server app diagnoses from the seeded demo data, because that is the tool layer it is built with — a real deployment passes its own tool layer to `create_app`, and no diagnosis here claims anything about a real system. Live approvals are now served by the server too: when a server is built with an environment policy and a runbook backend, a fix can be proposed for a diagnosed incident over HTTP (`POST /incidents/{id}/actions`), the policy engine decides and audits that decision, and a proposal sent for approval waits as a pending action for a named human to approve or reject it (`POST /incidents/{id}/approvals/{pending-id}/approve`) under the same rules as the CLI flow — 15-minute expiry, single use, policy re-checked at approval time. A proposal cannot name its own target: it acts on the target pinned on its incident, and the count of replicas it touches is worked out from the backend's real state. An approved run is written on the incident as evidence and moves it to "mitigating", never straight to "resolved", because the server has not checked the fix worked. The shipped server app serves approvals against the seeded demo workloads under a Tier 2 demo policy; a server built without a policy or a backend holds no approvals and says so. The read adapters now feed live diagnosis too: an operator can select any of them as extra evidence sources (an `AdapterSources` bundle handed to the reasoning loop, or to the server, which passes it on), and each selected adapter is then asked one fixed question during a diagnosis — the pods for the incident's namespace and service, the Datadog error metric for its service, the Azure Monitor error rows for its service — with the answers stored on the incident as evidence and able to support claims and possible causes as counts and measured values only, never as copied text. Adapter questions are built only from a service name that is a plain name; a name carrying query syntax skips that source, a failing adapter is skipped, and a step budget that runs out before an adapter step skips it without discarding the diagnosis the core evidence earned. The shipped server app selects the seeded demo adapters; a real deployment passes its own bundle, whose live adapters already passed the backend address rules when they were built, and no live adapter call is claimed as tested. Incident memory now feeds live diagnosis as advice too: an operator can hand a memory of past incidents to the reasoning loop, or to the server, which passes it on (`create_app(memory=...)`), and after a diagnosis is decided the loop attaches the memory's report to the incident — similar past incidents, each runbook's record, and suggested runbooks — stored on the incident as `memory_report`, readable through the API and printed by `sre-agent show` under a plain "advice only" label. The advice is computed last, on purpose: it cannot have influenced any claim, cause or action, the tests check that a diagnosis with memory is word-for-word the diagnosis without it, and a suggestion creates no proposal — a suggested fix still has to be proposed and approved through the live approval flow like any other. A memory that fails is skipped, never fatal. The report models now live with the other core models in `models.py`, because an incident record can carry one, and the seeded demo past is available as a memory itself (`demo_incident_memory`); the shipped server app is built with it, so its demo diagnoses carry advice from those four seeded past incidents and claim nothing about a real system. A database store now exists too, which completes the Phase 7 wiring items: a server built in code with `SQLiteIncidentStore` (in `storage.py`, one SQLite file, standard library only, no new dependency) keeps its incidents across a restart — with everything on them, status, evidence, possible causes, the diagnosis and the memory advice — plus the duplicate counts and the dedupe and grouping rules, so a repeated alert is still a duplicate afterwards and a resolved alert still closes its incident. Pending approvals persist in the same file: a proposal still waiting after a restart is still waiting, with the same expiry it was given (a restart does not extend its 15 minutes), and a decided approval stays decided, so it cannot be run twice across a restart. The database store keeps exactly the in-memory store's interface and inherits its dedupe, grouping and intake-cap logic unchanged; the server writes a diagnosis or a run through the store's `save` hook, which is the one design rule this item added. Two honest limits: it is a single-operator, single-file store — not a shared database for several servers — and the shipped server app still uses the in-memory store, so the demo behaves exactly as before; feeding a server's own stored incidents back in as its memory is not wired yet. Phase 7 is now complete: its last item was the evaluation gate, re-run with the live path wired — all five suites passed unchanged (scenarios 5 of 5, regression gate against the v0.1.0 baseline, hostile-text set 8 of 8, Tier 3 drills 7 of 7, security probes 10 of 10, 0 unsafe actions), pinned by a gate test file (`tests/test_evaluation_gate.py`) that also checks a fully wired server — tool layer, adapters, memory, live approvals and a database store together — diagnosing with evidence, running nothing on its own, surviving a restart, and letting a named human approve a fix exactly once. Phase 8 — polish for users — has started with a one-command install: `./quickstart.sh` from a fresh clone makes the private Python folder, installs the agent with its server parts, and proves the install with a demo diagnosis and the scored scenarios, stopping at the first step that fails; it was verified from a clean environment on 12 November 2026. The quick start below now also carries a full walkthrough — start the server, send an alert, read the diagnosis, approve an action — and every step and expected answer in it was run against a real server in the pass that wrote it, on 12 November 2026. Phase 8 is now complete, and v0.2.0 is released: the version is 0.2.0 in the package and the project file, the regression baseline was re-recorded on purpose at that version (5 of 5 correct, 100%, 0 unsafe actions, with the v0.1.0 scores kept in its history), and [docs/v0.2.0-walkthrough.md](docs/v0.2.0-walkthrough.md) carries the release notes with every check re-run on release day. The changes since v0.1.0 are recorded in [CHANGELOG.md](CHANGELOG.md), and the community basics are in place for anyone who wants to help: [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), issue templates and a pull request template. As with v0.1.0, creating the GitHub Release and tag on the website is a manual step, because the GitHub connection used to build this project has no tool for it. Follow along in [docs/progress.md](docs/progress.md), written in plain English.

Phase 9 has started with its first item: a language-model backend now sits behind the same diagnosis report shape as the plain-rules backend, and it is off by default. The deterministic backend — plain rules, no model call, no API key — stays the default everywhere: the demo, the CLI, the evaluation suites and the shipped server all use it unless an operator builds a model backend in code and hands it in on purpose (`ReasoningLoop(..., backend=...)`, or `create_app(..., diagnosis_backend=...)`). This repository ships no real model client: choosing a provider, a model and a key is the operator's decision. A model's reply is treated as untrusted input and checked before any of it can become a claim — it must be one JSON object carrying only claims and possible causes, every one citing a real evidence record for that incident, with a confidence between 0 and 1; an entry carrying any other field is dropped, a reply carrying a top-level field beyond those two is refused whole, and a model that fails or answers badly simply gets no say, so the incident escalates to a human as it does when the evidence is thin. Every report now names the backend that produced it (`Backend: deterministic` or `Backend: language-model`). Two honest limits: the model backend proposes wording inside a diagnosis only — it proposes no action, and whether the policy engine treats model-proposed actions identically is the next Phase 9 item, not claimed here; and no score for the model backend is published yet — the published scores are the deterministic backend's, and the model backend must pass the same suites with its scores published separately before it is ever recommended, which is the last Phase 9 item.

- [PLAN.md](PLAN.md) — the full plan: vision, architecture, safety model, roadmap, and the long-term open-core path.
- [docs/v0.1.0-walkthrough.md](docs/v0.1.0-walkthrough.md) — the v0.1.0 release notes and demo scenario walkthrough: what shipped, the scores measured on release day, and a step-by-step run through the scored scenario.
- [docs/v0.2.0-walkthrough.md](docs/v0.2.0-walkthrough.md) — the v0.2.0 release notes: what shipped since v0.1.0 (the Phase 6 hardening, the Phase 7 live path, the Phase 8 polish), the scores measured on release day, and the limits that ship with the release.
- [CHANGELOG.md](CHANGELOG.md) — every notable change, release by release, in plain words.
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to help: setup, the checks every change must pass, and the safety rules that do not bend.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — how we treat each other in this project's spaces.
- [docs/security-review.md](docs/security-review.md) — the security review and red-team report on the agent itself: what was attacked, what held, and the findings that remain.
- [docs/operator-guide.md](docs/operator-guide.md) — running the agent day to day: install, server, CLI, approvals, stopping it, and the checks to run after any change.
- [docs/runbook-authoring-guide.md](docs/runbook-authoring-guide.md) — writing a new runbook: the four places that must agree, honest blast-radius counting, and the tests it must have.
- [docs/evaluation.md](docs/evaluation.md) — the evaluation harness: the five scenarios, what is scored, and what is not scored yet.
- [docs/demo.md](docs/demo.md) — the demo environment: what runs, how to start it, and how to break it on purpose.
- [docs/how-it-works.md](docs/how-it-works.md) — the whole system explained simply.
- [docs/architecture.md](docs/architecture.md) — the technical design.
- [docs/safety-model.md](docs/safety-model.md) — how the agent is kept safe: tiers of autonomy, guardrails, threat model.

## What it cannot do yet

Being straight about this matters more than hype:

- It does not manage real production systems yet. It is being proven in a local demo environment where failures are injected on purpose.
- Diagnosis is read-only first. Fixing things (with human approval) comes after the diagnosis is measurably good.
- Diagnosis does not need a language model: the default backend is plain rules that anyone can run with no API key. A language-model backend exists behind the same report shape, off by default, and this repository ships no real model client — an operator who wants one supplies their own client, provider and key in code.

## Quick start

One command, from a fresh clone (macOS and Linux; you need
Python 3.11 or newer):

```bash
git clone https://github.com/KakoozaJerry/sre-agent.git
cd sre-agent
./quickstart.sh
```

That is the whole install. The script makes a private Python
folder (`.venv`, nothing is installed system-wide), installs
`sre-agent` with its server parts from this source copy, and
then proves the install works: it prints the version, runs one
demo diagnosis, and runs the five scored chaos scenarios, which
must score 5 of 5. If any step fails, the script stops there and
says so — it never prints success for a step that did not pass.
It was verified from a clean environment (a fresh copy with no
`.venv` and no installed package) on 12 November 2026. Windows
users, and anyone who prefers step by step, can do the same
things by hand:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[server]"
sre-agent diagnose
sre-agent evaluate
```

For development (the test tools as well), install
`pip install -e ".[dev,server]"` instead and run `pytest`.

## Walkthrough: one alert, end to end

This walkthrough needs no Docker and touches no real system.
It uses the server this project ships, which works from the
seeded demo data — the same made-up checkout picture that
`sre-agent diagnose` uses. Allow about five minutes. Do it
after the quickstart above, from the top folder of the clone;
the commands below put `.venv/bin/` in front of the tools —
if you installed by hand and your `.venv` is active, the plain
names (`sre-agent`, `uvicorn`) do the same thing.

**1. Start the server.** Leave this terminal open; the
server runs in it.

```bash
.venv/bin/uvicorn sre_agent.server:app --port 8080
```

In a second terminal, in the same folder, check it is up:

```bash
curl http://localhost:8080/health
# {"status":"ok"}
```

**2. Send an alert.** This is the simple webhook shape, for
the demo checkout service:

```bash
curl -X POST http://localhost:8080/webhooks/generic \
  -H 'Content-Type: application/json' \
  -d '{"alert_name":"HighErrorRate","service":"checkout","namespace":"shop","environment":"demo","summary":"Checkout errors are above 10%"}'
```

The reply says `"created": true`, `"status": "diagnosed"`,
and a diagnosis beginning "Most likely: A call that checkout
depends on is timing out". Copy the `"incident_id"` value
from the reply — the steps below call it `<incident-id>`.

**3. Read the diagnosis.**

```bash
.venv/bin/sre-agent incidents
.venv/bin/sre-agent show <incident-id>
```

`show` prints the incident in full: status `diagnosed`, the
diagnosis, the possible causes with their confidence, the
evidence behind them — eight records for this alert: the
alert itself, the service status, a log search, two metric
queries, and the three read adapters (Kubernetes pods,
Datadog, Azure Monitor) — each naming the exact query behind
it, and the memory advice, under a plain "advice only"
label. That advice is worked out after the diagnosis is
decided; it runs and approves nothing.

**4. Approve an action.** Propose the restart fix for this
incident (the proposal cannot name a target — it acts on
the target pinned on the incident):

```bash
curl -X POST http://localhost:8080/incidents/<incident-id>/actions \
  -H 'Content-Type: application/json' \
  -d '{"runbook_id":"restart-workload"}'
```

The reply is `201` with `"decision": "require_approval"` and
a `"pending"` block: copy its `"id"` as `<pending-id>`. It
touches 2 replicas and expires in 15 minutes. See what is
waiting, then approve it under your own name:

```bash
curl http://localhost:8080/incidents/<incident-id>/approvals
curl -X POST http://localhost:8080/incidents/<incident-id>/approvals/<pending-id>/approve \
  -H 'Content-Type: application/json' \
  -d '{"by":"your name"}'
```

(To turn it down instead, send the same name to the same
address ending in `/reject`; nothing then runs.) A successful
approval answers with the summary "Restarted checkout:
restarts now 1, failing state cleared." and
`"incident_status": "mitigating"`. Run
`.venv/bin/sre-agent show <incident-id>` again: the status is
`mitigating` and the evidence now holds a ninth record, the
action itself. The status is not `resolved` — running a fix
is not proof it worked, so the server does not claim that.

**5. Close it, and stop.** In a real setup the resolved
alert arrives from the monitoring tool; send it by hand:

```bash
curl -X POST http://localhost:8080/webhooks/generic \
  -H 'Content-Type: application/json' \
  -d '{"alert_name":"HighErrorRate","service":"checkout","namespace":"shop","environment":"demo","status":"resolved","summary":"Checkout errors are above 10%"}'
```

The reply says `"resolved": true`, and `show` now reads
`Status: resolved`. Stop the server with Ctrl-C in its
terminal. One honest limit: the shipped server keeps its
incidents in memory, so they are gone after that restart —
a server built in code can keep them in a database file
instead (see [docs/operator-guide.md](docs/operator-guide.md)).

The Docker Compose demo in [docs/demo.md](docs/demo.md) is a
separate path: it runs the pretend service, Prometheus and
Alertmanager for real, and its alert reaches this same
server — but the diagnosis there still comes from the seeded
demo data, not from the demo's live Prometheus.

After the walkthrough, try the command-line tool — with the
manual steps above, use the plain names; after
`./quickstart.sh`, put `.venv/bin/` in front of each one:

```bash
sre-agent diagnose                       # demo diagnosis, no server needed
uvicorn sre_agent.server:app --port 8080 # in another terminal: the server
sre-agent incidents                      # list what the server knows
sre-agent show <incident-id>             # one incident, with its diagnosis
sre-agent postmortem-demo                # a demo incident's postmortem draft
sre-agent evaluate                       # five chaos scenarios, scored
sre-agent regression-check               # fresh scores vs the recorded baseline
sre-agent injection-check                # hostile telemetry must change nothing
sre-agent tier3-check                    # Tier 3 drills + kill switch drills (demo)
sre-agent memory-demo                    # past incidents, runbook records, suggestions
sre-agent adapters-demo                  # Kubernetes pods, Datadog metrics, Azure KQL (all read-only)
sre-agent security-check                 # ten probes attack the agent itself; findings printed
```

## Contributing

Help is welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers
setup, the checks every change must pass, and the safety
rules that do not bend; [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
covers how we treat each other here. Bugs and ideas go
through the issue templates, and the changes in each release
are recorded in [CHANGELOG.md](CHANGELOG.md).

## Licence

MIT. The core of this project will always be open source.
