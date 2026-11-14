"""Azure Monitor read adapter: a KQL query (read-only).

Part of the read adapters package (see the package docstring for
the shared safety rules). This module is the Azure piece: the
checked KQL request (management commands — the ones that start
with a dot, such as ``.drop`` — are refused, because they change
a workspace instead of reading it), the pure parser for a Log
Analytics reply, an in-memory demo backend, the live backend,
and the adapter that turns rows into evidence.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field, field_validator

from ..backend_url import validate_backend_url
from ..logging_config import get_logger
from ..models import Evidence
from ..tools import MAX_EVIDENCE_SUMMARY_CHARS, redact_secrets
from ._common import _bound

log = get_logger(__name__)

MAX_AZURE_ROWS = 50
MAX_KQL_LENGTH = 1000

# Azure management commands change a workspace; a read adapter
# must never send one. They all start with a dot.
_MANAGEMENT_RE = re.compile(r"(?i)(^|[;\s])\.(drop|delete|set|clear|ingest|create|alter)\b")


class KqlQueryRequest(BaseModel):
    """A KQL question. Checked before it is allowed to run.

    KQL itself is a read language, but a Log Analytics workspace
    also accepts management commands — those start with a dot and
    change things, so any query carrying one is refused here.
    """

    query: str
    limit_rows: int = Field(default=20, ge=1, le=MAX_AZURE_ROWS)

    @field_validator("query")
    @classmethod
    def _query_must_be_a_safe_read(cls, v: str) -> str:
        q = v.strip()
        if not q:
            raise ValueError("KQL query must not be empty")
        if len(q) > MAX_KQL_LENGTH:
            raise ValueError(f"KQL query is too long (limit {MAX_KQL_LENGTH})")
        if q.startswith(".") or _MANAGEMENT_RE.search(q):
            raise ValueError("KQL management commands are not reads and are refused")
        return q


def parse_azure_response(
    payload: dict[str, object],
) -> tuple[list[str], list[dict[str, str]]]:
    """Turn an Azure Log Analytics query reply into columns and rows.

    Pure function, tested against a recorded reply shape. Azure
    answers with ``tables``; the first table names its ``columns``
    and holds each result as a ``rows`` list in the same order.
    Values are returned as text — that is all an evidence summary
    needs, and it avoids guessing at a type the reply's column
    types only loosely pin down. A reply with no tables gives
    back no columns and no rows.
    """
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables or not isinstance(tables[0], dict):
        return [], []
    table = tables[0]
    columns: list[str] = []
    raw_columns = table.get("columns")
    if isinstance(raw_columns, list):
        for column in raw_columns:
            if isinstance(column, dict) and "name" in column:
                columns.append(str(column["name"]))
    rows: list[dict[str, str]] = []
    raw_rows = table.get("rows")
    if isinstance(raw_rows, list):
        for raw_row in raw_rows:
            if not isinstance(raw_row, list):
                continue
            row: dict[str, str] = {}
            for idx, column in enumerate(columns):
                value = raw_row[idx] if idx < len(raw_row) else ""
                row[column] = "" if value is None else str(value)
            rows.append(row)
    return columns, rows


class AzureQueryResult(BaseModel):
    query: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, str]] = Field(default_factory=list)
    total_rows_found: int = 0
    truncated: bool = False

    def to_evidence(self) -> Evidence:
        if not self.rows:
            summary = f"No rows matched KQL query: {self.query}"
        else:
            first = self.rows[0]
            shown = ", ".join(f"{k}={v}" for k, v in list(first.items())[:4])
            summary = f"{self.total_rows_found} row(s) matched; first: {shown}"
            if self.truncated:
                summary += " [truncated]"
        return Evidence(
            kind="azure-log",
            query=f"kql: {self.query}",
            summary=_bound(redact_secrets(summary), MAX_EVIDENCE_SUMMARY_CHARS),
        )


class InMemoryAzureBackend:
    """Demo Azure backend: rows held in memory, seeded by tests."""

    def __init__(self, rows: list[dict[str, str]] | None = None) -> None:
        self._rows: list[dict[str, str]] = [dict(r) for r in rows or []]

    def fetch(self, query: str) -> tuple[list[str], list[dict[str, str]]]:
        columns = list(self._rows[0]) if self._rows else []
        return columns, [dict(r) for r in self._rows]


class AzureLogAnalyticsBackend:
    """Azure backend that asks a real Log Analytics workspace.

    The bearer token is passed in by the caller (in real use it
    comes from the caller's own Azure sign-in) and is only ever
    put in the request header. The query endpoint is a read.
    """

    def __init__(
        self,
        workspace_id: str,
        token: str,
        base_url: str = "https://api.loganalytics.azure.com",
        timespan: str = "PT1H",
        timeout_seconds: float = 5.0,
        allowed_hosts: list[str] | None = None,
    ) -> None:
        self._workspace_id = workspace_id
        self._token = token
        self._base_url = validate_backend_url(
            base_url, allowed_hosts=allowed_hosts, what="Azure Monitor address"
        )
        self._timespan = timespan
        self._timeout = timeout_seconds

    def fetch(self, query: str) -> tuple[list[str], list[dict[str, str]]]:
        url = (
            f"{self._base_url}/v1/workspaces/"
            f"{urllib.parse.quote(self._workspace_id)}/query"
        )
        body = json.dumps({"query": query, "timespan": self._timespan}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            return [], []
        return parse_azure_response(payload)


class AzureAdapter:
    """The Azure Monitor KQL read, with bounds and cleaning applied."""

    def __init__(
        self, backend: InMemoryAzureBackend | AzureLogAnalyticsBackend | None = None
    ) -> None:
        self._backend = backend if backend is not None else InMemoryAzureBackend()

    def query(self, request: KqlQueryRequest) -> AzureQueryResult:
        columns, rows = self._backend.fetch(request.query)
        truncated = len(rows) > request.limit_rows
        kept = [
            {k: redact_secrets(v) for k, v in row.items()}
            for row in rows[: request.limit_rows]
        ]
        log.info("azure kql query -> %d row(s)", len(kept))
        return AzureQueryResult(
            query=request.query,
            columns=columns,
            rows=kept,
            total_rows_found=len(rows),
            truncated=truncated,
        )
