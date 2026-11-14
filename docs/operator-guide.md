# Operator guide

How to run `sre-agent`, watch it work, stop it, and check it is
healthy. Written for the one person operating it today: a single
operator, on their own machine, against the demo environment.

If you only want the demo, start with [demo.md](demo.md). This
guide is for running the agent itself, day to day. New to the
agent? The README's walkthrough (the "Walkthrough: one alert,
end to end" section of the Quick start) takes one alert
through the whole live path on the shipped server — send an
alert, read a diagnosis, approve an action, close the
incident — and every step in it was run against a real server
before it was written down.

## Before you start — what this build is, and is not

- The agent receives alerts, records incidents, and can diagnose
  them with read-only tools. Fixes run only in the demo, through
  the approval flow or the bounded Tier 3 grant. No real system
  is touched by any command in this guide.
- **Do not expose the webhook server to a network other people
  can reach — unless you have set a token.** With no token set,
  the server has no authentication: that was finding F-01 in
  [security-review.md](security-review.md), accepted in writing
  for a local, single-operator demo only. After v0.1.0 the fix
  exists: set `SRE_AGENT_API_TOKEN` before you start the server
  and every endpoint except `/health` requires that token (see
  "Run the server"). Finding F-02 is fixed too: alert intake is
  now capped — at most 100 alerts in one payload, bounded labels
  and annotations, at most 200 evidence records on one incident
  and 1,000 incidents in the store — and past a cap the server
  refuses with a clear error. One honest remainder: there is
  still no per-sender *rate* limit, so a flood can fill the
  store to its cap; it just cannot grow memory without bound.
- By default, everything the server knows lives in its memory.
  Stop the server and its incidents are gone. A database store
  now exists for servers built in code (see "Keep incidents in
  a database file" below); the shipped server app still uses
  the in-memory store, so its incidents are still gone on a
  restart — that is its current design, not a bug.

## Install

You need Python 3.11 or newer. The one-command way, from the
top folder of a fresh clone (macOS and Linux):

```bash
./quickstart.sh
```

The script makes the private Python folder (`.venv`), installs
`sre-agent` with its server parts, and proves the install: it
prints the version, runs one demo diagnosis, and runs the
eighteen scored chaos scenarios (18 of 18 must pass). If any step fails it
stops there. It was verified from a clean environment — a fresh
copy with no `.venv` and no installed package — on
12 November 2026. After it, the commands below live in
`.venv/bin/` (for example `.venv/bin/sre-agent incidents`).

By hand, the same install is:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server]"
```

The `dev` part brings the test tools; the `server` part brings
the web server. Check the install worked:

```bash
sre-agent --version
pytest
```

All tests should pass, including the safety tests. If they do
not, do not run the agent — a red safety suite means a guardrail
is broken, and the guardrails are the product.

## Run the server

```bash
uvicorn sre_agent.server:app --port 8080
```

Leave that terminal open; the server runs in it. Check it is up,
from another terminal:

```bash
curl http://localhost:8080/health
```

It answers `{"status":"ok"}`. The endpoints are:

- `POST /webhooks/alertmanager` — the shape Alertmanager sends.
- `POST /webhooks/generic` — one simple alert as JSON, for tools
  that do not speak Alertmanager. The fields are `alert_name`
  and `service` (both required), plus `namespace` (default
  `default`), `environment` (default `demo`), `severity`,
  `status` (`firing` or `resolved`), `summary`, `labels` and
  `annotations`.
- `GET /incidents` — the incidents the server knows, one summary
  each.
- `GET /incidents/{id}` — one incident in full.
- `POST /incidents/{id}/actions` — propose a runbook fix for a
  diagnosed incident (see "Approve a fix" below).
- `GET /incidents/{id}/approvals` — that incident's approvals,
  with their state (pending, approved, rejected, expired).
- `POST /incidents/{id}/approvals/{pending-id}/approve` and
  `.../reject` — a named human's decision on one pending fix.

### Protect the server with a token

By default the server runs token-less, for one operator on their
own machine. Before anyone else can reach it, set a token:

```bash
SRE_AGENT_API_TOKEN='choose-a-long-random-value' \
  uvicorn sre_agent.server:app --port 8080
```

From then on, every endpoint except `GET /health` needs that
exact value in the `X-SRE-Agent-Token` header; a missing or
wrong token gets a `401` and nothing is ingested or shown. By
hand, that is:

```bash
curl -H 'X-SRE-Agent-Token: choose-a-long-random-value' \
  http://localhost:8080/incidents
