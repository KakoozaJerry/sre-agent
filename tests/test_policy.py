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
