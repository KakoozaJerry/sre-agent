"""The safety contract, as tests. These must pass before anything merges."""

from sre_agent.models import Target
from sre_agent.policy import (
    Decision,
    EnvironmentPolicy,
    ProposedAction,
    Tier,
    evaluate,
)

TARGET = Target(environment="demo", namespace="shop", service="checkout")


def _action(**overrides) -> ProposedAction:
    base: dict = {
        "runbook_id": "restart-workload",
        "target": TARGET,
        "parameters": {"deployment": "checkout"},
        "replicas_affected": 1,
        "rationale": "pods are crash-looping after the last deploy",
    }
    base.update(overrides)
    return ProposedAction(**base)


def test_observe_only_environment_denies_mutation():
    policy = EnvironmentPolicy(environment="demo", granted_tier=Tier.OBSERVE)
    assert evaluate(_action(), policy).decision is Decision.DENY


def test_advise_tier_denies_mutation():
    policy = EnvironmentPolicy(environment="demo", granted_tier=Tier.ADVISE)
    assert evaluate(_action(), policy).decision is Decision.DENY


def test_tier2_requires_human_approval():
    policy = EnvironmentPolicy(environment="demo", granted_tier=Tier.ACT_WITH_APPROVAL)
    assert evaluate(_action(), policy).decision is Decision.REQUIRE_APPROVAL


def test_tier3_allows_only_preapproved_runbooks():
    policy = EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITHIN_BOUNDS,
        tier3_runbooks={"restart-workload"},
    )
    assert evaluate(_action(), policy).decision is Decision.ALLOW
    other = _action(runbook_id="rollback-deployment")
    assert evaluate(other, policy).decision is Decision.REQUIRE_APPROVAL


def test_halt_denies_everything_even_at_tier3():
    policy = EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITHIN_BOUNDS,
        tier3_runbooks={"restart-workload"},
        halted=True,
    )
    assert evaluate(_action(), policy).decision is Decision.DENY


def test_no_touch_namespace_is_denied():
    policy = EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITHIN_BOUNDS,
        tier3_runbooks={"restart-workload"},
        denied_namespaces={"shop"},
    )
    assert evaluate(_action(), policy).decision is Decision.DENY


def test_blast_radius_limit_is_enforced():
    policy = EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITHIN_BOUNDS,
        tier3_runbooks={"restart-workload"},
        max_replicas_affected=2,
    )
    assert evaluate(_action(replicas_affected=10), policy).decision is Decision.DENY


def test_wrong_environment_is_denied():
    policy = EnvironmentPolicy(environment="production", granted_tier=Tier.ACT_WITHIN_BOUNDS)
    assert evaluate(_action(), policy).decision is Decision.DENY


# --- Classification, allow-list, and the wider blast-radius checks ---

from sre_agent.policy import ActionClass, classify_action


def test_catalogue_classifies_the_known_runbooks():
    assert classify_action("restart-workload") is ActionClass.REVERSIBLE_MUTATION
    assert classify_action("scale-replicas") is ActionClass.REVERSIBLE_MUTATION
    assert classify_action("rollback-deployment") is ActionClass.REVERSIBLE_MUTATION
    assert classify_action("query-metrics") is ActionClass.READ_ONLY
    assert classify_action("search-logs") is ActionClass.READ_ONLY
    assert classify_action("get-service-status") is ActionClass.READ_ONLY


def test_unknown_runbook_is_not_classified():
    assert classify_action("delete-everything") is None
    assert classify_action("") is None


def test_unknown_runbook_is_denied_even_at_tier3():
    policy = EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITHIN_BOUNDS,
        tier3_runbooks={"delete-everything"},
    )
    result = evaluate(_action(runbook_id="delete-everything"), policy)
    assert result.decision is Decision.DENY
    assert "allow-list" in result.reason


def test_read_only_action_is_allowed_at_observe_tier():
    policy = EnvironmentPolicy(environment="demo", granted_tier=Tier.OBSERVE)
    result = evaluate(_action(runbook_id="query-metrics", replicas_affected=0), policy)
    assert result.decision is Decision.ALLOW
    assert result.action_class is ActionClass.READ_ONLY


def test_read_only_action_still_runs_when_halted_but_mutation_does_not():
    policy = EnvironmentPolicy(environment="demo", granted_tier=Tier.OBSERVE, halted=True)
    read = evaluate(_action(runbook_id="search-logs", replicas_affected=0), policy)
    assert read.decision is Decision.ALLOW
    assert evaluate(_action(), policy).decision is Decision.DENY


def test_read_only_action_for_wrong_environment_is_denied():
    policy = EnvironmentPolicy(environment="production", granted_tier=Tier.OBSERVE)
    result = evaluate(_action(runbook_id="query-metrics", replicas_affected=0), policy)
    assert result.decision is Decision.DENY


def test_action_aimed_away_from_the_incidents_pinned_target_is_denied():
    other = Target(environment="demo", namespace="shop", service="payments")
    policy = EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITHIN_BOUNDS,
        tier3_runbooks={"restart-workload"},
    )
    result = evaluate(_action(), policy, incident_target=other)
    assert result.decision is Decision.DENY
    assert "pinned" in result.reason
    ok = evaluate(_action(), policy, incident_target=TARGET)
    assert ok.decision is Decision.ALLOW


def test_allowed_namespaces_scope_is_enforced():
    policy = EnvironmentPolicy(
        environment="demo",
        granted_tier=Tier.ACT_WITHIN_BOUNDS,
        tier3_runbooks={"restart-workload"},
        allowed_namespaces={"other"},
    )
    assert evaluate(_action(), policy).decision is Decision.DENY
    policy.allowed_namespaces = {"shop"}
    assert evaluate(_action(), policy).decision is Decision.ALLOW


def test_result_names_the_action_class():
    policy = EnvironmentPolicy(environment="demo", granted_tier=Tier.ACT_WITH_APPROVAL)
    result = evaluate(_action(), policy)
    assert result.action_class is ActionClass.REVERSIBLE_MUTATION