```

The CLI sends the header for you when the same variable is set
in its environment (see the next section). Alertmanager can send
it too, from its webhook receiver's `http_config`. Keep the
token out of the repository and out of logs — it is a shared
secret, not a configuration detail.

Send a test alert by hand:

```bash
curl -X POST http://localhost:8080/webhooks/generic \
  -H 'Content-Type: application/json' \
  -d '{"alert_name":"HighErrorRate","service":"checkout","namespace":"shop","environment":"demo","summary":"Checkout errors are above 10%"}'
```

The same alert sent again is counted as a duplicate on the same
incident, not a new incident. An alert with `"status":"resolved"`
for the same service, namespace, environment and alert name
closes the incident.

## Keep incidents in a database file (optional)

A server built in code can keep its incidents in one SQLite
database file instead of in memory, using
`SQLiteIncidentStore` from `sre_agent.storage`:

```python
from sre_agent.storage import SQLiteIncidentStore
from sre_agent.server import create_app

store = SQLiteIncidentStore("sre-agent.db")
app = create_app(store=store, tool_layer=..., approval_policy=...,
                 runbook_backend=...)
```

What survives a restart, in plain words: the incidents with
everything on them — status, evidence, possible causes, the
diagnosis, and any memory advice — plus the counts of duplicate
alerts and the rules for recognising them, so a repeated alert
is still a duplicate afterwards and a resolved alert still
closes its incident. Pending approvals survive too: a proposal
waiting for a human is still waiting afterwards, with the same
expiry time it was given (a restart does not extend its
15 minutes), and an approval that was already decided stays
decided, so it cannot be run twice across a restart.

Two honest limits: the store is one file for one operator —
it is not a shared database for several servers at once — and
the shipped server app still uses the in-memory store, so
this only applies to a server you build in code, as above.
The audit log is separate: it lives in the server process
unless code hands it a file (see "The audit log").

## Watch incidents from the terminal

The command-line tool reads the running server:

```bash
sre-agent incidents            # one plain line per incident
sre-agent show <incident-id>   # one incident in full
```

The server address defaults to `http://127.0.0.1:8080`. Use
`--base-url URL` on either command, or set the
`SRE_AGENT_BASE_URL` environment variable, if you ran the
server somewhere else. If the server was started with a token,
set `SRE_AGENT_API_TOKEN` to the same value for the CLI and it
sends the token header on every read; without it, the server
answers `401` and the CLI reports a plain server error.

The address follows the backend address rules (finding F-05,
fixed after v0.1.0, in `backend_url.py`): it must be an
`https://` address, or a plain `http://` address only when the
server is on this same machine (`localhost`, `127.0.0.1`,
`::1`). A plain-http address for a server on another machine
is refused with a plain reason, before anything is sent — the
CLI can carry your token, and a token must not cross a network
unencrypted. The same rules guard every live backend address
(Prometheus, Kubernetes, Datadog, Azure Monitor) in code, and
a backend may also be given an allow-list of hosts it may
call; the list can only narrow where it may call, never widen
the https rule.

Two honest limits, by design:

- If the server is not running, the tool says so plainly, with
  the command to start it. It never prints an empty list that
  would look like "no incidents".
- Diagnosis depends on how the server was built. The shipped
  server app runs the diagnosis step itself, from the seeded
  demo tool data: a new alert is diagnosed as it arrives, and
  `sre-agent show` prints that diagnosis with its causes and
  evidence. The shipped app also selects the seeded demo read
  adapters (Kubernetes, Datadog, Azure Monitor) as extra
  evidence sources, so its diagnoses carry their answers too;
  a server built in code can pass its own adapter bundle
  (`create_app(adapters=...)`) or none at all. The shipped
  app also carries the seeded demo memory, so a diagnosis it
  makes has that memory's advice attached — similar past
  incidents and suggested runbooks, printed by
  `sre-agent show` under "Memory advice (advice only...)".
  That advice is worked out after the diagnosis is decided:
  it changes no claim or cause, it creates no proposal, and
  a suggested fix still needs the normal proposal and
  approval below. A server built in code can pass its own
  memory (`create_app(memory=...)`) or none at all. A server built
  with no tool layer records incidents only, so its incidents
  show "Diagnosis: none yet". Either way, the seeded demo
  diagnosis needs no server at all: `sre-agent diagnose`.

## The commands that need no server

