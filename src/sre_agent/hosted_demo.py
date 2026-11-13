"""The public-safe hosted demo server (Phase 10).

In plain words, this is the one server build that is meant to be
put on a public address later. It is the shipped demo server with
every public-safety rule switched on at once, in one place, so a
deployment cannot quietly forget one of them:

- **A token is required.** The app is not built at all without
  one: ``create_hosted_demo_app`` refuses an empty token instead
  of falling back to the unauthenticated local mode the shipped
  app allows. With a token set, every route except ``/health``
  requires it, exactly as in ``server.py`` (finding F-01).
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

Honest scope: this module is the public-safe *mode*. It is not a
deployment — there is no public URL yet, and nothing here claims
one. Deploying it, naming the host and any cost, and watching its
health are the next Phase 10 items. See ``docs/hosted-demo.md``.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from .adapters import demo_adapter_sources
from .memory import demo_incident_memory
from .runbooks import demo_runbooks
from .server import ENV_API_TOKEN, create_app
from .tier3 import demo_tier3_policy
from .tools import demo_tool_layer


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
    return create_app(
        auth_token=token,
        tool_layer=demo_tool_layer(),
        adapters=demo_adapter_sources(),
        memory=demo_incident_memory(),
        approval_policy=demo_tier3_policy(),
        runbook_backend=runbook_backend,
        tier3_only=True,
    )
