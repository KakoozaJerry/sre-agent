"""Phase 9, second item: model-proposed actions go through the
same policy engine, approval tiers and audit log as any other
proposal — and the model can propose, never approve.

The fake clients here are test doubles returning fixed replies;
no real model is called anywhere in this file.
"""

import json
from pathlib import Path

from sre_agent.approvals import approve_pending, execute_approved
from sre_agent.audit import AuditLog, evaluate_and_audit
from sre_agent.model_proposals import (
    LanguageModelProposer,
    submit_model_proposal,
)
from sre_agent.models import Incident, IncidentStatus, Target
from sre_agent.policy import Decision, EnvironmentPolicy, Tier
from sre_agent.runbooks import build_action, demo_runbooks
from sre_agent.tier3 import demo_tier3_policy

TARGET = Target(environment="demo", namespace="shop", service="checkout")
REPO_ROOT = Path(__file__).resolve().parents[1]

TIER2 = EnvironmentPolicy(
    environment="demo",
    granted_tier=Tier.ACT_WITH_APPROVAL,
    max_replicas_affected=5,
)


def _incident() -> Incident:
    incident = Incident(title="HighErrorRate on checkout", target=TARGET)
    incident.transition(IncidentStatus.TRIAGING)
    incident.diagnosis = "Most likely: a dependency is timing out"
    incident.transition(IncidentStatus.DIAGNOSED)
    return incident


class _FixedClient:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._reply


class _BrokenClient:
    def complete(self, prompt: str) -> str:
        raise RuntimeError("the model is unreachable")


def _reply(**fields: object) -> str:
    base: dict[str, object] = {
        "runbook_id": "restart-workload",
        "parameters": {},
        "rationale": "the workload is failing",
    }
    base.update(fields)
    return json.dumps(base)


def _submit(client, policy=TIER2):
    executor, backend = demo_runbooks()
    audit = AuditLog()
    incident = _incident()
    outcome = submit_model_proposal(
        LanguageModelProposer(client),
        incident,
        runbook_backend=backend,
        executor=executor,
        policy=policy,
        audit_log=audit,
    )
    return outcome, backend, audit, incident


# --- Tier 2: the same decision, the same audit, nothing runs --------------


def test_model_proposal_at_tier2_waits_for_a_human_and_runs_nothing():
    outcome, backend, audit, _ = _submit(_FixedClient(_reply()))
    assert outcome.result is not None
    assert outcome.result.decision is Decision.REQUIRE_APPROVAL
    assert outcome.pending is not None
    assert outcome.run_result is None
    state = backend.get(TARGET)
    assert state is not None and state.restarts == 0 and state.failing is True
    records = audit.records
    assert len(records) == 1 and records[0].decision == "require_approval"
    assert records[0].approved_by is None


def test_model_proposal_is_decided_and_audited_identically_to_a_hand_built_one():
    outcome, _, model_audit, incident = _submit(_FixedClient(_reply()))
    # The same action, proposed the ordinary way, in a fresh log.
    _, backend = demo_runbooks()
    hand_action = build_action(
        "restart-workload",
        TARGET,
        {},
        backend,
        rationale="Proposed by language model: the workload is failing",
    )
    hand_audit = AuditLog()
    hand_result = evaluate_and_audit(
        hand_action, TIER2, hand_audit,
        incident_id=incident.id, incident_target=TARGET,
    )
    assert outcome.result is not None and outcome.action is not None
    assert outcome.result.decision is hand_result.decision
    assert outcome.result.reason == hand_result.reason
    assert outcome.action.replicas_affected == hand_action.replicas_affected
    model_record, hand_record = model_audit.records[0], hand_audit.records[0]
    assert model_record.action_summary == hand_record.action_summary
    assert model_record.decision == hand_record.decision
    assert model_record.reason == hand_record.reason
    assert model_record.parameters_hash == hand_record.parameters_hash


def test_a_named_human_can_approve_a_model_proposal_exactly_once():
    outcome, backend, audit, _incident_unused = _submit(_FixedClient(_reply()))
    assert outcome.pending is not None
    # An executor on the same backend the proposal was built against.
    from sre_agent.runbooks import RunbookExecutor

    executor = RunbookExecutor(backend)
    grant, allowed = approve_pending(
        outcome.pending,
        approved_by="Abraham",
        policy=TIER2,
        audit_log=audit,
        incident_target=TARGET,
    )
    assert grant.approved_by == "Abraham"
    run = execute_approved(executor, outcome.pending, grant, allowed)
    assert "Restarted checkout" in run.summary
    state = backend.get(TARGET)
    assert state is not None and state.restarts == 1
    # The grant is spent: approving the model changed nothing about
    # single use — a second run under the same yes is refused.
    try:
        execute_approved(executor, outcome.pending, grant, allowed)
        raise AssertionError("a spent grant must be refused")
    except ValueError:
        pass


