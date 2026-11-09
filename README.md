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

Early build. Phase 0 (the foundation) is done, and Phase 1 (read-only diagnosis) has started: the agent can now receive alerts — from Alertmanager or a simple generic webhook — and turn them into incidents, counting repeat firings as duplicates and grouping related alerts onto one incident instead of opening a new file each time. The incident model, the safety policy engine, the settings, and the logging are also in. Next in Phase 1: the agent learns to read evidence — metrics, logs, and service status. Follow along in [docs/progress.md](docs/progress.md), written in plain English.

- [PLAN.md](PLAN.md) — the full plan: vision, architecture, safety model, roadmap, and the long-term open-core path.
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

## Licence

MIT. The core of this project will always be open source.
