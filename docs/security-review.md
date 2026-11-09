# Security review and red-team report

*Review date: 9 November 2026. Scope: the code in this repository, at the end of Phase 4. Reviewer: the project itself (a self-review — no external tester was involved, and this document says so wherever that matters).*

This is Phase 5's first item: the agent turned on itself. An SRE agent that can restart workloads is itself a piece of powerful software, so before anything else in Phase 5, this review asked a simple question from an attacker's side: **if someone hostile — or just careless — pushes on this agent, what breaks, and what holds?**

The answer comes in two halves, and both halves are published:

1. **Probes that passed.** Ten attacks were run against the real code paths, as an automated set that anyone can re-run: `sre-agent security-check`. All ten were refused or contained. The set lives in `src/sre_agent/security_review.py` and runs in the test suite, so a future change that weakens one of these defences fails a test.
2. **Findings that remain.** Eight weaknesses the probes cannot prove away, each written down below with a severity, what it would take to exploit it, and the fix. The largest one is not subtle: **the webhook server has no authentication at all.** It is accepted, in writing, for a demo that runs on one person's own machine — and it must be fixed before the server is shared with anyone else.

Nothing in this report is a claim that the agent is safe to run against real systems. Phase 4's own limits still hold: the runbooks act on demo workloads only, and the live read backends have never been called against a real cluster, account or workspace.

## How the review was done

- **Code reading.** Every module in `src/sre_agent/` was read with an attacker's questions in mind: where does outside input enter, where could a secret leak, what can one caller make another part of the system do, and what happens if the same request is sent a thousand times?
- **Probes.** The concrete attacks that reading suggested were written as automated probes (below), in the same style as the evaluation harness, the injection set and the Tier 3 drills: seeded demo data, no server, no Docker, no network.
- **What was not done.** No live system was attacked — there is no live Kubernetes cluster, Datadog account or Azure workspace in the build environment. The Docker demo was not run under attack (Docker is not available where this was built; the full `docker compose up` run remains a human check, as `docs/demo.md` already states). No dependency vulnerability scan or external penetration test was run. The reasoning loop calls no language model, so attacks that only exist against a live model backend — beyond the telemetry injection set in Phase 3 — are out of scope and will need a new review when a model is wired in.

## What held — the ten probes

Measured 2026-11-09: **10 of 10 probes pass**, as part of a test suite of 233 tests, all passing, with the style and type checks clean. Re-run them any time with `sre-agent security-check`.

| Probe | The attack | What happened |
|---|---|---|
| `unknown-runbook-denied` | Propose a runbook that does not exist (`delete-the-database`) | Denied outright by the policy engine — never sent to a human for approval — and the denial is in the audit log |
| `free-form-command-refused` | Smuggle a shell command in as a runbook parameter (`command`, `shell`) | Refused: each runbook takes a fixed list of parameters, and anything else is rejected when the proposal is built and again when it would run |
| `target-stays-pinned` | Aim a fix for one incident at a different service | Denied: an action's target must match the target pinned on its incident |
| `audit-log-holds-no-secrets` | Put a secret value in an action's parameters | The audit record holds a SHA-256 fingerprint of the parameters; the secret value appears nowhere in the record |
| `planted-secrets-redacted` | Plant a password and a token in a log line and in a service status message | Both are replaced with `[redacted]` in the tool results and in the evidence built from them (but see finding F-03: this cleaning is a pattern list) |
| `approval-single-use-and-rechecked` | Use one human approval twice; and approve a fix after the environment has been halted | The second use of the spent approval is refused — even though the first run succeeded — and the approval into the halt is refused, with that denial audited |
| `tier3-limits-hold` | On the Tier 3 path: roll back a deploy, touch five replicas, act outside the granted namespace | Rollback still requires a human; the oversize fix is denied; the out-of-namespace fix is denied |
| `query-guards-hold` | Send an Azure management command (`.drop`, `.set-or-append`); send PromQL with a semicolon, over the length limit, or unbalanced | All refused before anything runs |
| `no-shell-surface` | Look for any way the package itself can run a shell or evaluate code | The package source was scanned for shell and code-execution calls (`subprocess`, `os.system`, `eval(`, `exec(`, and friends): none found in any module. There is no shell anywhere in this codebase to reach |
| `alert-text-cannot-close` | Send a firing alert whose text says the incident is already resolved | The incident opens and stays in triage, on the target named by the alert's structured fields. Only an alert whose status is actually `resolved` closes an incident (the injection set, Phase 3, checks the wider version of this boundary) |

## Findings

Severity is judged for the case that matters: the server, or a live backend, used beyond one operator's own machine. For the demo exactly as it stands today — local, single operator, demo workloads only — the status column says what holds.

### F-01 — High — The webhook server has no authentication

**Where:** `src/sre_agent/server.py` — every endpoint.

**What:** There is no token, signature, or login on any route. Anyone who can reach the server can:

- post alerts to `/webhooks/alertmanager` or `/webhooks/generic`, opening fake incidents at will;
- close a real incident by posting a "resolved" alert that matches its group (same service, namespace, environment and alert name) — no credential needed, because resolving was designed for Alertmanager, which in the demo sends no credential either;
- read every incident in full from `/incidents` and `/incidents/{id}`, including its evidence and diagnosis.

**Why it is still here:** the demo's threat model is one operator on their own machine, and Alertmanager's webhook receiver is commonly protected by network placement rather than by the receiver itself. That reasoning only holds while the server is reachable by nobody else.

**Fix before any shared use:** require a shared secret or signed payloads on both webhook routes, require authentication on the read routes too, and bind the demo server to localhost by default in the docs. Until then, the honest instruction — stated in the README's spirit and now here in writing — is: **do not expose this server to a network other people can reach.**

