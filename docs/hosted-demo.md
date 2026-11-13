# The hosted demo — in plain English

Phase 10 is about letting a visitor try the agent without
installing anything. This page covers the piece that is built:
the **public-safe demo mode** — the one server build that is
meant to sit on a public address. The demo is deployed and
live at **https://sre-agent-demo.onrender.com** (Render free
plan, deployed 12 November 2026, `/health` verified from
outside).

## What public-safe means here

The hosted demo is the shipped demo server with every safety
rule switched on at once, in one place in the code
(`src/sre_agent/hosted_demo.py`), so a deployment cannot quietly
forget one of them:

- **A token is required, with no fallback.** The server is not
  built at all without a token: building it with no token (or an
  empty one) fails with an error, instead of falling back to the
  unauthenticated mode the local server allows. The token is
  supplied in the `SRE_AGENT_API_TOKEN` environment variable,
  and every route except `/health` requires it in the
  `X-SRE-Agent-Token` header.
- **Seeded demo data only.** Diagnosis reads the seeded demo
  data, and actions run against the seeded demo workloads. No
  real system is read or touched.
- **No real credentials anywhere in the stack.** The only secret
  is the shared token itself, chosen by whoever hosts the demo.
  The demo data, the demo workloads and the demo memory need no
  keys, accounts or passwords.
- **The Tier 3 demo grant is the only action that can run.**
  That grant pre-approves two fixes — restart and scale — in the
  `shop` namespace only, touching at most three replicas at a
  time. The server is built with approvals switched off
  (`tier3_only` in `server.py`): a fix the grant does not
  pre-approve, such as a rollback, is refused and can never run
  here, because there is no approval path for a visitor — or
  anyone — to use.
- **The ingestion caps stay on.** At most 100 alerts in one
  Alertmanager payload, capped labels and summaries, at most
  200 evidence records per incident and 1,000 incidents held.
  Past a cap the server refuses, clearly, instead of growing.

## Run it locally, the way it will run hosted

```bash
SRE_AGENT_API_TOKEN=choose-a-token \
  uvicorn --factory sre_agent.hosted_demo:create_hosted_demo_app \
  --host 0.0.0.0 --port 8080
```

Then a visitor's path, with the token in the header:

1. Open the address in a browser: the landing page at `GET /`
   explains the demo and prints the token. It and `GET /health`
   are the two open routes; every API route needs the token.
2. Send the demo checkout alert to `POST /webhooks/generic` and
   read the diagnosis on the incident it opens.
3. Propose the restart (`POST /incidents/<id>/actions` with
   `restart-workload`): the Tier 3 grant allows it outright, so
   it runs at once and the incident moves to `mitigating`.
4. Close the incident by sending the same alert with
   `"status": "resolved"`.

The tests in `tests/test_hosted_demo.py` run exactly this path,
plus the refusals: no token, a wrong token, a rollback, and an
oversize payload.

## Deploying it — the host, the cost, and the one step left

The host is chosen, and the cost is named here before anything
is paid for, as the plan requires:

- **Host: Render**, as a **free web service**. The cost is
  **$0**: nothing is paid, and no card is needed for the free
  plan. If the demo ever outgrows the free plan, the cost of a
  paid plan will be named here before anything is paid for then
  either.
- **The package is ready in this repo.** `render.yaml` is a
  Render Blueprint: it names the service (`sre-agent-demo`),
  the free plan, the Oregon region, the Docker runtime, and
  `Dockerfile.hosted` as the image to build. `Dockerfile.hosted`
  installs from the hash-checked lock file, runs as a user who
  is not root, and starts the hosted demo factory — the build
  that refuses to start without a token — on the port Render
  assigns (`$PORT`). The token is never written in this repo:
  `render.yaml` marks `SRE_AGENT_API_TOKEN` as `sync: false`,
  which means Render asks for its value at deploy time instead.
- **The health check is set.** `render.yaml` sets Render's
  health check path to `/health` — the one open route — so
  Render watches the service's health at its URL once it is
  deployed, and replaces a copy that stops answering it.
- **The free plan's limits, stated before anyone deploys.** A
  free web service spins down after 15 minutes with no traffic,
  and the next visitor waits about a minute while it starts
  again. A workspace gets 750 free instance hours a month; when
  they run out, its free services pause until the next month.
  That is acceptable for a demo people visit now and then, and
  it is not production hosting — Render says so itself.

The deployment package was verified in its build pass the one
way this environment allows: there is no Docker here, so the
image itself was not built; instead the image's exact steps
were run by hand — install from the lock file with hashes
checked, install the package, start the hosted factory with a
token on a chosen port — and a visitor's whole path was then
run over real HTTP against that server: `/health` answered,
a request with no token was refused, the demo alert was
diagnosed, the Tier 3 restart ran to `mitigating`, and the
resolved alert closed the incident.

**The one step left is Abraham's.** Deploying needs Abraham's
own Render account and his own sign-in, which no one else can
do for him:

1. Sign in to Render and choose **New → Blueprint**.
2. Select this repository (`KakoozaJerry/sre-agent`). Render
   reads `render.yaml` and proposes the `sre-agent-demo`
   service on the free plan.
3. When Render asks for `SRE_AGENT_API_TOKEN`, type a token
   value of his choosing. That value goes only into Render —
   never into this repo.
4. Apply the Blueprint. Render builds `Dockerfile.hosted`,
   starts the service, checks `/health`, and gives it a public
   HTTPS address ending in `onrender.com`.

That step happened on 12 November 2026. The address is
**https://sre-agent-demo.onrender.com**, recorded in
[progress.md](progress.md), and the README carries its
"Try the hosted demo" link pointing at it. The visitor token
is `sre-agent-demo-visitor`, sent in the
`X-SRE-Agent-Token` header — public on purpose, for the
reasons this page gives.

## Why the hosted demo's token is public

The visitor token (`sre-agent-demo-visitor`) is printed in
the README and in this guide, on purpose. A shared token
that everyone knows is not a secret, and this section says
plainly why that is acceptable here — and only here:

- **It is read-mostly.** The token's main job is letting a
  visitor read the seeded incidents and diagnoses. It is a
  gate against a completely open server, not a password
  protecting anything private: there is nothing private
  behind it.
- **No real data.** Every incident, number, workload and
  past incident behind the token is seeded demo data,
  made up for the demo. Nothing real can leak, because
  nothing real is there.
- **Tier-3-only.** The only action that can run behind the
  token is the Tier 3 demo grant — restart and scale in
  the `shop` namespace only, at most three replicas — and
  it runs against seeded demo workloads. Rollback can
  never run here, because hosted mode has no approval path
  at all.
- **Capped.** The ingestion caps stay on: a visitor can
  open only so many pretend incidents, of only so large a
  size, before the server refuses.

That reasoning belongs to this demo only. A server holding
real incidents needs a real, private token — the local
server's rule, stated in the operator guide, is that a
token you set is a secret you keep.

## What the hosted demo cannot do — honestly

- It cannot touch anything real. Every number, workload and
  past incident is seeded demo data.
- It cannot run a fix outside the Tier 3 demo grant. Rollback
  always needs a human, and hosted mode gives no one a way to
  approve it — that is deliberate, not a missing feature.
- A run is not a recovery: a restart that runs moves its
  incident to `mitigating`, never straight to `resolved`.
  Resolution still arrives only as a resolved alert.
- It sleeps. On Render's free plan the service spins down
  after about 15 minutes idle, so the first request can take
  a minute to wake it, and a workspace gets 750 free instance
  hours a month. It is a demo, not a production deployment.
