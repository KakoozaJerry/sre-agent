"""The public-safe hosted demo server (Phase 10).

In plain words, this is the one server build that is meant to be
put on a public address later. It is the shipped demo server with
every public-safety rule switched on at once, in one place, so a
deployment cannot quietly forget one of them:

- **A token is required.** The app is not built at all without
  one: ``create_hosted_demo_app`` refuses an empty token instead
  of falling back to the unauthenticated local mode the shipped
  app allows. With a token set, every route except ``/health``
  and the landing page at ``/`` requires it, exactly as in
  ``server.py`` (finding F-01). The landing page is open for the
  same reason ``/health`` is: it serves no data and runs no
  action — it only explains the demo and prints the visitor
  token, which is public on purpose.
- **Seeded demo data only.** Diagnosis reads the seeded demo tool
  layer, the seeded demo read adapters and the seeded demo
  memory, and actions run against the seeded demo workloads.
  No real system is read or touched, and no real credential is
  used anywhere in the stack: the only secret is the shared
  token itself, supplied by whoever hosts it.
- **The Tier 3 demo grant is the only action that can run.**
  The policy is ``tier3.demo_tier3_policy`` — restart and scale,
  in the ``shop`` namespace only, at most three replicas touched
  per action — and the server is built ``tier3_only=True``, so
  there is no approval path: a fix the grant does not pre-approve
  (rollback is the example) is refused and can never run here.
- **The ingestion caps stay on.** The store is the ordinary
  incident store, so the F-02 caps apply unchanged: at most 100
  alerts per Alertmanager payload, capped labels and summaries,
  at most 200 evidence records per incident and 1,000 incidents
  held, with a clear refusal past a cap.

Run it locally the same way it will run when hosted, with the
token in the environment:

    SRE_AGENT_API_TOKEN=<a token you choose> \
        uvicorn --factory sre_agent.hosted_demo:create_hosted_demo_app \
        --host 0.0.0.0 --port 8080

Honest scope: this module is the public-safe *mode*. The
deployment it was built for is live (Render free plan,
https://sre-agent-demo.onrender.com, deployed 2026-11-12 with
the host and cost named first, as the plan required); the mode
itself claims nothing beyond what is written here. See
``docs/hosted-demo.md``.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .adapters import demo_adapter_sources
from .memory import demo_incident_memory
from .runbooks import demo_runbooks
from .server import ENV_API_TOKEN, create_app
from .tier3 import demo_tier3_policy
from .tools import demo_tool_layer

LANDING_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>sre-agent — hosted demo</title>
<style>
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 44rem; padding: 0 1rem; line-height: 1.5; }
code, pre { background: #f2f2f2; padding: 0.1rem 0.3rem; border-radius: 4px; }
pre { padding: 0.75rem; overflow-x: auto; }
</style>
</head>
<body>
<h1>sre-agent — hosted demo</h1>
<p>An autonomous SRE agent that detects production incidents, works out
what is wrong using real evidence, and helps fix them — without ever
being allowed to make things worse on its own. This is its public demo:
seeded demo data only, nothing real is read or touched.</p>

<h2>The visitor token</h2>
<p>Every API route needs the token in the <code>X-SRE-Agent-Token</code>
header. The demo token is public on purpose:</p>
<pre>X-SRE-Agent-Token: sre-agent-demo-visitor</pre>

<h2>Try it</h2>
<p>Send the demo checkout alert, and read the diagnosis it opens:</p>
<pre>curl -X POST https://sre-agent-demo.onrender.com/webhooks/generic \
  -H 'Content-Type: application/json' \
  -H 'X-SRE-Agent-Token: sre-agent-demo-visitor' \
  -d '{"alert_name":"HighErrorRate","service":"checkout","namespace":"shop","environment":"demo","summary":"Checkout errors are above 10%"}'

curl https://sre-agent-demo.onrender.com/incidents \
  -H 'X-SRE-Agent-Token: sre-agent-demo-visitor'</pre>
<p>Propose the restart for the incident and it runs at once — the
demo's Tier 3 grant pre-approves it. Send the same alert again with
<code>"status": "resolved"</code> to close the incident.</p>

<h2>What this demo cannot do</h2>
<ul>
<li>It cannot touch anything real. Every number, workload and past
incident is seeded demo data.</li>
<li>It cannot run a fix outside the Tier 3 demo grant (restart and
scale, shop namespace only, at most three replicas). Rollback always
needs a human, and hosted mode has no approval path at all — that is
deliberate.</li>
<li>It sleeps. On the free hosting plan the service spins down after
about 15 minutes idle, so the first request can take a minute.</li>
</ul>
<p>Source, scores and the full record:
<a href="https://github.com/KakoozaJerry/sre-agent">github.com/KakoozaJerry/sre-agent</a></p>
</body>
</html>
"""


def create_hosted_demo_app(auth_token: str | None = None) -> FastAPI:
    """Build the public-safe hosted demo server, or refuse.

    ``auth_token`` wins when given; otherwise the token is read
    from the ``SRE_AGENT_API_TOKEN`` environment variable, the
    same variable the server and the CLI already use. An empty
    or missing token raises ``ValueError``: a hosted demo with
    no token would be an unauthenticated server on a public
    address, and that build is refused here rather than warned
    about in a guide a deployer might not read.
    """
    token = auth_token
    if token is None:
        token = os.environ.get(ENV_API_TOKEN) or None
    if not token:
        raise ValueError(
            "the hosted demo requires an API token: pass auth_token "
            f"or set {ENV_API_TOKEN}; refusing to build an "
            "unauthenticated server for a public address"
        )
    _, runbook_backend = demo_runbooks()
    app = create_app(
        auth_token=token,
        tool_layer=demo_tool_layer(),
        adapters=demo_adapter_sources(),
        memory=demo_incident_memory(),
        approval_policy=demo_tier3_policy(),
        runbook_backend=runbook_backend,
        tier3_only=True,
    )

    @app.get("/", response_class=HTMLResponse)
    def landing() -> str:
        return LANDING_PAGE

    return app
