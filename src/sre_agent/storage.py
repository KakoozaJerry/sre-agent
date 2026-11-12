"""Database store: incidents, diagnoses and approvals that last.

In plain words, this is the Phase 7 database item. Until now every
incident lived in the server process's memory, so stopping the
server lost them all — diagnoses, evidence, and any approval still
waiting for a human included.

``SQLiteIncidentStore`` keeps exactly the same interface as the
in-memory ``IncidentStore`` in ``ingestion.py`` — same ``ingest``,
``get``, ``list_incidents`` and ``duplicate_count``, same dedupe,
grouping and intake caps, because all of that logic is inherited,
not rewritten — and writes every change through to one SQLite
file, using only Python's standard library (``sqlite3``), so no
new dependency was added. A server built with this store instead
of the in-memory one keeps working after a restart:

- **Incidents persist**, with everything on the record: status,
  evidence, hypotheses, the diagnosis, and the memory report.
  A diagnosis is a field on the incident, so it persists with it;
  the server calls the store's ``save`` after a diagnosis and
  after a run, which is what writes those later changes through.
- **The dedupe and grouping indexes persist too** — which alert
  fingerprints belong to which incident, which group is still
  open, and how many duplicates each incident has counted — so
  after a restart a repeated alert is still a duplicate, a
  related alert still groups, and a resolved alert still closes
  its incident.
- **Approvals persist.** The server asks the store to keep each
  pending approval when it is created and again when it is
  decided, and asks for them back when it starts
  (``save_approval`` / ``load_approvals``). A pending approval
  restored after a restart is the same pending action, with the
  same 15-minute expiry measured from when it was proposed —
  a restart does not give a stale proposal new life, and a
  decided approval stays decided, so it cannot be run twice
  across a restart either.

How it is stored, plainly: each incident is one row holding its
full record as JSON (the same shape the API returns), the two
indexes and the duplicate counts are small rows of their own,
and each approval is one row holding the pending action as JSON
plus its decision (blank while it still waits). Every write
replaces those small tables from the in-memory state in one
transaction — the store is capped at 1,000 incidents, so this
stays small and simple, and a crash mid-write cannot leave half
an update behind.

Honest scope: this is a single-operator, single-file store, the
SQLite-first step the roadmap named — not a multi-writer
database, and nothing here claims Postgres or concurrent servers
sharing one file. The shipped ``app`` still uses the in-memory
store, so the demo behaves exactly as before; a deployment that
wants persistence builds its app with this store, in code:

    store = SQLiteIncidentStore("sre-agent.db")
    app = create_app(store=store, tool_layer=..., ...)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Self

from .approvals import PendingAction
from .ingestion import (
    MAX_EVIDENCE_PER_INCIDENT,
    MAX_INCIDENTS,
    IncidentStore,
    IncomingAlert,
    IngestResult,
)
from .logging_config import get_logger
from .models import Incident

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS store_index (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS duplicate_counts (
    incident_id TEXT PRIMARY KEY,
    count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    pending_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    data TEXT NOT NULL,
    decision TEXT
);
"""


class SQLiteIncidentStore(IncidentStore):
    """An ``IncidentStore`` that writes through to one SQLite file.

    The in-memory logic is inherited unchanged; this class loads
    the file's state at start-up and re-writes it after every
    change (ingestion, a ``save`` from the server, an approval
    being created or decided).
    """

    def __init__(
        self,
        path: str | Path,
        max_incidents: int = MAX_INCIDENTS,
        max_evidence_per_incident: int = MAX_EVIDENCE_PER_INCIDENT,
    ) -> None:
        super().__init__(
            max_incidents=max_incidents,
            max_evidence_per_incident=max_evidence_per_incident,
        )
        self._path = Path(path)
        if self._path.parent != Path(""):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._load()

    # -- lifecycle -------------------------------------------------

    def close(self) -> None:
        """Close the database file. The data stays in the file."""
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- loading ---------------------------------------------------

    def _load(self) -> None:
        for incident_id, data in self._conn.execute(
            "SELECT id, data FROM incidents"
        ):
            try:
                incident = Incident.model_validate_json(data)
            except ValueError:
                log.exception(
                    "stored incident %s could not be read back; skipped",
                    incident_id,
                )
                continue
            self._incidents[incident.id] = incident
        for kind, key, incident_id in self._conn.execute(
            "SELECT kind, key, incident_id FROM store_index"
        ):
            if kind == "fingerprint":
                self._incident_by_fingerprint[key] = incident_id
            elif kind == "group":
                group_key = tuple(json.loads(key))
                self._active_by_group[group_key] = incident_id  # type: ignore[assignment]
        for incident_id, count in self._conn.execute(
            "SELECT incident_id, count FROM duplicate_counts"
        ):
            self._duplicate_counts[incident_id] = int(count)
        if self._incidents:
            log.info(
                "loaded %d incident(s) from %s",
                len(self._incidents),
                self._path,
            )

    # -- writing ---------------------------------------------------

    def _sync(self) -> None:
        """Write the incidents, indexes and duplicate counts out.

        One transaction, replacing the three tables from the
        in-memory state, so the file always holds a whole,
        consistent picture — never half an ingestion.
        """
        with self._conn:
            self._conn.execute("DELETE FROM incidents")
            self._conn.executemany(
                "INSERT INTO incidents (id, data) VALUES (?, ?)",
                [
                    (incident.id, incident.model_dump_json())
                    for incident in self._incidents.values()
                ],
            )
            self._conn.execute("DELETE FROM store_index")
            rows: list[tuple[str, str, str]] = [
                ("fingerprint", fingerprint, incident_id)
                for fingerprint, incident_id in (
                    self._incident_by_fingerprint.items()
                )
            ]
            rows += [
                ("group", json.dumps(list(group_key)), incident_id)
                for group_key, incident_id in self._active_by_group.items()
            ]
            self._conn.executemany(
                "INSERT INTO store_index (kind, key, incident_id) "
                "VALUES (?, ?, ?)",
                rows,
            )
            self._conn.execute("DELETE FROM duplicate_counts")
            self._conn.executemany(
                "INSERT INTO duplicate_counts (incident_id, count) "
                "VALUES (?, ?)",
                list(self._duplicate_counts.items()),
            )

    # -- the store interface, with write-through -------------------

    def ingest(self, alert: IncomingAlert) -> IngestResult:
        result = super().ingest(alert)
        self._sync()
        return result

    def save(self, incident: Incident) -> None:
        super().save(incident)
        self._sync()

    # -- approvals ---------------------------------------------------

    def save_approval(
        self, pending: PendingAction, decision: str | None
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO approvals (pending_id, incident_id, data, decision) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(pending_id) DO UPDATE SET "
                "data = excluded.data, decision = excluded.decision",
                (
                    pending.id,
                    pending.incident_id,
                    pending.model_dump_json(),
                    decision,
                ),
            )

    def load_approvals(
        self,
    ) -> tuple[dict[str, PendingAction], dict[str, str]]:
        pendings: dict[str, PendingAction] = {}
        decisions: dict[str, str] = {}
        for pending_id, data, decision in self._conn.execute(
            "SELECT pending_id, data, decision FROM approvals"
        ):
            try:
                pending = PendingAction.model_validate_json(data)
            except ValueError:
                log.exception(
                    "stored approval %s could not be read back; skipped",
                    pending_id,
                )
                continue
            pendings[pending.id] = pending
            if decision:
                decisions[pending.id] = decision
        return pendings, decisions
