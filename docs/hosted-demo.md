# The hosted demo — in plain English

Phase 10 is about letting a visitor try the agent without
installing anything. This page covers the piece that is built:
the **public-safe demo mode** — the one server build that is
meant to sit on a public address. The deployment itself is a
later step: there is **no public URL yet**, and nothing on this
page claims one.

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

1. Check it is up: `GET /health` (the one open route).
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

## What the hosted demo cannot do — honestly

- It cannot touch anything real. Every number, workload and
  past incident is seeded demo data.
- It cannot run a fix outside the Tier 3 demo grant. Rollback
  always needs a human, and hosted mode gives no one a way to
  approve it — that is deliberate, not a missing feature.
- A run is not a recovery: a restart that runs moves its
  incident to `mitigating`, never straight to `resolved`.
  Resolution still arrives only as a resolved alert.
- It is not deployed yet. Choosing the host, naming any cost
  before anything is paid for, putting the demo at a public
  HTTPS address and watching its health are the next Phase 10
  steps, and they will be recorded in
  [progress.md](progress.md) when they happen.
