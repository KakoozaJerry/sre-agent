"""Read adapters: Kubernetes, Datadog and Azure Monitor (KQL).

In plain words, this module is how the agent looks at three more
places an SRE already uses — still only looking, never changing:

- **Kubernetes.** List the pods (the running copies of a workload)
  in one namespace, with each pod's phase, how many of its
  containers are ready, and how many times they have restarted.
  This is the read a person does first with ``kubectl get pods``.
- **Datadog.** Ask a metrics question, in the same shape the
  existing PromQL tool already returns, so a Datadog series can
  become evidence exactly like a Prometheus one.
- **Azure Monitor.** Run a KQL query (Kusto Query Language, the
  query language of Azure Log Analytics) against a workspace and
  get rows back as evidence.

How safety is kept, the same rules as the tool layer:

- **Read-only by construction.** Every backend here only fetches.
  There is no method that creates, scales, restarts or deletes
  anything, in any of the three systems. The KQL check also
  refuses management commands (the ones that start with a dot,
  such as ``.drop``), because those change a workspace, they do
  not read it.
- **Inputs are checked and outputs are bounded.** Namespace
  names, query lengths and result counts all have hard limits,
  anything cut off is flagged, and every text value passes the
  tool layer's secret cleaning before it can be stored or shown.
- **Secrets are passed in, never written down.** The live
  backends take their API keys or token as arguments from the
  caller — nothing here reads a key from a file, logs one, or
  has one in its code.
- **Backend addresses are checked.** Every live backend's
  address must pass the rules in ``backend_url.py`` (finding
  F-05) before a credential can be sent to it: https, or plain
  http to this machine only, no credentials inside the address,
  and an optional host allow-list per backend.
- **Replies are parsed by pure functions.** Each system's reply
  shape is turned into this project's own models by a function
  that takes the decoded reply and nothing else, so the parsing
  is tested against recorded reply shapes with no live account,
  no cluster and no network — the same two-layer honesty the
  demo environment uses.

Scope, stated honestly: the demo backends and the parsers are
built and tested here. The live backends are thin standard-
library HTTP clients behind the same shapes, but no live
Kubernetes cluster (kind or otherwise), Datadog account or Azure
workspace was available in the build environment, so no live
call is claimed as tested; running against a real kind cluster
is a human check, the same status the Docker demo had at first.

Feeding a diagnosis (Phase 7): an operator may select any of
these adapters as extra evidence sources for the reasoning loop
by handing an :class:`AdapterSources` bundle to it (or to the
server, which passes it on). That selection is the only wiring:
the loop asks each selected adapter one fixed question, built
only from the incident's validated target names, and the answers
become evidence records on the incident like any other. An
adapter that is not selected is never called, and a selected
adapter that fails is skipped — it never takes the diagnosis
down with it.

Since the Phase 12 restructure this is a package, split per
backend with no behaviour change: ``kubernetes.py``,
``datadog.py`` and ``azure.py`` hold one backend each,
``sources.py`` holds the bundle that selects adapters as
evidence sources plus the seeded demo, and ``_common.py`` holds
the one helper two backends share. Every name the old single
module exported is re-exported here, so
``from sre_agent.adapters import ...`` works exactly as before.
"""

from __future__ import annotations

from ._common import _bound as _bound  # re-exported: the old module held it
from .azure import (
    MAX_AZURE_ROWS,
    MAX_KQL_LENGTH,
    AzureAdapter,
    AzureLogAnalyticsBackend,
    AzureQueryResult,
    InMemoryAzureBackend,
    KqlQueryRequest,
    parse_azure_response,
)
from .datadog import (
    DatadogAdapter,
    DatadogMetricsBackend,
    InMemoryDatadogBackend,
    parse_datadog_response,
)
from .kubernetes import (
    MAX_NAMESPACE_LENGTH,
    MAX_PODS,
    InMemoryKubernetesBackend,
    KubernetesApiBackend,
    KubernetesPodsResult,
    KubernetesReader,
    ListPodsRequest,
    PodStatus,
    parse_kubernetes_pods,
)
from .sources import (
    AdaptersDemoReport,
    AdapterSources,
    demo_adapter_sources,
    demo_adapters,
)

__all__ = [
    "MAX_AZURE_ROWS",
    "MAX_KQL_LENGTH",
    "MAX_NAMESPACE_LENGTH",
    "MAX_PODS",
    "AdapterSources",
    "AdaptersDemoReport",
    "AzureAdapter",
    "AzureLogAnalyticsBackend",
    "AzureQueryResult",
    "DatadogAdapter",
    "DatadogMetricsBackend",
    "InMemoryAzureBackend",
    "InMemoryDatadogBackend",
    "InMemoryKubernetesBackend",
    "KqlQueryRequest",
    "KubernetesApiBackend",
    "KubernetesPodsResult",
    "KubernetesReader",
    "ListPodsRequest",
    "PodStatus",
    "demo_adapter_sources",
    "demo_adapters",
    "parse_azure_response",
    "parse_datadog_response",
    "parse_kubernetes_pods",
]
