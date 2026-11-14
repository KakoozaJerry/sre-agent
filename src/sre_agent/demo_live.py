"""The live Compose demo server (Phase 12).

In plain words, this is the server the Docker Compose demo
runs. It is the shipped server with one deliberate change: its
diagnosis reads the demo's own live services instead of the
seeded demo picture.

- **Metrics** come from the demo's live Prometheus
  (``PrometheusMetricsBackend``): the same Prometheus that
  scrapes the sample service and fires ``HighErrorRate``.
- **Service status** is derived from that same Prometheus
  (``PrometheusServiceStatusBackend``): degraded when its
  latest error value is above zero, healthy when it is not.
- **Log lines** come from the sample service itself
  (``SampleServiceLogsBackend``): the lines it recorded for
  the requests it really served, at ``GET /logs``.

So when ``demo/inject_fault.sh`` turns the failure on and makes
its five requests, the diagnosis that lands is a diagnosis of
that fault — the live error count, the live log lines — and
when the failure is off and no error has been recorded, the
loop honestly says "I don't know" instead of repeating the
seeded picture. No seeded adapter bundle and no seeded memory
are wired in here, for the same reason: extra evidence and
advice in this mode must be live too, or not present. The
shipped ``sre_agent.server:app`` is unchanged (it is still the
seeded demo server the README walkthrough uses), and the
hosted demo is untouched — it stays seeded and Tier-3-only by
design, as its own guide states.

Addresses come from the environment, with the Compose
network's names as defaults:

- ``SRE_AGENT_PROMETHEUS_URL`` (default
  ``http://prometheus:9090``)
- ``SRE_AGENT_SAMPLE_SERVICE_URL`` (default
  ``http://sample-service:8000``)

Both are plain http to a host that is not this machine, which
the backend address rules refuse by default (finding F-05).
The Compose network is a private network on one operator's
machine and neither service takes a credential, so the
factory names each address's own host in the explicit
``allow_http_hosts`` exception in ``backend_url.py`` — a
separate, written-down decision for those two hosts only,
never a widening of the general rule. Run it the way Compose
does:

    uvicorn --factory sre_agent.demo_live:create_live_demo_app \
        --host 0.0.0.0 --port 8080

Honest limits, stated the same way the demo guide states
them: a fix approved here still runs against the seeded demo
workloads, not against the sample container — the sample
service stops failing only when its chaos switch is turned
off — and this server still has no token set in the Compose
demo, so it is a local demo for one machine, not a public
server.
"""

from __future__ import annotations

import os
import urllib.parse

from fastapi import FastAPI

from .policy import EnvironmentPolicy, Tier
from .runbooks import demo_runbooks
from .server import create_app
from .tools import (
    PrometheusMetricsBackend,
    PrometheusServiceStatusBackend,
    SampleServiceLogsBackend,
    ToolLayer,
)

ENV_PROMETHEUS_URL = "SRE_AGENT_PROMETHEUS_URL"
ENV_SAMPLE_SERVICE_URL = "SRE_AGENT_SAMPLE_SERVICE_URL"
DEFAULT_PROMETHEUS_URL = "http://prometheus:9090"
DEFAULT_SAMPLE_SERVICE_URL = "http://sample-service:8000"


def _host_of(url: str) -> str:
    host = urllib.parse.urlparse(url).hostname
    return host or ""


def live_demo_tool_layer(
    prometheus_url: str = DEFAULT_PROMETHEUS_URL,
    sample_service_url: str = DEFAULT_SAMPLE_SERVICE_URL,
) -> ToolLayer:
    """Build the live tool layer: live Prometheus for metrics
    and status, the live sample service for log lines."""
    metrics = PrometheusMetricsBackend(
        prometheus_url, allow_http_hosts=[_host_of(prometheus_url)]
    )
    logs = SampleServiceLogsBackend(
        sample_service_url,
        allow_http_hosts=[_host_of(sample_service_url)],
    )
    services = PrometheusServiceStatusBackend(metrics)
    return ToolLayer(metrics=metrics, logs=logs, services=services)


def create_live_demo_app() -> FastAPI:
    """Build the Compose demo's server, with live diagnosis.

    A factory (no arguments), so ``uvicorn --factory`` can call
    it the way ``docker-compose.yml`` does. The two service
    addresses are read from the environment at build time.
    Approvals are the shipped demo's Tier 2 flow against the
    demo runbook backend, exactly as in the shipped app; only
    the diagnosis data changed from seeded to live.
    """
    prometheus_url = (
        os.environ.get(ENV_PROMETHEUS_URL) or DEFAULT_PROMETHEUS_URL
    )
    sample_url = (
        os.environ.get(ENV_SAMPLE_SERVICE_URL) or DEFAULT_SAMPLE_SERVICE_URL
    )
    _, runbook_backend = demo_runbooks()
    return create_app(
        tool_layer=live_demo_tool_layer(prometheus_url, sample_url),
        approval_policy=EnvironmentPolicy(
            environment="demo",
            granted_tier=Tier.ACT_WITH_APPROVAL,
            max_replicas_affected=5,
        ),
        runbook_backend=runbook_backend,
    )
