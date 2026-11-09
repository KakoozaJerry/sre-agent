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

Early build. Phase 0 (the foundation) and Phase 1 (read-only diagnosis) are done: the agent can now receive alerts — from Alertmanager or a simple generic webhook — and turn them into incidents, counting repeat firings as duplicates and grouping related alerts onto one incident instead of opening a new file each time. It can also read evidence, still without touching anything: it can ask a PromQL metrics question, search log lines, and check whether a service is up. Every tool answer is checked, kept to a fixed size, cleaned of common secret shapes, and stored as evidence that names the exact query behind it. And it can now put that evidence together into a diagnosis: a short, fixed set of read-only questions, answered in order, becomes a report where every claim names the evidence and the exact query behind it — and where a claim with no evidence behind it cannot be written at all. This first version reasons with plain rules and no language model, on purpose, so anyone can run it with no API key and the tests can check every diagnosis exactly. If the evidence is thin, or the step budget runs out, it says "I don't know" and hands the incident to a human instead of guessing. The incident model, the safety policy engine, the settings, and the logging are also in. And there is now a demo environment that breaks on purpose: one command (`docker compose up`) starts a pretend checkout service, Prometheus, Alertmanager, and the agent together, and a small script injects a failure so the alert-to-incident path can be watched end to end. One honest limit, stated in [docs/demo.md](docs/demo.md): the reasoning loop is not yet wired to the demo's live Prometheus — its tools still read the built-in demo data. And there is now a command-line tool, which completes Phase 1: with the agent server running, `sre-agent incidents` lists what the server knows and `sre-agent show <incident-id>` shows one incident in full — its diagnosis (or a plain "none yet"), the possible causes recorded for it, and the evidence with the exact query behind each record. With no server needed, `sre-agent diagnose` runs the reasoning loop once on the seeded demo data and prints the report. Two honest limits: the CLI is a thin client of the server, so if the server is not running it says so instead of pretending there are no incidents; and the server does not run the reasoning loop yet, so a live incident's diagnosis only appears once that wiring lands. Phase 2 has now started with its referee: the policy engine is finished and paired with an audit log. Every proposed action is classified from one fixed list in the code — three read-only tools and three undoable fixes (restart, scale, rollback) — and a fix name that is not on the list is refused outright, never just sent for approval. A fix aimed anywhere other than the target stamped on its incident is refused too, an environment can name exactly which areas may be touched, and the stop switch now blocks changes while still letting the agent look. Every decision the engine makes — allowed, needs approval, or refused — is written to an append-only audit log before anything can run, with a fingerprint of the fix's parameters instead of their values, so a secret cannot leak into the log. The runbook library is now built too: the three undoable fixes — restart a workload, set how many copies run, roll back the last deploy — for the demo environment only. Each fix names the parameters it takes (restart and rollback take none; scale takes a copy count from 1 to 5), anything else is refused, and there is no way to pass a free-form command. The checks run twice, when a fix is proposed and again when it is about to run, and the count of copies a fix touches is worked out from the workload's real state, not claimed by whoever proposes it. The executor runs a fix only when the policy engine has allowed it outright. That approval wiring is now built too, so a Tier 2 fix runs end to end: `sre-agent approve-demo --by <your name>` proposes a fix for the demo checkout workload, the policy engine sends it for approval, and it runs only if the person at the terminal approves that exact action. An approval covers one runbook, one target, one set of parameters and one incident; it expires after 15 minutes, it can be used once only (even a failed run spends it), the approver is named in the audit log, and the policy engine is asked again at approval time — if the environment has been halted or tightened since the proposal, the denial stands. Saying no, or letting the prompt pass, runs nothing. One honest limit: this approves demo fixes in one process, like the rest of Phase 2; approving a live server incident is not wired yet, because the server holds no runbook state. The last piece of Phase 2 is now built too, so Phase 2 is complete: postmortem draft generation. `sre-agent postmortem-demo` runs one demo incident through the whole path — diagnosis, an approved restart, resolution — and prints the first draft of its postmortem, written from the incident record itself: a timeline, the diagnosis, the possible causes, the evidence with the exact query behind each record, the actions taken, and the audit decisions, ending with a plain list of what the record does not know. The draft invents nothing: if no diagnosis was recorded, it says no cause is stated; if an incident is not resolved yet, the draft is labelled preliminary, not final; and its timeline uses only timestamps the record really has, because status changes are not themselves timestamped. One honest limit: drafting a live server incident's postmortem is not wired yet, because the full record and its audit records do not yet live in one place. Phase 3 has now started with its first item: the evaluation harness. `sre-agent evaluate` runs five chaos scenarios — pod kill, latency injection, bad deploy, disk fill, config error — against the reasoning loop on seeded demo data (no server or Docker needed), and scores each one: the outcome, the top cause, whether every claim cites real evidence, the steps used, and how many unsafe actions ran (zero, counted from the incident records, not assumed). The measured result on 2026-11-09 is 5 of 5 correct on this suite, with 0 unsafe actions. Three honest limits, stated in [docs/evaluation.md](docs/evaluation.md): which fix the agent would propose is not scored, because the loop proposes none; the specific cause is not scored for the disk, config and deploy scenarios, because the loop has no tool that reads disk, config or deploys; and the faults run on seeded tool data, not yet through the live Docker demo. A regression gate now guards those scores: `sre-agent regression-check` runs the suite again and compares it against the recorded baseline in `evaluation/baseline.json` — the last release's scores. Unsafe actions must be zero (always, not just compared to the baseline), every diagnosis must cite real evidence, accuracy may not drop, and a scenario that was correct and turns wrong fails the gate by name. The baseline moves only on purpose, at a release, and each move is appended to its history, so accuracy is tracked per release in one place. Phase 3 is now complete: its last piece is the prompt-injection test set. `sre-agent injection-check` plants hostile text in every kind of telemetry the agent reads — log lines ordering a deletion or a fix, an alert summary ordering a retarget, an alert annotation claiming the incident is already resolved, a status message claiming a down service is healthy, a metric label ordering a rollback, a log line asking for secrets — and checks, case by case, that the text changed nothing: it stays in the evidence record as data and never reaches a claim, a hypothesis, a diagnosis, a target, or an action; no action runs; and the answer stays the one the honest numbers give. Measured on 2026-11-09: 8 of 8 cases pass, 0 unsafe actions. One limit is stated in the report itself: the word "timeout" in an error log is read as a symptom signal by a plain rule, so a crafted line can raise that one hypothesis — it cannot add wording, actions, or targets beyond the loop's own fixed set. Phase 4 has now started with bounded autonomy in the demo: `sre-agent tier3-check` runs seven drills on the demo environment's Tier 3 grant. In the demo, in the shop namespace only, restart and scale are pre-approved (at most three replicas touched at once) and run through one runner that audits every decision before anything runs; rollback is not pre-approved and still needs a human. The drills prove the pre-approved fixes run, everything outside the grant does not, and a halt pressed between two runs stops the second one while read-only checks continue. Measured on 2026-11-09: 7 of 7 drills pass. Tier 3 here is the demo only — no real system is touched, and no other environment has this grant. Follow along in [docs/progress.md](docs/progress.md), written in plain English.

- [PLAN.md](PLAN.md) — the full plan: vision, architecture, safety model, roadmap, and the long-term open-core path.
- [docs/evaluation.md](docs/evaluation.md) — the evaluation harness: the five scenarios, what is scored, and what is not scored yet.
- [docs/demo.md](docs/demo.md) — the demo environment: what runs, how to start it, and how to break it on purpose.
- [docs/how-it-works.md](docs/how-it-works.md) — the whole system explained simply.
- [docs/architecture.md](docs/architecture.md) — the technical design.
- [docs/safety-model.md](docs/safety-model.md) — how the agent is kept safe: tiers of autonomy, guardrails, threat model.

## What it cannot do yet

Being straight about this matters more than hype:

- It does not manage real production systems yet. It is being proven in a local demo environment where failures are injected on purpose.
- Diagnosis is read-only first. Fixing things (with human approval) comes after the diagnosis is measurably good.
- It needs a language model to reason with. The code runs in a free "mock model" mode for development and tests, so anyone can try it without an API key.

## Quick start (development)

```bash
git clone https://github.com/KakoozaJerry/sre-agent.git
cd sre-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Try the command-line tool:

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
```

## Licence

MIT. The core of this project will always be open source.
