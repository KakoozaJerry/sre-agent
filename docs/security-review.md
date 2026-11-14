# Security review and red-team report

*Review date: 9 November 2026. Scope: the code in this repository, at the end of Phase 4. Reviewer: the project itself (a self-review — no external tester was involved, and this document says so wherever that matters).*

This is Phase 5's first item: the agent turned on itself. An SRE agent that can restart workloads is itself a piece of powerful software, so before anything else in Phase 5, this review asked a simple question from an attacker's side: **if someone hostile — or just careless — pushes on this agent, what breaks, and what holds?**

The answer comes in two halves, and both halves are published:

1. **Probes that passed.** Ten attacks were run against the real code paths, as an automated set that anyone can re-run: `sre-agent security-check`. All ten were refused or contained. The set lives in the `src/sre_agent/security_review/` package (probes in `probes.py`, findings in `findings.py`) and runs in the test suite, so a future change that weakens one of these defences fails a test.
2. **Findings that remain.** Eight weaknesses the probes cannot prove away, each written down below with a severity, what it would take to exploit it, and the fix. The largest one is not subtle: **the webhook server has no authentication at all.** It is accepted, in writing, for a demo that runs on one person's own machine — and it must be fixed before the server is shared with anyone else. *(After v0.1.0, that fix and the F-02 intake caps landed, F-03's secret cleaning was widened, the audit records were hash-chained (F-04), backend addresses were put under https/allow-list rules (F-05), and the demo images and dependency installs were hardened (F-06, improved — the Compose images are still pinned by tag, not by digest); see "Fixed after v0.1.0" below. The findings themselves are kept as found.)*

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
| `audit-log-holds-no-secrets` | Put a secret value in an action's parameters; edit a written audit record | The audit record holds a SHA-256 fingerprint of the parameters; the secret value appears nowhere in the record; and the records are hash-chained, so the probe's edit breaks the chain and the verifier reports it |
| `planted-secrets-redacted` | Plant a password and a token in a log line and in a service status message | Both are replaced with `[redacted]` in the tool results and in the evidence built from them (but see finding F-03: this cleaning is a pattern list) |
| `approval-single-use-and-rechecked` | Use one human approval twice; and approve a fix after the environment has been halted | The second use of the spent approval is refused — even though the first run succeeded — and the approval into the halt is refused, with that denial audited |
| `tier3-limits-hold` | On the Tier 3 path: roll back a deploy, touch five replicas, act outside the granted namespace | Rollback still requires a human; the oversize fix is denied; the out-of-namespace fix is denied |
| `query-guards-hold` | Send an Azure management command (`.drop`, `.set-or-append`); send PromQL with a semicolon, over the length limit, or unbalanced; build a backend with a plain-http address for a remote host, a host off its allow-list, or credentials inside the address | All refused before anything runs; https and loopback-http backend addresses are accepted |
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

**Status:** accepted for the local demo only at review time; **fixed after v0.1.0** — see "Fixed after v0.1.0" below. With no token set the server is still unauthenticated, so the local-only rule still applies in full to a token-less server.

### F-02 — Medium — Ingestion has no size or rate limits

**Where:** `src/sre_agent/ingestion.py`, `src/sre_agent/server.py`.

**What:** The payload models check the *shape* of an alert, not its *size or count*. One payload may carry any number of alerts; labels and annotations may be any size; a grouped alert adds an evidence record to its incident with no cap; and the incident store holds everything in memory with no bound on the number of incidents. A sender — hostile, or a misconfigured monitoring system in a retry storm — can grow the process until it runs out of memory. The tool layer learned this lesson in Phase 1 (every query and result there is bounded); ingestion has not yet.

**Fix:** cap the alerts per payload, the size of labels, annotations and summaries, and the evidence records per incident; add a store-wide incident cap or expiry; return a clear error past the caps instead of accepting the work.

**Status:** **fixed after v0.1.0** — see "Fixed after v0.1.0" below. One part of the original wording is deliberately not claimed: a per-sender *rate* limit was not added; the caps bound the damage a flood can do, they do not slow a sender down.

### F-03 — Medium — Secret cleaning is a pattern list, not a guarantee

**Where:** `redact_secrets` in `src/sre_agent/tools.py`.

**What:** The filter catches the shapes it lists: `password=` / `token=` / `api_key=` style assignments, bearer tokens, AWS access key ids, GitHub tokens, and private-key headers. It does not catch, among others: JSON web tokens (three long base64 parts separated by dots), connection strings (`Server=...;Password=...` is caught only by the `password` part), unlabelled high-entropy secrets, or a private key's body lines without their header. A secret in one of those shapes, written into a log line by the system under observation, would be stored in an evidence record and shown by the CLI.

