# Changelog

Every notable change to `sre-agent`, newest first. The format
follows the usual "Keep a Changelog" shape, in plain words:
what was added, what changed, what was fixed, and — under
Security — what was hardened, with anything that was improved
but not finished stated as exactly that.

The scores named in each release were measured on the release
day by the check commands in the release notes
([docs/v0.2.0-walkthrough.md](docs/v0.2.0-walkthrough.md) for
the current release). Nothing is listed here that the tests or
the scored suites did not verify.

## [Unreleased]

Nothing yet. Work in progress lives in
[docs/progress.md](docs/progress.md), not here — this file
records releases, not intentions.

## [0.2.0] — 2026-11-12

The live path is wired, the Phase 6 hardening is in, and the
project is ready for strangers: a one-command install, a
walkthrough that was run before it was written, and the
community basics.

### Added

- **The live path (Phase 7).** The reasoning loop now runs
  inside the server: an alert that opens an incident is
  diagnosed at once, and the diagnosis, its causes and its
  evidence are stored on the incident, readable through the
  API and `sre-agent show`. The server also serves the Tier 2
  approval flow for its own incidents over HTTP — propose,
  wait as a pending approval, approve or reject by name —
  under the same rules as the CLI flow (single use, 15-minute
  expiry, policy checked again at approval time). A proposal
  cannot name a target: it acts on the target pinned on its
  incident, and an approved run moves the incident to
  "mitigating", never straight to "resolved".
- **Read adapters as live evidence.** The Kubernetes, Datadog
  and Azure Monitor adapters can be selected as extra evidence
  sources for a diagnosis, one fixed question each after the
  core four, cited as counts and measured values only.
- **Memory advice on live diagnoses.** Past-incident advice is
  attached to a diagnosis after it is decided — advice only:
  it changes no claim, creates no proposal, and approves
  nothing.
- **A database store.** `SQLiteIncidentStore` keeps incidents,
  diagnoses and approvals in one SQLite file across a restart,
  behind the same store interface. A restored approval keeps
  its original expiry; a decided one stays decided.
- **An evaluation gate for the wired whole.**
  `tests/test_evaluation_gate.py` runs all five suites
  together and checks a fully wired server in the same place.
- **One-command install.** `./quickstart.sh` from a fresh
  clone installs the agent with its server parts and proves
  the install — version, one demo diagnosis, and the five
  scored scenarios — stopping at the first failed step.
  Verified from a clean environment on release day.
- **A README walkthrough.** "Walkthrough: one alert, end to
  end": start the shipped server, send an alert, read the
  diagnosis, approve the restart by name, close the incident
  — every expected answer taken from a real run.
- **Community basics.** A contributing guide
  ([CONTRIBUTING.md](CONTRIBUTING.md)), a code of conduct
  ([CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)), bug-report and
  feature-request issue templates, and a pull request
  template that asks for the check results.

### Security

The post-v0.1.0 hardening (Phase 6), against the findings in
[docs/security-review.md](docs/security-review.md):

- **F-01 fixed.** The server takes a shared token
  (`SRE_AGENT_API_TOKEN`); when one is set, every route
  except `/health` requires it, reads as well as webhooks,
  and the CLI sends it too. With no token set the server is
  unauthenticated, exactly as v0.1.0 shipped — local,
  single-operator demo mode, stated wherever it matters.
- **F-02 fixed.** Ingestion is bounded at every layer: alerts
  per payload, labels and annotations, evidence per incident
  and incidents per store are all capped, with clear refusals
  past a cap. No per-sender rate limit was added — the caps
  bound what a flood can make the server hold.
- **F-03 improved, not closed.** Secret redaction now also
  catches JSON web tokens, passwords in connection strings
  and URLs, whole private-key blocks, and GitLab, Slack and
  Stripe token shapes, with a test guarding ordinary text
  against being cleaned by mistake. A secret with no label
  and no known prefix is still not caught, and no pattern
  list can promise it.
- **F-04 fixed.** Audit records are hash-chained: each record
  carries the previous record's hash and its own, and a
  verifier reports an edited, deleted or reordered record by
  position. Deleting the last records, or replacing the whole
  file with a recomputed chain, is still not detectable.
- **F-05 fixed.** Every live backend address, and the CLI's
  server address, is checked when the backend is built:
  https beyond this machine, plain http only to loopback, an
  address carrying credentials refused, and an optional host
  allow-list that can narrow but never widen those rules.
- **F-06 improved, not closed.** Both demo images run as a
  named non-root user, and the agent image installs from
  `requirements.lock` — exact versions, each with a SHA-256
  hash checked at install. The Compose images (Prometheus,
  Alertmanager) are still pinned by tag, not by manifest
  digest, because no verified digest could be produced in
  the build environment.

### Notes

- The regression baseline was re-recorded on purpose at this
  version: 5 of 5 scenarios correct, 100% accuracy, 0 unsafe
  actions, with the v0.1.0 and development scores kept in its
  history.
- Creating the GitHub Release and the `v0.2.0` tag on the
  website is a short human step, as it was for v0.1.0: the
  GitHub connection used to build this project has no tool
  for it. [docs/v0.2.0-walkthrough.md](docs/v0.2.0-walkthrough.md)
  is written to serve as the release notes.

## [0.1.0] — 2026-11-09

The whole v1 line, working end to end on demo data. Full
notes: [docs/v0.1.0-walkthrough.md](docs/v0.1.0-walkthrough.md).

### Added

- Alert ingestion (Alertmanager and generic webhooks) with
  duplicates counted and related alerts grouped onto one
  incident.
- Read-only diagnosis: three tools (a PromQL metrics
  question, a log search, a service status check) and a
  reasoning loop whose every claim cites its evidence. Plain
  rules, no language model, no API key needed.
- The safety core: a policy engine classifying every action
  from one fixed catalogue, an append-only audit log written
  before anything runs, an allow-listed runbook library
  (restart, scale, rollback — demo targets only), and a
  Tier 2 approval flow: single-use, 15 minutes, named
  approver, policy re-checked at approval time.
- Bounded autonomy in the demo (Tier 3): a small pre-approved
  grant for restart and scale in one namespace, with
  kill-switch drills.
- Incident memory over past incidents, with per-runbook
  records — advice only.
- Read adapters for Kubernetes, Datadog and Azure Monitor,
  on seeded demo data; no live call claimed as tested.
- A postmortem draft written from the incident record itself.
- The checks: a five-scenario chaos suite (5 of 5, 0 unsafe
  actions), a regression gate, an eight-case prompt-injection
  set (8 of 8), Tier 3 drills (7 of 7), and a published
  security review — ten probes (10 of 10) and eight written
  findings, F-01 to F-08, which the 0.2.0 entry above follows
  up.
- The Docker Compose demo environment, the operator guide
  and the runbook authoring guide.
