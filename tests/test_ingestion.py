"""Ingestion: alerts become incidents, copies do not, groups share."""

from sre_agent.ingestion import (
    AlertmanagerPayload,
    GenericAlertPayload,
    IncidentStore,
    fingerprint_for_labels,
    normalize_alertmanager,
    normalize_generic,
)
from sre_agent.models import IncidentStatus


def _alertmanager_payload(**overrides) -> AlertmanagerPayload:
    body: dict = {
        "status": "firing",
        "commonLabels": {"environment": "demo", "namespace": "shop"},
        "commonAnnotations": {},
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighErrorRate",
                    "service": "checkout",
                    "severity": "critical",
                },
                "annotations": {"summary": "Checkout errors are above 10%"},
                "startsAt": "2026-11-08T20:00:00Z",
                "fingerprint": "abc123",
            }
        ],
    }
    body.update(overrides)
    return AlertmanagerPayload(**body)


def _generic(**overrides) -> GenericAlertPayload:
    body: dict = {
        "alert_name": "HighErrorRate",
        "service": "checkout",
        "namespace": "shop",
        "environment": "demo",
        "severity": "critical",
        "summary": "Checkout errors are above 10%",
        "fingerprint": "abc123",
    }
    body.update(overrides)
    return GenericAlertPayload(**body)


def test_alertmanager_payload_normalises():
    alerts = normalize_alertmanager(_alertmanager_payload())
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.alert_name == "HighErrorRate"
    assert alert.target.service == "checkout"
    assert alert.target.namespace == "shop"  # from commonLabels
    assert alert.target.environment == "demo"
    assert alert.severity == "critical"
    assert alert.title == "Checkout errors are above 10%"
    assert alert.fingerprint == "abc123"
    assert alert.source == "alertmanager"


def test_missing_fingerprint_is_made_from_labels_and_is_stable():
    labels = {"alertname": "HighErrorRate", "service": "checkout"}
    assert fingerprint_for_labels(labels) == fingerprint_for_labels(dict(labels))
    changed = {**labels, "service": "payments"}
    assert fingerprint_for_labels(labels) != fingerprint_for_labels(changed)


def test_first_firing_alert_opens_incident_in_triaging():
    store = IncidentStore()
    result = store.ingest(normalize_generic(_generic()))
    assert result.created is True
    assert result.incident is not None
    assert result.incident.status is IncidentStatus.TRIAGING
    assert result.incident.title == "Checkout errors are above 10%"
    assert len(result.incident.evidence) == 1
    assert result.incident.evidence[0].kind == "alert"


def test_same_alert_again_is_a_duplicate_not_a_new_incident():
    store = IncidentStore()
    first = store.ingest(normalize_generic(_generic()))
    second = store.ingest(normalize_generic(_generic()))
    assert first.incident is not None and second.incident is not None
    assert second.duplicate is True
    assert second.created is False
    assert second.incident.id == first.incident.id
    assert len(store.list_incidents()) == 1
    assert store.duplicate_count(first.incident.id) == 1


def test_different_alert_same_target_and_name_is_grouped():
    store = IncidentStore()
    first = store.ingest(normalize_generic(_generic()))
    other = store.ingest(normalize_generic(_generic(fingerprint="def456")))
    assert first.incident is not None and other.incident is not None
    assert other.grouped is True
    assert other.incident.id == first.incident.id
    assert len(store.list_incidents()) == 1
    # The grouped alert is kept as evidence on the shared incident.
    assert len(first.incident.evidence) == 2


def test_different_service_opens_a_separate_incident():
    store = IncidentStore()
    store.ingest(normalize_generic(_generic()))
    other = store.ingest(
        normalize_generic(_generic(service="payments", fingerprint="zzz999"))
    )
    assert other.created is True
    assert len(store.list_incidents()) == 2


def test_resolved_alert_resolves_the_incident_and_closes_the_group():
    store = IncidentStore()
    opened = store.ingest(normalize_generic(_generic()))
    closed = store.ingest(normalize_generic(_generic(status="resolved")))
    assert opened.incident is not None and closed.incident is not None
    assert closed.resolved is True
    assert closed.incident.id == opened.incident.id
    assert closed.incident.status is IncidentStatus.RESOLVED
    # After the group closes, a new firing opens a fresh incident.
    reopened = store.ingest(normalize_generic(_generic()))
    assert reopened.created is True
    assert reopened.incident is not None
    assert reopened.incident.id != opened.incident.id


def test_resolved_alert_with_no_open_incident_is_a_no_op():
    store = IncidentStore()
    result = store.ingest(normalize_generic(_generic(status="resolved")))
    assert result.incident is None
    assert store.list_incidents() == []


def test_alertmanager_end_to_end_opens_one_incident():
    store = IncidentStore()
    results = [
        store.ingest(a) for a in normalize_alertmanager(_alertmanager_payload())
    ]
    assert results[0].created is True
    assert len(store.list_incidents()) == 1