**Why it is not worse:** secrets are never *needed* in evidence — this is a leak of someone else's secret into the incident record, not a path to running an action — and the audit log is safe by construction (it stores hashes, probe `audit-log-holds-no-secrets`). The tool documentation has always called this filter "a safety net, not a promise"; this finding is that sentence, made specific.

**Fix:** add the missing shapes (JWTs first), and treat redaction as defence in depth rather than the control: the real control is that evidence is shown only to the operator, which finding F-01 currently undermines for the server's read routes.

**Status:** **improved after v0.1.0** — see "Fixed after v0.1.0" below. The missing shapes named here were added (JWTs first), and the limitation is narrowed, not closed: an unlabelled secret with no known prefix, and a private key's body lines shown without their header, are still not caught, and that remainder is stated wherever the cleaning is described.

### F-04 — Low — The audit log file has no tamper protection

**Where:** `src/sre_agent/audit.py`.

**What:** In the code's own use the log is append-only, and the in-memory form hands out copies so callers cannot rewrite records. But the file form is plain JSON lines: anyone or anything that can edit the file can change or delete history, and nothing would detect it — records are not hash-chained (each record does not include a hash of the one before it) and not signed.

**Fix:** hash-chain the records when the file form is used for anything real, and treat the audit file's permissions as part of the deployment.

**Status:** **fixed after v0.1.0** — see "Fixed after v0.1.0" below. The chain detects an edited, deleted or reordered record; what it cannot detect — deleting the last records, or replacing the whole file with a freshly recomputed chain — is stated in that section, not hidden.

### F-05 — Low — Live backend addresses are caller-supplied, with no allow-list

**Where:** the live backends in `tools.py` (Prometheus) and the `adapters` package (Kubernetes, Datadog, Azure), and the CLI's `--base-url` / `SRE_AGENT_BASE_URL`.

**What:** Each live backend takes a base URL from its caller and sends its credential to that address as a header. Today the caller is always operator code or configuration — never telemetry, never an alert, never a model output — so there is no injection path into the address. The risk is misdirection: a mistyped or maliciously set address would receive a live token, and plain `http://` addresses are accepted, so a token could cross a network unencrypted.

**Fix:** when live backends are wired for real use, require `https://` for any address that will receive a credential, and consider an allow-list of hosts per environment.

**Status:** **fixed after v0.1.0** — see "Fixed after v0.1.0" below. The rules were applied now, at backend construction, rather than waiting for the live wiring: every backend address passes them before any credential can be sent to it, and the CLI's server address follows the same rules.

### F-06 — Low — Demo containers and dependencies are not hardened

**Where:** `Dockerfile`, `docker-compose.yml`, `pyproject.toml`.

**What:** The demo image runs as root (no `USER` line), installs dependencies from version ranges with no lock file and no hashes, and Compose pins Prometheus (`v2.54.1`) and Alertmanager (`v0.27.0`) by tag, not by digest — a re-pointed tag would change what runs. This is a supply-chain and container-hygiene note about the demo, not a hole in the agent's logic.

**Fix:** add a non-root user to the image, pin the two images by digest, and generate a lock file for the release.

**Status:** **improved after v0.1.0** — see "Fixed after v0.1.0" below. Both demo images now run as a named non-root user and the agent image installs from a hashed lock file; the Compose images are still pinned by tag, not by digest, for the reason stated there.

### F-07 — Info — The log-file backend reads a caller-chosen path

**Where:** `FileLogsBackend` in `tools.py`.

**What:** The backend reads whatever file path its caller gives it — that is its job in the demo. Only demo and test code chooses the path today. The boundary to keep, in writing: that path must never come from telemetry, an alert, or an API request, or this becomes a file-read hole.

**Status:** accepted, with the boundary stated.

### F-08 — Info — A crafted log line can spoof one symptom signal

**Where:** the reasoning loop in `reasoning.py`. Carried over from the injection set (Phase 3), restated here so this report is complete.

**What:** The loop reads the word "timeout" in error logs as a symptom by plain rule. A planted line containing it can raise the timeout hypothesis, the way any false symptom can mislead a diagnosis. It cannot add wording, actions or targets beyond the loop's fixed set, and no fix is proposed by the loop at all.

**Status:** known limitation.

## Fixed after v0.1.0 — F-01, F-02, F-03, F-04, F-05 and F-06 (improved)

