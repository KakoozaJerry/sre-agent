import pytest

from sre_agent.models import Evidence, Incident, IncidentStatus, Target


def _incident() -> Incident:
    return Incident(
        title="High error rate on checkout",
        target=Target(environment="demo", namespace="shop", service="checkout"),
    )


def test_incident_starts_open():
    assert _incident().status is IncidentStatus.OPEN


def test_legal_transition_path():
    inc = _incident()
    inc.transition(IncidentStatus.TRIAGING)
    inc.transition(IncidentStatus.DIAGNOSED)
    inc.transition(IncidentStatus.MITIGATING)
    inc.transition(IncidentStatus.RESOLVED)
    assert inc.status is IncidentStatus.RESOLVED


def test_illegal_transition_raises():
    inc = _incident()
    with pytest.raises(ValueError):
        inc.transition(IncidentStatus.RESOLVED)


def test_failed_fix_returns_to_triaging():
    inc = _incident()
    inc.transition(IncidentStatus.TRIAGING)
    inc.transition(IncidentStatus.DIAGNOSED)
    inc.transition(IncidentStatus.MITIGATING)
    inc.transition(IncidentStatus.TRIAGING)
    assert inc.status is IncidentStatus.TRIAGING


def test_target_is_immutable():
    target = Target(environment="demo", namespace="shop", service="checkout")
    with pytest.raises(Exception):
        target.service = "payments"  # type: ignore[misc]


def test_evidence_is_recorded():
    inc = _incident()
    inc.add_evidence(
        Evidence(kind="metric", query="rate(http_errors[5m])", summary="error rate 12%")
    )
    assert len(inc.evidence) == 1
    assert inc.evidence[0].query.startswith("rate(")