Each of these runs locally, on seeded demo data, and changes
nothing outside its own process:

| Command | What it shows |
|---|---|
| `sre-agent diagnose` | One demo diagnosis, every claim citing its evidence |
| `sre-agent postmortem-demo` | A demo incident run end to end, then its postmortem draft |
| `sre-agent evaluate` | The eighteen chaos scenarios, scored (exits 1 if any score is wrong or any unsafe action is counted) |
| `sre-agent regression-check` | Fresh scores against the recorded baseline in `evaluation/baseline.json` (exits 1 if any gate rule breaks) |
| `sre-agent injection-check` | Hostile text planted in telemetry must change nothing (exits 1 if any case fails) |
| `sre-agent tier3-check` | The seven Tier 3 and kill switch drills (exits 1 if any drill fails) |
| `sre-agent memory-demo` | Past incidents, each runbook's record, and suggested runbooks (advice only) |
| `sre-agent adapters-demo` | The Kubernetes, Datadog and Azure Monitor read adapters on demo data |
| `sre-agent security-check` | The ten security probes, plus the findings they cannot prove away (exits 1 if any probe fails) |

Grouped names (Phase 12): the demo commands also answer to
`sre-agent demo diagnose`, `sre-agent demo postmortem`,
`sre-agent demo memory` and `sre-agent demo adapters`, and the
checks to `sre-agent check evaluate`, `sre-agent check
regression`, `sre-agent check injection`, `sre-agent check
tier3` and `sre-agent check security`. The names in the table
still work, kept as aliases for one release; both forms run
the same code.

Run the four checks — `evaluate`, `regression-check`,
`injection-check`, `tier3-check`, plus `security-check` — after
any change, before you trust the build again. They are quick,
and together they are the measured answer to "is it still
safe?".

Phase 7 closed with an evaluation gate: all five suites were
re-run with the live path wired, and they passed unchanged —
scenarios 5 of 5, the regression gate against the v0.1.0
baseline, hostile-text cases 8 of 8, Tier 3 drills 7 of 7, and
security probes 10 of 10, with 0 unsafe actions. A test file
(`tests/test_evaluation_gate.py`) pins that gate: it runs the
five suites together and checks a fully wired server — tool
layer, read adapters, memory, live approvals and a database
store all switched on at once — diagnosing with evidence,
running nothing on its own, surviving a restart, and letting
a named human approve a fix exactly once. If that file fails,
the live path has broken a promise the separate suites made.

## Diagnosis backend: plain rules by default, a model only if you wire one

Every diagnosis is read by a *backend* (`model_backend.py`),
and the report names which one ran. The default — in the demo,
the CLI, the checks above and the shipped server — is the
deterministic backend: plain rules over the gathered evidence,
no model call and no API key, so every diagnosis can be
checked exactly.

A language-model backend also exists, and it is
off by default. There is no setting, environment variable
or CLI flag for it: the only way to turn it on is to build one in
your own code, around your own model client, and hand it in
on purpose:

```python
from sre_agent.model_backend import LanguageModelBackend
from sre_agent.reasoning import ReasoningLoop

loop = ReasoningLoop(
    tool_layer, backend=LanguageModelBackend(your_client)
)
# or, on a server: create_app(tool_layer=..., diagnosis_backend=...)
```

Your client is any object with one method,
`complete(prompt) -> str` — text in, text out. This
repository ships no real model client: which provider, model
and key to use is your decision, and your key never passes
through this project's code or configuration.

The model's reply is checked as untrusted input before any
of it can become a claim. It must be one JSON object with
only two keys, `claims` and `hypotheses`; each entry may
carry only a statement, the evidence ids behind it, and a
confidence between 0 and 1, and every id it cites must be a
real evidence record gathered for that incident. An entry
carrying any other field — a runbook name, say — is dropped;
a reply with a top-level field beyond the two keys is
refused whole; and a client that fails, or a reply that is
not JSON, leaves the backend with nothing to say, so the
incident escalates to a human exactly as it does when the
evidence is thin. The backend proposes wording inside a
diagnosis only: it proposes no action, runs nothing, and
approves nothing.

