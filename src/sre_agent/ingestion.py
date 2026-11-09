"""Alert ingestion: turn webhook payloads into incidents.

Two webhook shapes are understood:

- **Alertmanager.** The payload Prometheus Alertmanager sends: a list of
  alerts with labels, annotations, a start time and a fingerprint.
- **Generic.** One simple alert as a small JSON object, for tools that
  do not speak Alertmanager's shape.

What happens to an alert, in plain words:

- **Dedupe.** The same alert (same fingerprint) firing again while its
  incident is still open is a copy. A copy never opens a second
  incident; it is counted on the incident it belongs to.
- **Grouping.** A different alert (a different fingerprint) for the
  same target and the same alert name, while an incident for them is
  still open, belongs to that incident. It is added as evidence.
- **Resolving.** When a "resolved" alert arrives for an open incident,
  the incident is resolved and its group closes, so the next firing
  opens a fresh incident.

Alert text is data, never instructions. Labels and annotations are
stored as evidence only; nothing in them is ever run or obeyed. See
docs/safety-model.md for why that matters.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, Field

from .logging_config import get_logger
from .models import Evidence, Incident, IncidentStatus, Target

log = get_logger(__name__)

FIRING = "firing"
RESOLVED = "resolved"


class IncomingAlert(BaseModel):
    """One alert, normalised into the same shape whatever sent it."""

    fingerprint: str
    alert_name: str
    title: str
    target: Target
    severity: str = "unknown"
    status: str = FIRING
    source: str  # "alertmanager" or "generic"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime | None = None

    @property
    def group_key(self) -> tuple[str, str, str, str]:
        """Alerts that share a target and a name belong to one incident."""
        return (
            self.target.environment,
            self.target.namespace,
            self.target.service,
            self.alert_name,
        )


class AlertmanagerAlert(BaseModel):
    """One alert inside an Alertmanager payload."""

    model_config = {"populate_by_name": True}

    status: str = FIRING
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime | None = Field(default=None, alias="startsAt")
    fingerprint: str = ""


class AlertmanagerPayload(BaseModel):
    """The webhook body Alertmanager sends."""

    model_config = {"populate_by_name": True}

    status: str = FIRING
    common_labels: dict[str, str] = Field(default_factory=dict, alias="commonLabels")
    common_annotations: dict[str, str] = Field(
        default_factory=dict, alias="commonAnnotations"
    )
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


class GenericAlertPayload(BaseModel):
    """The simple webhook body: one alert, plain fields."""

    alert_name: str
    service: str
    namespace: str = "default"
    environment: str = "demo"
    severity: str = "unknown"
    status: str = FIRING
    fingerprint: str = ""
    summary: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime | None = None


def fingerprint_for_labels(labels: dict[str, str]) -> str:
    """Make a stable fingerprint from labels when the sender gave none.

    The same labels always make the same fingerprint, so a repeated
    alert is recognised as a copy.
    """
    canonical = json.dumps(labels, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _title_for(alert_name: str, service: str, annotations: dict[str, str]) -> str:
    return (
        annotations.get("summary")
        or annotations.get("description")
        or f"{alert_name} on {service}"
    )


def normalize_alertmanager(
    payload: AlertmanagerPayload, default_environment: str = "demo"
) -> list[IncomingAlert]:
    """Turn an Alertmanager payload into a list of incoming alerts."""
    incoming: list[IncomingAlert] = []
    for alert in payload.alerts:
        labels = {**payload.common_labels, **alert.labels}
        annotations = {**payload.common_annotations, **alert.annotations}
        alert_name = labels.get("alertname", "unknown")
        service = labels.get("service") or labels.get("job") or "unknown"
        target = Target(
            environment=labels.get("environment", default_environment),
            namespace=labels.get("namespace", "default"),
            service=service,
        )
        incoming.append(
            IncomingAlert(
                fingerprint=alert.fingerprint or fingerprint_for_labels(labels),
                alert_name=alert_name,
                title=_title_for(alert_name, service, annotations),
                target=target,
                severity=labels.get("severity", "unknown"),
                status=alert.status or payload.status,
                source="alertmanager",
                labels=labels,
                annotations=annotations,
                starts_at=alert.starts_at,
            )
        )
    return incoming


def normalize_generic(payload: GenericAlertPayload) -> IncomingAlert:
    """Turn a generic webhook body into one incoming alert."""
    labels = {
        "alertname": payload.alert_name,
        "service": payload.service,
        "namespace": payload.namespace,
        "environment": payload.environment,
        "severity": payload.severity,
        **payload.labels,
    }
    annotations = dict(payload.annotations)
    if payload.summary and "summary" not in annotations:
        annotations["summary"] = payload.summary
    return IncomingAlert(
        fingerprint=payload.fingerprint or fingerprint_for_labels(labels),
        alert_name=payload.alert_name,
        title=_title_for(payload.alert_name, payload.service, annotations),
        target=Target(
            environment=payload.environment,
            namespace=payload.namespace,
            service=payload.service,
        ),
        severity=payload.severity,
        status=payload.status,
        source="generic",
        labels=labels,
        annotations=annotations,
        starts_at=payload.starts_at,
    )


class IngestResult(BaseModel):
    """What ingestion did with one alert."""

    incident: Incident | None = None
    created: bool = False
    duplicate: bool = False
    grouped: bool = False
    resolved: bool = False


class IncidentStore:
    """Keeps incidents in memory and decides what each new alert means.

    This is the small store interface the plan talks about: webhooks
    and (later) tools talk to this class, so swapping the memory here
    for SQLite later does not change the webhook shapes.
    """

    def __init__(self) -> None:
        self._incidents: dict[str, Incident] = {}
        self._active_by_group: dict[tuple[str, str, str, str], str] = {}
        self._incident_by_fingerprint: dict[str, str] = {}
        self._duplicate_counts: dict[str, int] = {}

    def list_incidents(self) -> list[Incident]:
        return list(self._incidents.values())

    def get(self, incident_id: str) -> Incident | None:
        return self._incidents.get(incident_id)

    def duplicate_count(self, incident_id: str) -> int:
        return self._duplicate_counts.get(incident_id, 0)

    def ingest(self, alert: IncomingAlert) -> IngestResult:
        if alert.status == RESOLVED:
            return self._resolve(alert)
        return self._fire(alert)

    def _fire(self, alert: IncomingAlert) -> IngestResult:
        known_id = self._incident_by_fingerprint.get(alert.fingerprint)
        if known_id is not None and known_id in self._incidents:
            incident = self._incidents[known_id]
            if incident.status is not IncidentStatus.RESOLVED:
                self._duplicate_counts[incident.id] = (
                    self._duplicate_counts.get(incident.id, 0) + 1
                )
                log.info(
                    "duplicate alert counted for incident %s (fingerprint %s)",
                    incident.id,
                    alert.fingerprint,
                )
                return IngestResult(incident=incident, duplicate=True)

        grouped_id = self._active_by_group.get(alert.group_key)
        if grouped_id is not None:
            incident = self._incidents[grouped_id]
            incident.add_evidence(self._evidence_for(alert))
            self._incident_by_fingerprint[alert.fingerprint] = incident.id
            log.info("alert grouped into incident %s", incident.id)
            return IngestResult(incident=incident, grouped=True)

        incident = Incident(title=alert.title, target=alert.target)
        # Ingestion hands the incident straight to triage: the agent
        # has started looking at it. This also makes a later
        # "resolved" alert a legal closing move.
        incident.transition(IncidentStatus.TRIAGING)
        incident.add_evidence(self._evidence_for(alert))
        self._incidents[incident.id] = incident
        self._active_by_group[alert.group_key] = incident.id
        self._incident_by_fingerprint[alert.fingerprint] = incident.id
        log.info("incident %s opened from %s alert", incident.id, alert.source)
        return IngestResult(incident=incident, created=True)

    def _resolve(self, alert: IncomingAlert) -> IngestResult:
        incident_id = self._active_by_group.get(alert.group_key)
        if incident_id is None:
            incident_id = self._incident_by_fingerprint.get(alert.fingerprint)
            if incident_id is not None:
                candidate = self._incidents.get(incident_id)
                if candidate is None or candidate.status is IncidentStatus.RESOLVED:
                    incident_id = None
        if incident_id is None:
            log.info("resolved alert with no open incident; nothing to close")
            return IngestResult()

        incident = self._incidents[incident_id]
        if incident.status is IncidentStatus.OPEN:
            incident.transition(IncidentStatus.TRIAGING)
        incident.transition(IncidentStatus.RESOLVED)
        # Close the group: the next firing opens a fresh incident.
        self._active_by_group.pop(alert.group_key, None)
        stale = [
            fp for fp, iid in self._incident_by_fingerprint.items() if iid == incident.id
        ]
        for fp in stale:
            del self._incident_by_fingerprint[fp]
        log.info("incident %s resolved by alert", incident.id)
        return IngestResult(incident=incident, resolved=True)

    @staticmethod
    def _evidence_for(alert: IncomingAlert) -> Evidence:
        return Evidence(
            kind="alert",
            query=f"webhook:{alert.source} fingerprint:{alert.fingerprint}",
            summary=f"{alert.alert_name} ({alert.severity}): {alert.title}",
        )