def test_the_proposer_has_no_way_to_approve():
    proposer = LanguageModelProposer(_FixedClient(_reply()))
    assert not hasattr(proposer, "approve")
    assert not hasattr(proposer, "approve_pending")
    outcome, _, _, _ = _submit(_FixedClient(_reply()))
    # A submission can hold a pending action, never a grant.
    assert outcome.pending is not None
    assert not hasattr(outcome, "grant")


# --- Tier 3 and denial: the tiers apply identically --------------------------


def test_model_proposal_at_tier3_runs_on_the_policys_own_allow():
    outcome, backend, audit, _ = _submit(
        _FixedClient(_reply()), policy=demo_tier3_policy()
    )
    assert outcome.result is not None
    assert outcome.result.decision is Decision.ALLOW
    assert outcome.pending is None
    assert outcome.run_result is not None
    state = backend.get(TARGET)
    assert state is not None and state.restarts == 1
    assert audit.records[0].decision == "allow"


def test_model_proposal_over_the_blast_radius_limit_is_denied_and_audited():
    tight = EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITH_APPROVAL,
        max_replicas_affected=2,
    )
    # Checkout runs 2 copies; scaling to 5 touches 3 — over the limit.
    outcome, backend, audit, _ = _submit(
        _FixedClient(
            _reply(runbook_id="scale-replicas", parameters={"replicas": 5})
        ),
        policy=tight,
    )
    assert outcome.result is not None
    assert outcome.result.decision is Decision.DENY
    assert outcome.pending is None and outcome.run_result is None
    state = backend.get(TARGET)
    assert state is not None and state.replicas == 2
    assert audit.records[0].decision == "deny"


def test_model_proposal_while_halted_is_denied_and_audited():
    halted = EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITH_APPROVAL,
        max_replicas_affected=5,
        halted=True,
    )
    outcome, backend, audit, _ = _submit(_FixedClient(_reply()), policy=halted)
    assert outcome.result is not None
    assert outcome.result.decision is Decision.DENY
    assert outcome.run_result is None
    state = backend.get(TARGET)
    assert state is not None and state.restarts == 0
    assert audit.records[0].decision == "deny"


# --- the reply checks: a model cannot retarget, approve, or smuggle --------


def test_reply_carrying_a_target_or_an_approver_is_refused_whole():
    for extra in (
        {"target": {"environment": "demo", "namespace": "shop",
                    "service": "search"}},
        {"approved_by": "language-model"},
        {"decision": "allow"},
    ):
        outcome, backend, audit, _ = _submit(_FixedClient(_reply(**extra)))
        assert outcome.action is None and outcome.result is None
        assert outcome.pending is None and outcome.run_result is None
        assert audit.records == []
        state = backend.get(TARGET)
        assert state is not None and state.restarts == 0


def test_model_action_target_is_always_the_incidents_pinned_target():
    outcome, _, _, incident = _submit(_FixedClient(_reply()))
    assert outcome.action is not None
    assert outcome.action.target == incident.target == TARGET
    assert outcome.action.rationale.startswith("Proposed by language model:")


def test_unbuildable_or_broken_replies_propose_nothing_and_audit_nothing():
    for client in (
        _FixedClient(_reply(runbook_id="delete-everything")),
        _FixedClient(
            _reply(runbook_id="scale-replicas", parameters={"replicas": 99})
        ),
        _FixedClient(_reply(parameters={"command": "rm -rf /"})),
        _FixedClient("this is not json"),
        _BrokenClient(),
    ):
        outcome, backend, audit, _ = _submit(client)
        assert outcome.action is None and outcome.result is None
        assert outcome.pending is None and outcome.run_result is None
        assert audit.records == []
        state = backend.get(TARGET)
        assert state is not None and state.restarts == 0


def test_proposal_prompt_lists_only_allow_listed_runbooks_and_no_approval():
    client = _FixedClient(_reply())
    _submit(client)
    prompt = client.prompts[0]
    assert "restart-workload" in prompt
    assert "scale-replicas" in prompt
    assert "rollback-deployment" in prompt
    assert "You cannot approve" in prompt


# --- the docs say all of this, in plain words -------------------------------


def test_docs_state_model_proposals_use_the_same_path_and_never_approve():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    operator = (REPO_ROOT / "docs" / "operator-guide.md").read_text(
        encoding="utf-8"
    )
    safety = (REPO_ROOT / "docs" / "safety-model.md").read_text(
        encoding="utf-8"
    )
    for text in (readme, operator, safety):
        assert "model" in text.lower()
        assert "never approve" in text
    assert "model_proposals" in operator
    assert "model-proposed" in safety.lower()