*Follow-up, 9 November 2026, after the v0.1.0 release. This section is an addition to the review above, not a rewrite of it: the findings stand as they were found, and this is what changed.*

- **F-01 — token authentication.** The server now takes a shared token, set with the `SRE_AGENT_API_TOKEN` environment variable (or passed to `create_app`). When a token is set, every endpoint except `GET /health` requires that exact token in the `X-SRE-Agent-Token` header — both webhook routes and both incident read routes — compared in constant time; a missing or wrong token gets a 401 and nothing is ingested or shown. The CLI sends the same header automatically when the variable is set for it, so the operator is not locked out of their own tool. Alertmanager can send the header from its webhook `http_config`. The honest limit: with **no** token set, the server is exactly as unauthenticated as this review found it — that is the local demo mode, and the rule from this report still applies to it in full: do not expose a token-less server to a network other people can reach.
- **F-02 — intake caps.** Ingestion is now bounded at every layer the finding named: at most 100 alerts in one Alertmanager payload; at most 50 labels or annotations on one alert, with key and value lengths capped; short fields and summaries length-capped; at most 200 evidence records on one incident; at most 1,000 incidents in the store. Past a payload cap the request is refused as invalid (422); past a store cap the server answers 429 with the reason, and a duplicate of an existing alert still counts, because it grows nothing. What was **not** added, stated plainly: a per-sender rate limit. A flood can now fill the store to its cap and then receive refusals — it cannot grow the server's memory without bound, which is the harm F-02 named.

- **F-03 — more secret shapes (improved, not closed).** `redact_secrets` now also catches: JSON web tokens (three base64url parts joined by dots, the first starting `eyJ` — requiring that start keeps ordinary dotted words and version numbers out of the rule); passwords inside connection strings (the labelled value now stops at a semicolon, so `Server=db;Password=…;Database=app` is cleaned segment by segment, not swallowed whole and not missed); passwords inside URLs (`scheme://user:password@host`, with the scheme, user and host kept); whole private-key blocks, not only their header line; basic credentials; and the prefixed token shapes of GitLab, Slack (tokens and webhook URLs) and Stripe, alongside the shapes it already caught. A test also guards the other direction: ordinary text — version numbers, hostnames, a commit hash, a link with no password in it — must come through unchanged, because a cleaner that eats normal log lines would be its own kind of damage. What remains, stated plainly: an unlabelled secret with no known prefix and no label, and a private key's body lines shown without their header, are still not caught. No pattern list can promise those, which is why this finding is recorded as improved, not fixed: redaction stays defence in depth, and evidence must still be shown only to the operator.

- **F-04 — the audit file is tamper-evident (hash-chained records).** Every audit record now carries two more fields: `prev_hash`, the hash of the record before it (the first record in a log carries a genesis value of 64 zeros), and `record_hash`, its own SHA-256 hash, computed over every other field including `prev_hash`. `verify_audit_records` (for a list) and `verify_audit_file` (for the JSON-lines file) walk the chain and report, by position, a record that carries no chain hashes, a record that does not point at the one before it, and a record that does not match its own hash — so an edited field, a record deleted from the middle, and a reordering are each detected, and each is named differently. A log that appends to an existing file continues that file's chain, so a restart does not break it. Two honest limits, stated plainly: the chain does not detect deleting the *last* records (nothing after them is left to point back), and it does not detect replacing the whole file with a freshly recomputed chain — both would need a signature or a second copy kept elsewhere, and neither was added. And a file written before this fix carries unchained records: the verifier reports them as unchained rather than passing them, so an old file stays an old file and a new log should be started rather than mixed.

- **F-05 — backend address rules (https and an optional allow-list).** Every live backend address — Prometheus, Kubernetes, Datadog and Azure Monitor — and the CLI's server address now pass one shared check (`validate_backend_url` in `backend_url.py`) when the backend is built, before any request is made: the scheme must be `https`, or plain `http` only when the host is on this same machine (`localhost`, `127.0.0.0/8`, `::1`) — which is where the demo backends and the local server live; an address carrying a user or password (`https://user:secret@host`) is refused, because credentials belong in the header a backend adds on purpose, never inside an address that gets logged and copied; and a caller may pass `allowed_hosts` (host names, optionally with a port), in which case the address's host — and its port, when an entry names one — must be on the list. The list can only narrow where the agent may call: it can never widen the scheme rule, so listing a remote host does not make plain http to it acceptable. The boundary the finding relied on is unchanged and still stated: addresses come only from operator code or configuration, never from telemetry, an alert or a model output — these rules are the second layer, for the day an operator's address is wrong. Applied to the CLI, they mean `sre-agent incidents --base-url http://server.example.com:8080` is now refused with a plain reason, while the default local address and any `https://` address work as before. Phase 12 note (2026-11-14): one explicit exception was added later, as a separate parameter (`allow_http_hosts`), for a private container network the operator controls whose service speaks only http and takes no credential — the Compose demo's Prometheus and sample service are its only users in this project. It does not change the sentence above for `allowed_hosts`, the default still refuses plain http beyond this machine, and naming one host accepts no other; tests pin all three facts.