**Status:** accepted for the local demo only; fix before any shared use.

### F-02 — Medium — Ingestion has no size or rate limits

**Where:** `src/sre_agent/ingestion.py`, `src/sre_agent/server.py`.

**What:** The payload models check the *shape* of an alert, not its *size or count*. One payload may carry any number of alerts; labels and annotations may be any size; a grouped alert adds an evidence record to its incident with no cap; and the incident store holds everything in memory with no bound on the number of incidents. A sender — hostile, or a misconfigured monitoring system in a retry storm — can grow the process until it runs out of memory. The tool layer learned this lesson in Phase 1 (every query and result there is bounded); ingestion has not yet.

**Fix:** cap the alerts per payload, the size of labels, annotations and summaries, and the evidence records per incident; add a store-wide incident cap or expiry; return a clear error past the caps instead of accepting the work.

**Status:** open.

### F-03 — Medium — Secret cleaning is a pattern list, not a guarantee

**Where:** `redact_secrets` in `src/sre_agent/tools.py`.

**What:** The filter catches the shapes it lists: `password=` / `token=` / `api_key=` style assignments, bearer tokens, AWS access key ids, GitHub tokens, and private-key headers. It does not catch, among others: JSON web tokens (three long base64 parts separated by dots), connection strings (`Server=...;Password=...` is caught only by the `password` part), unlabelled high-entropy secrets, or a private key's body lines without their header. A secret in one of those shapes, written into a log line by the system under observation, would be stored in an evidence record and shown by the CLI.

**Why it is not worse:** secrets are never *needed* in evidence — this is a leak of someone else's secret into the incident record, not a path to running an action — and the audit log is safe by construction (it stores hashes, probe `audit-log-holds-no-secrets`). The tool documentation has always called this filter "a safety net, not a promise"; this finding is that sentence, made specific.

**Fix:** add the missing shapes (JWTs first), and treat redaction as defence in depth rather than the control: the real control is that evidence is shown only to the operator, which finding F-01 currently undermines for the server's read routes.

**Status:** known limitation, stated wherever the cleaning is described.

### F-04 — Low — The audit log file has no tamper protection

**Where:** `src/sre_agent/audit.py`.

**What:** In the code's own use the log is append-only, and the in-memory form hands out copies so callers cannot rewrite records. But the file form is plain JSON lines: anyone or anything that can edit the file can change or delete history, and nothing would detect it — records are not hash-chained (each record does not include a hash of the one before it) and not signed.

**Fix:** hash-chain the records when the file form is used for anything real, and treat the audit file's permissions as part of the deployment.

**Status:** open.

### F-05 — Low — Live backend addresses are caller-supplied, with no allow-list

**Where:** the live backends in `tools.py` (Prometheus) and `adapters.py` (Kubernetes, Datadog, Azure), and the CLI's `--base-url` / `SRE_AGENT_BASE_URL`.

**What:** Each live backend takes a base URL from its caller and sends its credential to that address as a header. Today the caller is always operator code or configuration — never telemetry, never an alert, never a model output — so there is no injection path into the address. The risk is misdirection: a mistyped or maliciously set address would receive a live token, and plain `http://` addresses are accepted, so a token could cross a network unencrypted.

**Fix:** when live backends are wired for real use, require `https://` for any address that will receive a credential, and consider an allow-list of hosts per environment.

**Status:** open.

### F-06 — Low — Demo containers and dependencies are not hardened

**Where:** `Dockerfile`, `docker-compose.yml`, `pyproject.toml`.

**What:** The demo image runs as root (no `USER` line), installs dependencies from version ranges with no lock file and no hashes, and Compose pins Prometheus (`v2.54.1`) and Alertmanager (`v0.27.0`) by tag, not by digest — a re-pointed tag would change what runs. This is a supply-chain and container-hygiene note about the demo, not a hole in the agent's logic.

**Fix:** add a non-root user to the image, pin the two images by digest, and generate a lock file for the release.

**Status:** open.

### F-07 — Info — The log-file backend reads a caller-chosen path

**Where:** `FileLogsBackend` in `tools.py`.

**What:** The backend reads whatever file path its caller gives it — that is its job in the demo. Only demo and test code chooses the path today. The boundary to keep, in writing: that path must never come from telemetry, an alert, or an API request, or this becomes a file-read hole.

**Status:** accepted, with the boundary stated.

### F-08 — Info — A crafted log line can spoof one symptom signal

**Where:** the reasoning loop in `reasoning.py`. Carried over from the injection set (Phase 3), restated here so this report is complete.

**What:** The loop reads the word "timeout" in error logs as a symptom by plain rule. A planted line containing it can raise the timeout hypothesis, the way any false symptom can mislead a diagnosis. It cannot add wording, actions or targets beyond the loop's fixed set, and no fix is proposed by the loop at all.

**Status:** known limitation.

## What this review did not find

Worth stating, because the probes looked for them specifically: no path from telemetry to an action (the loop proposes no fix; every fix passes the policy gate, the approval flow, or the Tier 3 grant); no free-form command surface anywhere in the package; no secret value in the audit log or in cleaned tool output for the shapes the filter covers; no way to spend one approval twice; and no way for alert text — as opposed to an alert's structured status — to close an incident or move its target.

## The bottom line

For what the agent is today — a local demo that diagnoses seeded and demo incidents and acts only on demo workloads — the action surface held under every probe this review ran. The server surface did not: it was never built to be reached by anyone but its operator, and findings F-01 and F-02 are the price of sharing it before that changes. Both are written down here, in the code (`security_review.py` prints them on every run), and in the roadmap's remaining Phase 5 work they are the first things to fix if the demo is ever hosted for other people.
