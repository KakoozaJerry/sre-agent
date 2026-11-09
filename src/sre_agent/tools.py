"""Tool layer v1: read-only ways for the agent to gather evidence.

Three tools, all look-only. None of them can change anything:

- ``query_metrics`` — ask a metrics backend a PromQL question.
- ``search_logs`` — search log lines (in memory, or from a file in
  the demo).
- ``get_service_status`` — ask whether a service is up, and how
  many copies of it are ready.

How safety is kept, in plain words:

- **Read-only by construction.** The tools only fetch. There is no
  method here that writes, restarts, scales, or deletes.
- **Inputs are checked.** Every request is a typed model. An empty
  or over-long query, an unbalanced PromQL string, or a limit
  outside the allowed range is rejected before anything runs.
- **Outputs are bounded.** A query can return only so many series,
  so many points per series, and so many log lines. Anything cut
  off is flagged with ``truncated`` so the reader knows.
- **Outputs are cleaned.** Log lines and messages pass a filter
  that replaces common secret shapes (passwords, tokens, keys)
  with ``[redacted]`` before they are stored or shown.
- **Output is data, never orders.** A tool result becomes an
  ``Evidence`` record. Nothing in it is ever run or obeyed. See
  docs/safety-model.md.

Backends sit behind small classes so the tool shapes stay the same
when the demo backends are swapped for real ones. This pass ships
the demo backends (memory, file, and a Prometheus reply parser);
wiring a live Prometheus or Loki server plugs into the same
interface later, exactly as the incident store worked in Phase 1.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from .logging_config import get_logger
from .models import Evidence, Target

log = get_logger(__name__)

# Hard bounds. These are the tool layer's own limits; a caller may
# ask for less, never for more.
MAX_PROMQL_LENGTH = 500
MAX_LOG_TEXT_LENGTH = 200
MAX_SERIES = 20
MAX_POINTS_PER_SERIES = 200
MAX_LOG_LIMIT = 100
MAX_LOG_MESSAGE_CHARS = 500
MAX_EVIDENCE_SUMMARY_CHARS = 1000

# Words in a PromQL query that are functions or keywords, not metric
# names. The demo backend uses this to find the metric being asked
# about; a real Prometheus server does the full parsing itself.
_PROMQL_NON_METRICS = {
    "rate",
    "irate",
    "increase",
    "sum",
    "avg",
    "max",
    "min",
    "count",
    "by",
    "without",
    "histogram_quantile",
    "clamp_max",
    "clamp_min",
    "abs",
    "ceil",
    "floor",
    "round",
    "on",
    "group_left",
    "group_right",
    "and",
    "or",
    "unless",
}

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|apikey)"
               r"(\s*[:=]\s*)\S+"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
]


def redact_secrets(text: str) -> str:
    """Replace common secret shapes in *text* with ``[redacted]``.

    This is a safety net, not a promise: it catches the shapes
    secrets most often take in log lines. The first pattern keeps
    the key name (``password=``) so the line still makes sense.
    """
    out = text
    for idx, pattern in enumerate(_SECRET_PATTERNS):
        if idx == 0:
            out = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", out)
        else:
            out = pattern.sub("[redacted]", out)
    return out


def _bound(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…[truncated]"


# ---------------------------------------------------------------------------
# Metrics: PromQL query
# ---------------------------------------------------------------------------


class MetricSample(BaseModel):
    """One measured value at one moment."""

    timestamp: datetime
    value: float


class MetricSeries(BaseModel):
    """One line of measurements, with the labels that name it."""

    metric: str
    labels: dict[str, str] = Field(default_factory=dict)
    samples: list[MetricSample] = Field(default_factory=list)


class QueryMetricsRequest(BaseModel):
    """A PromQL question. Checked before it is allowed to run."""

    query: str
    limit_series: int = Field(default=10, ge=1, le=MAX_SERIES)
    limit_points_per_series: int = Field(default=50, ge=1, le=MAX_POINTS_PER_SERIES)

    @field_validator("query")
    @classmethod
    def _query_must_be_safe(cls, v: str) -> str:
        q = v.strip()
        if not q:
            raise ValueError("PromQL query must not be empty")
        if len(q) > MAX_PROMQL_LENGTH:
            raise ValueError(f"PromQL query is too long (limit {MAX_PROMQL_LENGTH})")
        if ";" in q or "\n" in q or "\r" in q:
            raise ValueError("PromQL query must be a single query with no semicolons")
        for open_ch, close_ch in (("{", "}"), ("[", "]"), ("(", ")")):
            if q.count(open_ch) != q.count(close_ch):
                raise ValueError(f"PromQL query has unbalanced '{open_ch}{close_ch}'")
        return q


class QueryMetricsResult(BaseModel):
    query: str
    series: list[MetricSeries] = Field(default_factory=list)
    total_series_found: int = 0
    truncated: bool = False

    def to_evidence(self) -> Evidence:
        """Turn this result into an evidence record for an incident."""
        if not self.series:
            summary = f"No series matched PromQL query: {self.query}"
        else:
            parts: list[str] = []
            for s in self.series[:3]:
                if s.samples:
                    last = s.samples[-1]
                    parts.append(
                        f"{s.metric}{s.labels} last value {last.value:g} "
                        f"at {last.timestamp.isoformat()}"
                    )
                else:
                    parts.append(f"{s.metric}{s.labels} (no samples)")
            summary = "; ".join(parts)
            if self.total_series_found > len(self.series):
                summary += f"; +{self.total_series_found - len(self.series)} more series"
            if self.truncated:
                summary += " [truncated]"
        return Evidence(
            kind="metric",
            query=f"promql: {self.query}",
            summary=_bound(redact_secrets(summary), MAX_EVIDENCE_SUMMARY_CHARS),
        )


def metric_name_from_query(query: str) -> str | None:
    """Find the metric a PromQL query is mainly about, if there is one.

    The demo backend matches on this name. It is a simple, honest
    helper: it skips known function names and label names inside
    ``{...}``, and returns the first other identifier it finds.
    """
    without_labels = re.sub(r"\{[^}]*\}", " ", query)
    without_labels = re.sub(r"\b(?:by|without)\s*\([^)]*\)", " ", without_labels)
    for token in re.findall(r"[a-zA-Z_:][a-zA-Z0-9_:]*", without_labels):
        if token.lower() not in _PROMQL_NON_METRICS:
            return token
    return None


class InMemoryMetricsBackend:
    """Demo metrics backend: series held in memory.

    ``add_series`` exists only so a demo or a test can seed data.
    The tool itself never calls it; querying is read-only.
    """

    def __init__(self) -> None:
        self._series: list[MetricSeries] = []

    def add_series(
        self, metric: str, labels: dict[str, str], samples: list[MetricSample]
    ) -> None:
        self._series.append(
            MetricSeries(metric=metric, labels=dict(labels), samples=list(samples))
        )

    def fetch(self, query: str) -> list[MetricSeries]:
        name = metric_name_from_query(query)
        if name is None:
            return []
        return [s for s in self._series if s.metric == name]


def parse_prometheus_response(payload: dict[str, object]) -> list[MetricSeries]:
    """Turn a Prometheus ``/api/v1/query`` reply into series.

    Pure function, so it can be tested without a server. Handles
    both instant replies (one value per series) and range replies
    (a list of values per series). A reply whose status is not
    ``success`` gives back no series.
    """
    if payload.get("status") != "success":
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if not isinstance(result, list):
        return []
    series: list[MetricSeries] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        metric_labels = item.get("metric")
        labels: dict[str, str] = {}
        metric_name = "unknown"
        if isinstance(metric_labels, dict):
            for k, v in metric_labels.items():
                if k == "__name__":
                    metric_name = str(v)
                else:
                    labels[str(k)] = str(v)
        samples: list[MetricSample] = []
        raw_values = item.get("values")
        raw_value = item.get("value")
        points: list[object] = []
        if isinstance(raw_values, list):
            points = raw_values
        elif isinstance(raw_value, list):
            points = [raw_value]
        for point in points:
            if not (isinstance(point, list) and len(point) == 2):
                continue
            try:
                ts = datetime.fromtimestamp(float(point[0]), tz=UTC)
                samples.append(MetricSample(timestamp=ts, value=float(point[1])))
            except (TypeError, ValueError):
                continue
        series.append(MetricSeries(metric=metric_name, labels=labels, samples=samples))
    return series


class PrometheusMetricsBackend:
    """Metrics backend that asks a real Prometheus server.

    Uses only the standard library, so no new dependency is needed.
    The reply parsing lives in :func:`parse_prometheus_response`.
    """

    def __init__(self, base_url: str, timeout_seconds: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def fetch(self, query: str) -> list[MetricSeries]:
        qs = urllib.parse.urlencode({"query": query})
        url = f"{self._base_url}/api/v1/query?{qs}"
        with urllib.request.urlopen(url, timeout=self._timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            return []
        return parse_prometheus_response(payload)


# ---------------------------------------------------------------------------
# Logs: search
# ---------------------------------------------------------------------------


class LogEntry(BaseModel):
    timestamp: datetime
    service: str
    namespace: str = "default"
    level: str = "INFO"
    message: str


class SearchLogsRequest(BaseModel):
    """A log search. An empty ``text`` means "all lines that match
    the other filters"."""

    text: str = ""
    service: str | None = None
    namespace: str | None = None
    level: str | None = None
    limit: int = Field(default=20, ge=1, le=MAX_LOG_LIMIT)

    @field_validator("text")
    @classmethod
    def _text_must_be_bounded(cls, v: str) -> str:
        if len(v) > MAX_LOG_TEXT_LENGTH:
            raise ValueError(f"log search text is too long (limit {MAX_LOG_TEXT_LENGTH})")
        return v

    @field_validator("level")
    @classmethod
    def _level_must_be_known(cls, v: str | None) -> str | None:
        if v is None:
            return v
        upper = v.upper()
        if upper not in {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unknown log level: {v!r}")
        return "WARN" if upper == "WARNING" else upper


class SearchLogsResult(BaseModel):
    text: str
    entries: list[LogEntry] = Field(default_factory=list)
    total_found: int = 0
    truncated: bool = False

    def to_evidence(self) -> Evidence:
        if not self.entries:
            summary = f"No log lines matched search: {self.text!r}"
        else:
            first = self.entries[0]
            summary = (
                f"{self.total_found} log line(s) matched {self.text!r}; "
                f"first: [{first.level}] {first.service}: {first.message}"
            )
            if self.truncated:
                summary += " [truncated]"
        return Evidence(
            kind="log",
            query=f"log search: {self.text!r}",
            summary=_bound(redact_secrets(summary), MAX_EVIDENCE_SUMMARY_CHARS),
        )


def _entry_matches(entry: LogEntry, request: SearchLogsRequest) -> bool:
    if request.text and request.text.lower() not in entry.message.lower():
        return False
    if request.service is not None and entry.service != request.service:
        return False
    if request.namespace is not None and entry.namespace != request.namespace:
        return False
    return not (request.level is not None and entry.level.upper() != request.level)


class InMemoryLogsBackend:
    """Demo log backend: lines held in memory. Seeding is test/demo
    setup only; searching is read-only."""

    def __init__(self, entries: list[LogEntry] | None = None) -> None:
        self._entries: list[LogEntry] = list(entries or [])

    def add_entry(self, entry: LogEntry) -> None:
        self._entries.append(entry)

    def search(self, request: SearchLogsRequest) -> list[LogEntry]:
        return [e for e in self._entries if _entry_matches(e, request)]


class FileLogsBackend:
    """Demo log backend that searches a plain file, one line at a time.

    Each line may be plain text, or a JSON object with ``message``,
    ``service``, ``namespace``, ``level`` and ``timestamp`` fields.
    A missing file simply gives no lines; it is not an error, so a
    demo with no log file yet still works.
    """

    def __init__(self, path: str | Path, default_service: str = "unknown") -> None:
        self._path = Path(path)
        self._default_service = default_service

    def _load(self) -> list[LogEntry]:
        if not self._path.exists():
            return []
        entries: list[LogEntry] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entries.append(self._parse_line(line))
        return entries

    def _parse_line(self, line: str) -> LogEntry:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return LogEntry(
                timestamp=datetime.now(UTC),
                service=self._default_service,
                message=line,
            )
        if not isinstance(obj, dict):
            return LogEntry(
                timestamp=datetime.now(UTC),
                service=self._default_service,
                message=line,
            )
        ts = obj.get("timestamp")
        when = datetime.now(UTC)
        if isinstance(ts, str):
            try:
                when = datetime.fromisoformat(ts)
            except ValueError:
                when = datetime.now(UTC)
        return LogEntry(
            timestamp=when,
            service=str(obj.get("service", self._default_service)),
            namespace=str(obj.get("namespace", "default")),
            level=str(obj.get("level", "INFO")),
            message=str(obj.get("message", "")),
        )

    def search(self, request: SearchLogsRequest) -> list[LogEntry]:
        return [e for e in self._load() if _entry_matches(e, request)]


# ---------------------------------------------------------------------------
# Service status
# ---------------------------------------------------------------------------


class ServiceStatus(BaseModel):
    service: str
    namespace: str
    environment: str
    # One of: healthy, degraded, down, unknown.
    status: str
    ready_replicas: int = Field(default=0, ge=0)
    desired_replicas: int = Field(default=0, ge=0)
    version: str = ""
    message: str = ""


class ServiceStatusResult(BaseModel):
    target: Target
    found: bool
    status: ServiceStatus | None = None

    def to_evidence(self) -> Evidence:
        t = self.target
        query = f"service status: {t.environment}/{t.namespace}/{t.service}"
        if not self.found or self.status is None:
            summary = f"No status known for service {t.service} in {t.namespace}"
        else:
            s = self.status
            summary = (
                f"Service {s.service} is {s.status}: "
                f"{s.ready_replicas}/{s.desired_replicas} replicas ready"
            )
            if s.version:
                summary += f", version {s.version}"
            if s.message:
                summary += f". {s.message}"
        return Evidence(
            kind="service",
            query=query,
            summary=_bound(redact_secrets(summary), MAX_EVIDENCE_SUMMARY_CHARS),
        )


class InMemoryServiceBackend:
    """Demo service backend. ``set_status`` is demo/test setup only;
    the tool itself only reads."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str, str], ServiceStatus] = {}

    def set_status(self, status: ServiceStatus) -> None:
        key = (status.environment, status.namespace, status.service)
        self._by_key[key] = status

    def get(self, target: Target) -> ServiceStatus | None:
        key = (target.environment, target.namespace, target.service)
        return self._by_key.get(key)


# ---------------------------------------------------------------------------
# The tool layer itself
# ---------------------------------------------------------------------------


class ToolLayer:
    """The three read-only tools, with bounds and cleaning applied
    in one place so no caller can skip them."""

    def __init__(
        self,
        metrics: InMemoryMetricsBackend | PrometheusMetricsBackend | None = None,
        logs: InMemoryLogsBackend | FileLogsBackend | None = None,
        services: InMemoryServiceBackend | None = None,
    ) -> None:
        self._metrics = metrics if metrics is not None else InMemoryMetricsBackend()
        self._logs = logs if logs is not None else InMemoryLogsBackend()
        self._services = services if services is not None else InMemoryServiceBackend()

    def query_metrics(self, request: QueryMetricsRequest) -> QueryMetricsResult:
        """Run a PromQL query and return bounded, cleaned series."""
        found = self._metrics.fetch(request.query)
        truncated = len(found) > request.limit_series
        kept: list[MetricSeries] = []
        for series in found[: request.limit_series]:
            samples = series.samples
            if len(samples) > request.limit_points_per_series:
                truncated = True
                samples = samples[-request.limit_points_per_series :]
            clean_labels = {
                k: redact_secrets(v) for k, v in series.labels.items()
            }
            kept.append(
                MetricSeries(metric=series.metric, labels=clean_labels, samples=samples)
            )
        log.info("query_metrics %r -> %d series", request.query, len(kept))
        return QueryMetricsResult(
            query=request.query,
            series=kept,
            total_series_found=len(found),
            truncated=truncated,
        )

    def search_logs(self, request: SearchLogsRequest) -> SearchLogsResult:
        """Search logs and return bounded, cleaned lines."""
        found = self._logs.search(request)
        truncated = len(found) > request.limit
        kept = [
            LogEntry(
                timestamp=e.timestamp,
                service=e.service,
                namespace=e.namespace,
                level=e.level,
                message=_bound(redact_secrets(e.message), MAX_LOG_MESSAGE_CHARS),
            )
            for e in found[: request.limit]
        ]
        log.info("search_logs %r -> %d lines", request.text, len(kept))
        return SearchLogsResult(
            text=request.text, entries=kept, total_found=len(found), truncated=truncated
        )

    def get_service_status(self, target: Target) -> ServiceStatusResult:
        """Look up one service. An unknown service is a normal
        answer (``found`` is false), not an error."""
        status = self._services.get(target)
        if status is None:
            return ServiceStatusResult(target=target, found=False)
        clean = status.model_copy(update={"message": redact_secrets(status.message)})
        return ServiceStatusResult(target=target, found=True, status=clean)


def demo_tool_layer() -> ToolLayer:
    """A small seeded demo, so the tools can be tried with no setup.

    The data is made up on purpose: a checkout service in the demo
    environment whose error rate is high and which is degraded.
    It claims nothing about any real system.
    """
    now = datetime.now(UTC)
    metrics = InMemoryMetricsBackend()
    metrics.add_series(
        "http_requests_total",
        {"service": "checkout", "namespace": "shop"},
        [
            MetricSample(timestamp=now, value=1240.0),
        ],
    )
    metrics.add_series(
        "http_request_errors_total",
        {"service": "checkout", "namespace": "shop"},
        [
            MetricSample(timestamp=now, value=162.0),
        ],
    )
    logs = InMemoryLogsBackend(
        [
            LogEntry(
                timestamp=now,
                service="checkout",
                namespace="shop",
                level="ERROR",
                message="payment gateway timeout after 2000ms",
            ),
            LogEntry(
                timestamp=now,
                service="checkout",
                namespace="shop",
                level="INFO",
                message="checkout request completed in 120ms",
            ),
        ]
    )
    services = InMemoryServiceBackend()
    services.set_status(
        ServiceStatus(
            service="checkout",
            namespace="shop",
            environment="demo",
            status="degraded",
            ready_replicas=2,
            desired_replicas=3,
            version="1.4.2",
            message="one replica is not ready",
        )
    )
    return ToolLayer(metrics=metrics, logs=logs, services=services)
