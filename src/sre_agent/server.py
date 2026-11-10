"""Webhook server: the agent's front door for alerts.

Run it with:

    uvicorn sre_agent.server:app --port 8080

Endpoints, in plain words:

- ``GET /health`` — is the agent up? Always answers ``{"status": "ok"}``.
  This one stays open even when a token is set, so a health check
  never needs the secret.
- ``POST /webhooks/alertmanager`` — send an Alertmanager payload;
  each alert in it is ingested (see ingestion.py for dedupe/grouping).
- ``POST /webhooks/generic`` — send one simple alert as JSON.
- ``GET /incidents`` — list the incidents the agent knows about.
- ``GET /incidents/{id}`` — one incident, in full.

Authentication (security finding F-01, fixed after v0.1.0): set the
``SRE_AGENT_API_TOKEN`` environment variable (or pass ``auth_token``
to :func:`create_app`) and every endpoint except ``/health`` then
requires that exact token in the ``X-SRE-Agent-Token`` header —
webhooks and incident reads alike. A missing or wrong token gets a
401 and nothing is ingested or shown. With no token set, the server
is unauthenticated, exactly as v0.1.0 was: that stays fine for one
operator on their own machine, and only for that. Before the server
is shared with anyone else, set a token. Alertmanager can send the
header too (its webhook receiver's ``http_config``), and the CLI
sends it automatically when the same environment variable is set.

The server only receives and records. It never runs a fix: acting
comes later in the roadmap, behind the policy engine and a human
approval, exactly as the safety model requires.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException

from .ingestion import (
    AlertmanagerPayload,
    GenericAlertPayload,
    IncidentStore,
    IngestionLimitError,
    IngestResult,
    normalize_alertmanager,
    normalize_generic,
)
from .models import Incident

ENV_API_TOKEN = "SRE_AGENT_API_TOKEN"
TOKEN_HEADER = "X-SRE-Agent-Token"


def _result_view(result: IngestResult) -> dict[str, object]:
    incident = result.incident
    return {
        "incident_id": incident.id if incident else None,
        "created": result.created,
        "duplicate": result.duplicate,
        "grouped": result.grouped,
        "resolved": result.resolved,
    }


def _incident_summary(store: IncidentStore, incident: Incident) -> dict[str, object]:
    return {
        "id": incident.id,
        "title": incident.title,
        "status": incident.status.value,
        "target": incident.target.model_dump(),
        "evidence_count": len(incident.evidence),
        "duplicate_count": store.duplicate_count(incident.id),
    }


def create_app(
    store: IncidentStore | None = None, auth_token: str | None = None
) -> FastAPI:
    """Build the webhook app. Pass a store to share or inspect state.

    ``auth_token`` wins when given; otherwise the token is read from
    the ``SRE_AGENT_API_TOKEN`` environment variable. An empty value
    counts as no token.
    """
    incident_store = store if store is not None else IncidentStore()
    token = auth_token
    if token is None:
        token = os.environ.get(ENV_API_TOKEN) or None
    if token == "":
        token = None
    app = FastAPI(title="sre-agent")

    def require_token(
        x_sre_agent_token: str | None = Header(default=None, alias=TOKEN_HEADER),
    ) -> None:
        if token is None:
            return
        if x_sre_agent_token is None or not secrets.compare_digest(
            x_sre_agent_token, token
        ):
            raise HTTPException(
                status_code=401, detail="missing or wrong API token"
            )

    guarded = [Depends(require_token)]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/alertmanager", dependencies=guarded)
    def alertmanager_webhook(payload: AlertmanagerPayload) -> dict[str, object]:
        try:
            results = [
                incident_store.ingest(alert)
                for alert in normalize_alertmanager(payload)
            ]
        except IngestionLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return {
            "received": len(results),
            "results": [_result_view(r) for r in results],
            "active_incidents": sum(
                1
                for inc in incident_store.list_incidents()
                if inc.status.value != "resolved"
            ),
        }

    @app.post("/webhooks/generic", dependencies=guarded)
    def generic_webhook(payload: GenericAlertPayload) -> dict[str, object]:
        try:
            result = incident_store.ingest(normalize_generic(payload))
        except IngestionLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return _result_view(result)

    @app.get("/incidents", dependencies=guarded)
    def list_incidents() -> list[dict[str, object]]:
        return [
            _incident_summary(incident_store, inc)
            for inc in incident_store.list_incidents()
        ]

    @app.get("/incidents/{incident_id}", dependencies=guarded)
    def get_incident(incident_id: str) -> dict[str, object]:
        incident = incident_store.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return {
            **incident.model_dump(mode="json"),
            "duplicate_count": incident_store.duplicate_count(incident.id),
        }

    return app


app = create_app()