A model may also *propose a fix*, through a separate channel
(`model_proposals.py`) that an operator builds and calls in
their own code the same way — there is no setting, flag or
shipped wiring for it either, and a diagnosis reply still may
not carry an action. A model proposal is treated exactly like
any other proposal: it is built by the same `build_action`
(parameters validated, replicas touched counted from the
workload's real state, target always the one pinned on the
incident — a proposal reply carrying a target, an approver or
a decision field is refused whole), decided by the same
policy engine, written to the same audit log before anything
can run, and held as the same pending action when it needs a
human. The model can propose, never approve: the proposer has
no approve step, a submission can produce a pending action
but never a grant, and a model-proposed fix runs before a
human's yes only when the policy itself allows it outright —
a Tier 3 grant a human wrote into the policy in advance.

Scores, stated plainly: each backend's scores are now
published separately in
[docs/evaluation.md](evaluation.md) ("Scores by backend").
The deterministic backend passes all the gates — chaos
18 of 18, the regression gate against the recorded baseline,
the hostile-text set 8 of 8, 0 unsafe actions — and it is
the recommended backend, and the default. The
language-model backend has been scored too, but only with a
scripted test-double client from the test suite: on the
expanded eighteen-scenario suite (Phase 12) that double
scores 3 of 18 and does not pass the gates, because its
rules know only three causes — it proves the scoring works
and shows its own limits; it is not a real model's score. No
real model has been scored, so no real model backend is
recommended: a language-model backend is not recommended
until the exact client you wire has been scored and its
scores published separately. Score your own client in code
with `score_backend` (`sre_agent.backend_scores`): hand it
your backend and the recorded baseline, and it runs all
three suites with your backend enabled and reports its
scores on their own — scoring never moves the baseline.

## Approve a fix (Tier 2, demo only)

```bash
sre-agent approve-demo --by <your name>
```

This proposes a restart of the demo checkout workload, prints
what would run and how many copies it touches, and runs it only
if you type `approve` at the prompt. `--yes` approves without
the prompt; use it only when you have read the proposal first.
Other runbooks: add `--runbook scale-replicas --replicas N` or
`--runbook rollback-deployment`.

What to know before you approve:

- An approval must name who is approving (`--by`). An unnamed
  approval is refused.
- The approval covers that one action only — that runbook, that
  target, those parameters, that incident. It expires after
  15 minutes and can be used once, even if the run then fails.
- The policy engine decides again at approval time. If the
  environment was halted or tightened after the proposal, the
  refusal stands and is written in the audit log. A human's yes
  does not override the limits.
- This command approves demo fixes in its own process. Live
  server incidents are approved on the server itself — the next
  section — under exactly the same rules.

## Approve a fix on the server (Tier 2, live path)

The shipped server holds the same approval flow for its own
incidents, acting on the seeded demo workloads. A fix is
proposed for one diagnosed incident, decided by the policy
engine, and run only after a named human approves that exact
pending action:

```bash
# 1. Propose a restart for a diagnosed incident.
curl -X POST http://localhost:8080/incidents/<incident-id>/actions \
  -H 'Content-Type: application/json' \
  -d '{"runbook_id":"restart-workload"}'
#    → 201, with the pending approval's id, its target, how many
#      replicas it touches, and when it expires (15 minutes).

# 2. See what is waiting on that incident.
curl http://localhost:8080/incidents/<incident-id>/approvals

# 3. Approve it, by name — or reject it the same way, at
#    .../reject. The approved fix then runs, once.
curl -X POST \
  http://localhost:8080/incidents/<incident-id>/approvals/<pending-id>/approve \
  -H 'Content-Type: application/json' \
  -d '{"by":"<your name>"}'
```

What to know before you approve:

- The proposal cannot name a target. It always acts on the
  target pinned on the incident, and how many replicas it
  touches is counted from the workload's real state.
- Only a diagnosed incident can have a fix proposed. A server
  built with no policy or no runbook backend holds no
  approvals at all, and these routes answer `409` saying so.
- The rules are the demo rules above, unchanged: named
  approver, 15-minute expiry, one use only (a failed run
  spends the approval too), and the policy engine decides
  again at approval time — a halt set after the proposal
  makes the approval fail, and that refusal is audited.
- A successful run is written on the incident as evidence and
  moves it to "mitigating". It does **not** mark the incident
  resolved: the server has not checked the fix worked, so
  resolution still comes from a resolved alert.
- If the server was started with a token, every one of these
  routes needs the token header, exactly like the webhooks.

## Stop the agent

- **Stop the server:** Ctrl-C in its terminal, or stop the
  process. Everything stops; with the default in-memory store
  the incidents are lost, and with a database store (above)
  they are in its file, ready for the next start.
