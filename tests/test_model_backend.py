"""Phase 9, first item: a language-model backend behind the same
diagnosis report shape, off by default.

The deterministic backend stays the default everywhere; a model
backend exists only where a test (or an operator) builds one and
hands it in. The fake clients here are test doubles: they return
fixed replies, and no real model is called anywhere in this file.
"""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from sre_agent.ingestion import IncidentStore
from sre_agent.model_backend import DeterministicBackend, LanguageModelBackend
from sre_agent.models import Incident, IncidentStatus, Target
from sre_agent.reasoning import DiagnosisOutcome, ReasoningLoop, demo_diagnosis
from sre_agent.server import create_app
from sre_agent.tools import demo_tool_layer

TARGET = Target(environment="demo", namespace="shop", service="checkout")
REPO_ROOT = Path(__file__).resolve().parents[1]


def _incident() -> Incident:
    incident = Incident(title="HighErrorRate on checkout", target=TARGET)
    incident.transition(IncidentStatus.TRIAGING)
    return incident


def _first_evidence_id(prompt: str) -> str:
    match = re.search(r"evidence_id=(\S+)", prompt)
    assert match is not None
    return match.group(1)


class _CitingClient:
    """A fake model that cites the first evidence id in its prompt."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        ev_id = _first_evidence_id(prompt)
        return json.dumps(
            {
                "claims": [
                    {
                        "statement": "The model saw a real problem here",
                        "evidence_ids": [ev_id],
                        "confidence": 0.7,
                    }
                ],
                "hypotheses": [
                    {
                        "statement": "A dependency is failing",
                        "supporting_evidence": [ev_id],
                        "confidence": 0.6,
                    }
                ],
            }
        )


class _FixedClient:
    """A fake model that always returns the same text."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete(self, prompt: str) -> str:
        return self._reply


class _BrokenClient:
    def complete(self, prompt: str) -> str:
        raise RuntimeError("the model is unreachable")


# --- the default stays deterministic ---------------------------------------


def test_default_loop_uses_the_deterministic_backend():
    loop = ReasoningLoop(demo_tool_layer())
    assert loop.backend_name == "deterministic"
    report = demo_diagnosis()
    assert report.backend_name == "deterministic"
    assert "Backend: deterministic" in report.render_text()
    assert report.outcome is DiagnosisOutcome.DIAGNOSED


def test_explicit_deterministic_backend_matches_the_default():
    default = ReasoningLoop(demo_tool_layer()).diagnose(_incident())
    explicit = ReasoningLoop(
        demo_tool_layer(), backend=DeterministicBackend()
    ).diagnose(_incident())
    assert explicit.backend_name == "deterministic"
    assert [c.statement for c in explicit.claims] == [
        c.statement for c in default.claims
    ]
    assert [h.statement for h in explicit.hypotheses] == [
        h.statement for h in default.hypotheses
    ]
    assert explicit.summary == default.summary


# --- a model backend behind the same report shape ---------------------------


def test_model_backend_diagnoses_behind_the_same_report_shape():
    client = _CitingClient()
    loop = ReasoningLoop(
        demo_tool_layer(), backend=LanguageModelBackend(client)
    )
    assert loop.backend_name == "language-model"
    incident = _incident()
    report = loop.diagnose(incident)
    assert report.backend_name == "language-model"
    assert report.outcome is DiagnosisOutcome.DIAGNOSED
    assert incident.status is IncidentStatus.DIAGNOSED
    assert report.claims[0].statement == "The model saw a real problem here"
    assert report.hypotheses[0].statement == "A dependency is failing"
    assert incident.diagnosis == "Most likely: A dependency is failing"
    # Every citation is a real evidence record on the incident.
    known = {ev.id for ev in incident.evidence}
    assert all(set(c.evidence_ids) <= known for c in report.claims)
    assert "Backend: language-model" in report.render_text()


def test_model_prompt_is_built_from_evidence_records_only():
    client = _CitingClient()
    ReasoningLoop(
        demo_tool_layer(), backend=LanguageModelBackend(client)
    ).diagnose(_incident())
    prompt = client.prompts[0]
    assert "evidence_id=" in prompt
    assert "summary=" in prompt
    # The fixed instruction text is the backend's own, and it
    # tells the model to propose no action.
    assert "Propose no action" in prompt


def test_model_reply_citing_unknown_evidence_is_dropped_and_escalates():
    reply = json.dumps(
        {
            "claims": [
                {
                    "statement": "A claim citing evidence that does not exist",
                    "evidence_ids": ["not-a-real-evidence-id"],
                    "confidence": 0.9,
                }
            ],
            "hypotheses": [
                {
                    "statement": "A cause citing the same invented id",
                    "supporting_evidence": ["not-a-real-evidence-id"],
                    "confidence": 0.9,
                }
            ],
        }
    )
    incident = _incident()
    report = ReasoningLoop(
        demo_tool_layer(), backend=LanguageModelBackend(_FixedClient(reply))
    ).diagnose(incident)
    assert report.claims == []
    assert report.hypotheses == []
    assert report.outcome is DiagnosisOutcome.INSUFFICIENT_EVIDENCE
    assert incident.status is IncidentStatus.ESCALATED
    assert incident.diagnosis is None