- **F-06 — demo images and dependencies hardened (improved, not closed).** Both images this project builds — the agent's `Dockerfile` and the sample service's — now create a named user (`sreagent`, `sample`) and run as that user (`USER`), never as root; the sample service listens on port 8000, above 1024, so a non-root user can still bind it. The agent image no longer installs from version ranges: it installs `requirements.lock` first — exact versions of the runtime and server dependencies and everything they need, each with a SHA-256 hash, installed with `pip install --require-hashes`, so a package whose bytes do not match its locked hash is refused — and then installs the agent package itself with `--no-deps`, because the lock already supplied its dependencies. The lock was generated on 2026-11-09 by resolving the dependencies for CPython 3.12 on Linux x86_64, the demo image's platform (`python:3.12-slim`), and a fresh environment installed from it with hashes enforced and ran the security probes 10 of 10 before this was written down; its hashes are for the wheels resolved on that platform, and another platform needs the file regenerated there. What remains, stated plainly: the Compose file still pins Prometheus (`v2.54.1`) and Alertmanager (`v0.27.0`) by tag, not by manifest digest. A digest is a fingerprint — writing down one that was not verified against the registry would either break the demo or, worse, look like a check that was never made; no Docker or registry tool in the build environment could produce a verified manifest digest for those tags, so the tags stay, named as the remainder, until someone with such a tool records the real digests.

Fourteen new tests pin both F-01/F-02 fixes (token required, refused and accepted; environment-supplied token; the CLI sending the header; every cap refusing), three more pin the F-03 shapes and the ordinary-text guard, nine more pin the F-04 chain (chaining order, an edit, a deletion, a reordering, a legacy unchained record reported rather than passed, an edit on disk, a second log continuing a file's chain, and the hash covering the approver and the reason), twenty pin the F-05 rules (https accepted, loopback http accepted, remote http refused, other schemes and credentials-in-address refused, the allow-list accepting, refusing and pinning a port, the allow-list unable to widen the https rule, and every live backend checked at build time), two more pin the CLI refusing a remote plain-http server address before any fetch and accepting https, three wiring tests and one findings test pin F-06 (both demo images running as a named non-root user, the agent image installing from the lock file with hashes required, every lock entry pinned with a 64-character hash including the four direct dependencies, and F-06's recorded improved status), the `planted-secrets-redacted` probe now also plants a JWT and a connection-string password, the `audit-log-holds-no-secrets` probe now also checks that the chain verifies as written and breaks when a record is edited, the `query-guards-hold` probe now also checks the F-05 address rules (a remote plain-http backend refused, a host off the allow-list refused, credentials inside an address refused, https and loopback http accepted), and the findings list printed by `sre-agent security-check` carries the new statuses, so the fix is stated in the same place the weakness was.

## What this review did not find

Worth stating, because the probes looked for them specifically: no path from telemetry to an action (the loop proposes no fix; every fix passes the policy gate, the approval flow, or the Tier 3 grant); no free-form command surface anywhere in the package; no secret value in the audit log or in cleaned tool output for the shapes the filter covers; no way to spend one approval twice; and no way for alert text — as opposed to an alert's structured status — to close an incident or move its target.

## The bottom line

For what the agent is today — a local demo that diagnoses seeded and demo incidents and acts only on demo workloads — the action surface held under every probe this review ran. The server surface did not: it was never built to be reached by anyone but its operator, and findings F-01 and F-02 were the price of sharing it before that changed. Both were written down here and in the code (`security_review` prints them on every run), and after v0.1.0 they were the first things fixed — see "Fixed after v0.1.0" above. F-03 was improved next, in the same place, with its remainder stated rather than closed, F-04 was fixed after that, with its own limits stated the same way, and F-05 was fixed after that, with the allow-list's limit — it narrows, it never widens — stated the same way. F-06 was improved after that, with its remainder — Compose images pinned by tag, not by verified digest — stated the same way. The remaining findings (F-07 and F-08) are still open as written.
