"""Tests for the proposal-selection suite (Phase 12).

The chaos suite scores diagnoses; this suite scores which fix
a diagnosis points to. The selector is advice only — these
tests also pin that a selection runs nothing, and that the
conservative answers (escalate, select nothing) are scored as
answers, not treated as missing.
"""

from __future__ import annotations

from sre_agent.proposal_selection import (
    default_proposal_cases,
    run_proposal_suite,
    select_runbook,
)


def test_suite_has_nine_cases_and_scores_all_correct() -> None:
    report = run_proposal_suite()
    assert report.total == 9
    assert report.correct == 9
    assert report.accuracy == 1.0
    assert all(r.selection_correct for r in report.results)


def test_no_unsafe_actions_across_the_suite() -> None:
    report = run_proposal_suite()
    assert report.unsafe_actions == 0
    assert all(r.unsafe_actions == 0 for r in report.results)


def test_cases_cover_each_runbook_and_escalation() -> None:
    expected = {c.expected_runbook for c in default_proposal_cases()}
    assert expected == {
        "rollback-deployment",
        "scale-replicas",
        "restart-workload",
        None,
    }


def test_rollback_needs_a_previous_version() -> None:
    hyp = "A recent deploy is the likely cause for search"
    assert (
        select_runbook(
            hyp, ready_replicas=2, desired_replicas=2,
            has_previous_version=True,
        )
        == "rollback-deployment"
    )
    # No previous version: a rollback cannot be selected, and
    # no other fix is guessed in its place.
    assert (
        select_runbook(
            hyp, ready_replicas=2, desired_replicas=2,
            has_previous_version=False,
        )
        is None
    )


def test_no_diagnosis_selects_nothing() -> None:
    assert (
        select_runbook(
            None, ready_replicas=0, desired_replicas=3,
            has_previous_version=True,
        )
        is None
    )


def test_selection_can_fail() -> None:
    # A wrong expectation must score as wrong.
    case = default_proposal_cases()[0].model_copy(
        update={"expected_runbook": "restart-workload"}
    )
    from sre_agent.proposal_selection import run_proposal_case

    result = run_proposal_case(case)
    assert not result.selection_correct


def test_rendered_report_states_advice_only() -> None:
    text = run_proposal_suite().render_text()
    assert "Accuracy: 100%" in text
    assert "Unsafe actions across the suite: 0" in text
    assert "Advice only" in text
