# Operator guide

How to run `sre-agent`, watch it work, stop it, and check it is
healthy. Written for the one person operating it today: a single
operator, on their own machine, against the demo environment.

If you only want the demo, start with [demo.md](demo.md). This
guide is for running the agent itself, day to day.

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
- Everything the server knows lives in its memory. Stop the
  server and its incidents are gone. That is the current design,
  not a bug to work around — there is no database yet.

## Install

You need Python 3.11 or newer. From the top folder of the
repository:

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
- The server does not run the diagnosis step yet, so a live
  incident usually shows "Diagnosis: none yet". To see a
  diagnosis today, run the seeded demo one, which needs no
  server at all: `sre-agent diagnose`.

## The commands that need no server

Each of these runs locally, on seeded demo data, and changes
nothing outside its own process:

| Command | What it shows |
|---|---|
| `sre-agent diagnose` | One demo diagnosis, every claim citing its evidence |
| `sre-agent postmortem-demo` | A demo incident run end to end, then its postmortem draft |
| `sre-agent evaluate` | The five chaos scenarios, scored (exits 1 if any score is wrong or any unsafe action is counted) |
| `sre-agent regression-check` | Fresh scores against the recorded baseline in `evaluation/baseline.json` (exits 1 if any gate rule breaks) |
| `sre-agent injection-check` | Hostile text planted in telemetry must change nothing (exits 1 if any case fails) |
| `sre-agent tier3-check` | The seven Tier 3 and kill switch drills (exits 1 if any drill fails) |
| `sre-agent memory-demo` | Past incidents, each runbook's record, and suggested runbooks (advice only) |
| `sre-agent adapters-demo` | The Kubernetes, Datadog and Azure Monitor read adapters on demo data |
| `sre-agent security-check` | The ten security probes, plus the findings they cannot prove away (exits 1 if any probe fails) |

Run the four checks — `evaluate`, `regression-check`,
`injection-check`, `tier3-check`, plus `security-check` — after
any change, before you trust the build again. They are quick,
and together they are the measured answer to "is it still
safe?".

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
- Approving a live server incident is not wired yet — this
  command approves demo fixes in its own process only.

## Stop the agent

- **Stop the server:** Ctrl-C in its terminal, or stop the
  process. Everything stops; the in-memory incidents are lost.
- **The halt switch, stated honestly:** the safety model speaks
  of a halt that blocks mutations while reads continue. In code
  today that halt is a setting on an environment's policy
  (`halted` in `policy.py`), checked at the moment each fix
  would run, and it is exercised by the Tier 3 drills
  (`sre-agent tier3-check`) and the demo policies. There is **no
  `sre-agent halt` command in this build yet** — for the running
  server, stopping the process is the halt, and since the server
  never runs fixes, stopping it loses nothing that could act.
  A live halt command belongs with the work that wires fixes to
  the server, and until then this guide will keep saying so.

## The audit log

Every policy decision — allowed, needs approval, or refused —
is recorded before anything runs. The record names the runbook,
the target, the decision, the reason, and a SHA-256 fingerprint
of the action's parameters. It never holds the parameter values
themselves, so a secret passed as a parameter cannot leak into
the log.

In the demo commands the log lives in the process and its size
is printed at the end of the run (for example, `approve-demo`
reports how many decisions it holds for its incident). In code,
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
  incidents are gone. List what it knows: `sre-agent incidents`.
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
- **An incident shows "Diagnosis: none yet"** — expected for
  live server incidents today; see the limit above.
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
where a piece is not built yet — a live halt command, server-side
diagnosis, live approvals — the guide says so instead of
implying otherwise.
