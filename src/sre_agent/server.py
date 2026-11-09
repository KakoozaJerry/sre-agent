"""Webhook server: the agent's front door for alerts.

Run it with:

    uvicorn sre_agent.server:app --port 8080

Endpoints, in plain words:

- ``GET /health`` — is the agent up? Always answers ``{"status": "ok"}``.
- ``POST /webhooks/alertmanager`` — send an Alertmanager payload;
  each alert in it is ingested (see ingestion.py for dedupe/grouping).
- ``POST /webhooks/generic`` — send one simple alert as JSON.
- ``GET /incidents`` — list the incidents the agent knows about.
- ``GET /incidents/{id}`` — one incident, in full.

The server only receives and records. It never runs a fix: acting
comes later in the roadmap, behind the policy engine and a human
approval, exactly as the safety model requires.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .ingestion import (
    AlertmanagerPayload,
    GenericAlertPayload,
    IncidentStore,
    IngestResult,
    normalize_alertmanager,
    normalize_generic,
)
from .models import Incident


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


def create_app(store: IncidentStore | None = None) -> FastAPI:
    """Build the webhook app. Pass a store to share or inspect state."""
    incident_store = store if store is not None else IncidentStore()
    app = FastAPI(title="sre-agent")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/alertmanager")
    def alertmanager_webhook(payload: AlertmanagerPayload) -> dict[str, object]:
        results = [
            incident_store.ingest(alert) for alert in normalize_alertmanager(payload)
        ]
        return {
            "received": len(results),
            "results": [_result_view(r) for r in results],
            "active_incidents": sum(
                1
                for inc in incident_store.list_incidents()
                if inc.status.value != "resolved"
            ),
        }

    @app.post("/webhooks/generic")
    def generic_webhook(payload: GenericAlertPayload) -> dict[str, object]:
        result = incident_store.ingest(normalize_generic(payload))
        return _result_view(result)

    @app.get("/incidents")
    def list_incidents() -> list[dict[str, object]]:
        return [
            _incident_summary(incident_store, inc)
            for inc in incident_store.list_incidents()
        ]

    @app.get("/incidents/{incident_id}")
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