- **The halt switch, stated honestly:** the safety model speaks
  of a halt that blocks mutations while reads continue. In code
  today that halt is a setting on an environment's policy
  (`halted` in `policy.py`), checked at the moment each fix
  would run, and it is exercised by the Tier 3 drills
  (`sre-agent tier3-check`) and the demo policies. There is **no
  `sre-agent halt` command in this build yet** — for the running
  server, stopping the process is the halt: it stops everything,
  including any fix the server might have run. The policy's
  `halted` setting is the finer switch — on a server built with
  a policy, it is re-checked when an approval is given, so a
  halt set after a proposal refuses that approval — but there
  is no live command that flips it on a running server yet.

## The audit log

Every policy decision — allowed, needs approval, or refused —
is recorded before anything runs. The record names the runbook,
the target, the decision, the reason, and a SHA-256 fingerprint
of the action's parameters. It never holds the parameter values
themselves, so a secret passed as a parameter cannot leak into
the log.

In the demo commands the log lives in the process and its size
is printed at the end of the run (for example, `approve-demo`
reports how many decisions it holds for its incident). On the
server, the proposal and approval decisions for its incidents
live in the server process's own audit log, held the same way.
In code,
`AuditLog` in `audit.py` can also write one JSON record per
line to a file; read it back with any JSON-lines tool.

The records are also chained together (finding F-04, fixed
after v0.1.0): each record carries the hash of the record
before it and its own hash, so an edit, a deletion from the
middle, or a reordering breaks the chain. Check a file with
`verify_audit_file` in `audit.py` — it returns a list of
problems, empty when the chain is intact. Two limits, stated
plainly: deleting the last records, or replacing the whole
file with a freshly recomputed chain, is not detected — that
would need a signature or a copy kept elsewhere — and the
chain shows an edit happened; it does not stop one.

## Settings

Settings live in `config.py` and are deliberately small: a data
directory, a log level (`DEBUG`, `INFO`, `WARNING` or `ERROR`),
per-incident budgets (at most 25 steps, 600 seconds and 100,000
model tokens by default — on exhaustion the agent escalates to
a human instead of continuing), and the model backend, which is
`mock` today: the reasoning step uses plain rules and no
language model, so no API key is needed and every diagnosis can
be checked exactly. Logs are one JSON object per line — time,
level, which part wrote it, and the message.

## Troubleshooting

- **"could not reach the agent server"** — the server is not
  running, or not at that address. Start it (above), or point
  the CLI at it with `--base-url` / `SRE_AGENT_BASE_URL`.
- **"server address uses plain http to a host that is not on
  this machine"** — the address rules (F-05) refused the
  `--base-url` before anything was sent. Use the server's
  `https://` address, or run the CLI on the same machine as a
  plain-http local server.
- **"the server says that incident does not exist"** — the id
  is wrong, or the server was restarted and its in-memory
  incidents are gone (a database store keeps them; see above).
  List what it knows: `sre-agent incidents`.
- **HTTP 401 from the server, or a plain server error from the
  CLI** — the server was started with a token and the request
  did not carry it (or carried a different one). Set
  `SRE_AGENT_API_TOKEN` to the server's token for the CLI, or
  send the `X-SRE-Agent-Token` header by hand.
- **HTTP 429 or 422 on a webhook** — an intake cap was hit
  (F-02): too many alerts in one payload, oversize labels or
  annotations, a full evidence record, or a full incident store.
  The error message names which cap; a flood sender should be
  fixed or blocked, not accommodated by raising caps blindly.
- **An incident shows "Diagnosis: none yet"** — the server was
  built with no tool layer, so it records incidents only; the
  shipped server app diagnoses as alerts arrive. An incident
  the loop could not diagnose is handed to a human instead
  ("escalated") and also shows no diagnosis.
- **A fix was refused that you expected to run** — read the
  reason; every refusal states it. The usual causes: the
  runbook is not on the allow-list, the action touches more
  copies than the environment allows, the target is not the one
  pinned on the incident, or the environment is halted. The full
  contract is [safety-model.md](safety-model.md).
- **Tests fail after a change** — do not run the agent. If the
  failing test is in the safety, policy, approval, injection or
  security sets, the change weakened a guardrail until proven
  otherwise.

## The short version

Install it, keep the tests green, run the server on your own
machine only (or set a token before anyone else can reach it),
watch incidents with the CLI, approve demo fixes
by name and one at a time, and run the check commands after any
change. Everything this guide claims is in the code today;
where a piece is not built yet — a live halt command — the
guide says so instead of implying otherwise.
