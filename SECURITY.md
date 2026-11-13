# Security policy

This page says how to report a security problem in this
project, which versions are supported, and what is in and out
of scope. It is written in plain words, like the rest of the
project's documents.

## Report a problem privately first

If you find a way to make the agent do something unsafe —
run an action nobody approved, approve its own action, leak
a secret into evidence or the audit log, follow an
instruction planted in logs, alerts or other telemetry, aim
a fix at the wrong target, or get past the server's token
when one is set — please **do not open a public issue with
the working details**.

Contact the maintainer, Abraham Kakooza, privately through
his GitHub profile first, so a fix can land before the
details are public. This project has no separate security
email address, and this page does not invent one.

A useful private report says:

- the version (`sre-agent --version`) and how you installed
  it;
- what you ran or sent, in enough detail to repeat it;
- what happened, and what you expected to happen instead;
- why it matters — what an attacker gains if it works;
- a suggested fix, if you have one.

A confirmed report joins the project's published record in
[docs/security-review.md](docs/security-review.md) once it
is fixed, the same way the project's own findings are
recorded there.

## Supported versions

Only the latest release is supported with security fixes.

| Version | Supported |
|---|---|
| 0.2.0 (latest release) | Yes |
| 0.1.0 and older | No — please upgrade |

Work on the main branch between releases is not a supported
release: it may change at any time, and fixes land there
first before the next release is cut.

## What is in scope

- The code in this repository: the agent, its server, the
  policy and approval flow, the runbook executor, the audit
  log, secret redaction, and the hosted demo mode.
- A bypass of a guardrail the project claims: the policy
  tiers, the single-use and re-checked approvals, the pinned
  target, the blast-radius limits, the ingestion caps, the
  backend address rules, and the audit hash chain.
- An instruction planted in telemetry (a log line, an alert
  field, a metric label) that changes a diagnosis's wording,
  a target, or an action — the boundary the prompt-injection
  set measures.
- The hosted demo breaking its stated rules: acting on
  anything but seeded demo data, running an action outside
  the Tier 3 demo grant, or answering a route other than
  `/health` without the token.

## What is out of scope

- **The hosted demo's visitor token being public.** That is
  by design, not a leak: the full reasons are written in
  [docs/hosted-demo.md](docs/hosted-demo.md), under "Why the
  hosted demo's token is public". In short — the demo is
  read-mostly, it holds no real data, the Tier 3 demo grant
  is the only action that can run, and the caps are on.
- **A local server run with no token set.** With no token
  set the server has no authentication, by design, for one
  operator on their own machine; the operator guide and the
  security review both say it must not be exposed to a
  network other people can reach in that state. A way past
  the token *when one is set* is in scope, above.
- **Problems that only restate a known finding.** The
  security review publishes what does not hold yet — among
  them, that secret redaction is a pattern list with stated
  misses (F-03, improved, not closed), that there is no
  per-sender rate limit (the caps bound a flood; they do
  not slow it down), and that the Compose images are pinned
  by tag, not by digest (F-06, improved, not closed). A
  report that adds a new way past a control, or a new
  impact, is in scope; a restatement is already on record.
- **Other people's services and systems.** GitHub, Render,
  Prometheus, Alertmanager and any system the agent only
  reads from are their own projects' concern. Report a
  problem here only if this repository's code is what makes
  it possible.
- **The demo data itself.** The seeded incidents, workloads
  and past incidents are pretend data on purpose; making
  the demo's pretend checkout service fail is the demo
  working as designed, not a security problem.

## What this policy is not

This policy is a reporting path, not a promise that the
agent is safe to run against real systems. It is not, yet:
the runbooks act on demo workloads only, and the live read
backends have never been called against a real cluster,
account or workspace — the honest limits in the README and
in [docs/security-review.md](docs/security-review.md) stand.
