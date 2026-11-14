"""Datadog read adapter: a metrics query (read-only).

Part of the read adapters package (see the package docstring for
the shared safety rules). This module is the Datadog piece: the
pure parser for a Datadog metrics reply, an in-memory demo
backend, the live API backend, and the adapter that returns the
tool layer's own metrics result shape, so a Datadog series
becomes evidence exactly like a Prometheus one.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from ..backend_url import validate_backend_url
from ..logging_config import get_logger
from ..tools import (
    MetricSample,
    MetricSeries,
    QueryMetricsRequest,
    QueryMetricsResult,
    metric_name_from_query,
    redact_secrets,
)

log = get_logger(__name__)


def parse_datadog_response(payload: dict[str, object]) -> list[MetricSeries]:
    """Turn a Datadog metrics query reply into series.

    Pure function, tested against a recorded reply shape. Datadog
    answers with a ``series`` list; each entry names its metric,
    carries its tags as ``"key:value"`` strings, and holds its
    points as ``[timestamp-milliseconds, value]`` pairs. A reply
    whose status is not ``ok`` gives back no series.
    """
    if payload.get("status") != "ok":
        return []
    raw_series = payload.get("series")
    if not isinstance(raw_series, list):
        return []
    series: list[MetricSeries] = []
    for item in raw_series:
        if not isinstance(item, dict):
            continue
        labels: dict[str, str] = {}
        tags = item.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str) and ":" in tag:
                    key, value = tag.split(":", 1)
                    labels[key] = value
        scope = item.get("scope")
        if isinstance(scope, str) and ":" in scope:
            key, value = scope.split(":", 1)
            labels.setdefault(key, value)
        samples: list[MetricSample] = []
        pointlist = item.get("pointlist")
        if isinstance(pointlist, list):
            for point in pointlist:
                if not (isinstance(point, list) and len(point) == 2):
                    continue
                try:
                    ts = datetime.fromtimestamp(float(point[0]) / 1000.0, tz=UTC)
                    samples.append(MetricSample(timestamp=ts, value=float(point[1])))
                except (TypeError, ValueError):
                    continue
        series.append(
            MetricSeries(
                metric=str(item.get("metric", "unknown")), labels=labels, samples=samples
            )
        )
    return series


class InMemoryDatadogBackend:
    """Demo Datadog backend: series held in memory, seeded by tests."""

    def __init__(self) -> None:
        self._series: list[MetricSeries] = []

    def add_series(
        self, metric: str, labels: dict[str, str], samples: list[MetricSample]
    ) -> None:
        self._series.append(
            MetricSeries(metric=metric, labels=dict(labels), samples=list(samples))
        )

    def fetch(self, query: str) -> list[MetricSeries]:
        # Datadog queries open with an aggregator — "sum:metric{...}".
        # The metric name follows it; without this step the whole
        # "sum:metric" string would be mistaken for the name.
        head = query.split("{", 1)[0]
        prefix, _, rest = head.partition(":")
        candidate = rest if rest and prefix in {"sum", "avg", "max", "min", "count"} else query
        name = metric_name_from_query(candidate)
        if name is None:
            return []
        return [s for s in self._series if s.metric == name]


class DatadogMetricsBackend:
    """Datadog backend that asks the real Datadog API.

    The API key and application key are passed in by the caller
    and sent only as request headers — never logged, never put
    on a result. The query window is the last hour by default.
    """

    def __init__(
        self,
        api_key: str,
        app_key: str,
        base_url: str = "https://api.datadoghq.com",
        window_seconds: int = 3600,
        timeout_seconds: float = 5.0,
        allowed_hosts: list[str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._app_key = app_key
        self._base_url = validate_backend_url(
            base_url, allowed_hosts=allowed_hosts, what="Datadog address"
        )
        self._window_seconds = window_seconds
        self._timeout = timeout_seconds

    def fetch(self, query: str) -> list[MetricSeries]:
        now = int(datetime.now(UTC).timestamp())
        qs = urllib.parse.urlencode(
            {"query": query, "from": now - self._window_seconds, "to": now}
        )
        request = urllib.request.Request(
            f"{self._base_url}/api/v1/query?{qs}",
            headers={
                "Accept": "application/json",
                "DD-API-KEY": self._api_key,
                "DD-APPLICATION-KEY": self._app_key,
            },
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            return []
        return parse_datadog_response(payload)


class DatadogAdapter:
    """The Datadog metrics read, returning the tool layer's shape.

    The result is a plain ``QueryMetricsResult``, so a Datadog
    series becomes evidence through the same ``to_evidence`` the
    Prometheus tool uses — the query on the record says it came
    from Datadog, so the source is never blurred.
    """

    def __init__(
        self, backend: InMemoryDatadogBackend | DatadogMetricsBackend | None = None
    ) -> None:
        self._backend = backend if backend is not None else InMemoryDatadogBackend()

    def query_metrics(self, request: QueryMetricsRequest) -> QueryMetricsResult:
        found = self._backend.fetch(request.query)
        truncated = len(found) > request.limit_series
        kept: list[MetricSeries] = []
        for series in found[: request.limit_series]:
            samples = series.samples
            if len(samples) > request.limit_points_per_series:
                truncated = True
                samples = samples[-request.limit_points_per_series :]
            kept.append(
                MetricSeries(
                    metric=series.metric,
                    labels={k: redact_secrets(v) for k, v in series.labels.items()},
                    samples=samples,
                )
            )
        log.info("datadog query_metrics %r -> %d series", request.query, len(kept))
        return QueryMetricsResult(
            query=f"datadog: {request.query}",
            series=kept,
            total_series_found=len(found),
            truncated=truncated,
        )