def test_model_reply_with_an_action_field_is_refused():
    def reply_with(top_extra: dict) -> str:
        return json.dumps(
            {
                "claims": [],
                "hypotheses": [],
                **top_extra,
            }
        )

    for extra in ({"actions": [{"runbook_id": "restart"}]}, {"runbook": "restart"}):
        incident = _incident()
        report = ReasoningLoop(
            demo_tool_layer(),
            backend=LanguageModelBackend(_FixedClient(reply_with(extra))),
        ).diagnose(incident)
        assert report.outcome is DiagnosisOutcome.INSUFFICIENT_EVIDENCE
        assert incident.status is IncidentStatus.ESCALATED


def test_model_entry_carrying_a_runbook_field_is_dropped_not_trusted():
    class _SmugglingClient:
        def complete(self, prompt: str) -> str:
            ev_id = _first_evidence_id(prompt)
            return json.dumps(
                {
                    "claims": [
                        {
                            "statement": "A claim that tries to smuggle a fix",
                            "evidence_ids": [ev_id],
                            "confidence": 0.9,
                            "runbook_id": "restart",
                        }
                    ],
                    "hypotheses": [
                        {
                            "statement": "A cause citing real evidence",
                            "supporting_evidence": [ev_id],
                            "confidence": 0.5,
                        }
                    ],
                }
            )

    incident = _incident()
    report = ReasoningLoop(
        demo_tool_layer(), backend=LanguageModelBackend(_SmugglingClient())
    ).diagnose(incident)
    # The smuggling claim is dropped; the honest cause still stands,
    # and nothing was proposed or run anywhere.
    assert report.claims == []
    assert [h.statement for h in report.hypotheses] == [
        "A cause citing real evidence"
    ]
    assert report.outcome is DiagnosisOutcome.DIAGNOSED


def test_broken_or_non_json_model_replies_escalate_cleanly():
    for backend in (
        LanguageModelBackend(_BrokenClient()),
        LanguageModelBackend(_FixedClient("this is not json")),
        LanguageModelBackend(_FixedClient(json.dumps(["not", "an", "object"]))),
    ):
        incident = _incident()
        report = ReasoningLoop(demo_tool_layer(), backend=backend).diagnose(
            incident
        )
        assert report.claims == []
        assert report.hypotheses == []
        assert report.outcome is DiagnosisOutcome.INSUFFICIENT_EVIDENCE
        assert incident.status is IncidentStatus.ESCALATED


def test_model_entry_with_out_of_range_confidence_is_dropped():
    class _OverconfidentClient:
        def complete(self, prompt: str) -> str:
            ev_id = _first_evidence_id(prompt)
            return json.dumps(
                {
                    "claims": [],
                    "hypotheses": [
                        {
                            "statement": "A cause claimed past certainty",
                            "supporting_evidence": [ev_id],
                            "confidence": 1.5,
                        }
                    ],
                }
            )

    incident = _incident()
    report = ReasoningLoop(
        demo_tool_layer(), backend=LanguageModelBackend(_OverconfidentClient())
    ).diagnose(incident)
    assert report.hypotheses == []
    assert report.outcome is DiagnosisOutcome.INSUFFICIENT_EVIDENCE


# --- the server: off by default, on only when handed in ---------------------


GENERIC_ALERT = {
    "alert_name": "HighErrorRate",
    "service": "checkout",
    "namespace": "shop",
    "environment": "demo",
    "severity": "critical",
    "summary": "Checkout errors are above 10%",
    "fingerprint": "abc123",
}


def test_server_diagnoses_with_a_model_backend_only_when_given_one():
    backend = LanguageModelBackend(_CitingClient())
    client = TestClient(
        create_app(
            IncidentStore(),
            tool_layer=demo_tool_layer(),
            diagnosis_backend=backend,
        )
    )
    created = client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    found = client.get(f"/incidents/{created['incident_id']}").json()
    assert found["status"] == "diagnosed"
    assert found["diagnosis"] == "Most likely: A dependency is failing"


def test_shipped_server_app_stays_deterministic():
    from sre_agent.server import app

    client = TestClient(app)
    created = client.post("/webhooks/generic", json=GENERIC_ALERT).json()
    found = client.get(f"/incidents/{created['incident_id']}").json()
    assert found["status"] == "diagnosed"
    # The deterministic demo diagnosis, word for word — no model
    # wording appears in the shipped server.
    assert "timing out" in found["diagnosis"]


# --- the docs say all of this, in plain words -------------------------------


def test_docs_state_the_model_backend_is_off_by_default():
    operator = (REPO_ROOT / "docs" / "operator-guide.md").read_text(
        encoding="utf-8"
    )
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "off by default" in operator
    assert "deterministic" in operator
    assert "no real model client" in operator
    assert "language-model backend" in readme
    assert "off by default" in readme
