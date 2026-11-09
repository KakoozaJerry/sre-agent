"""Tests for the prompt-injection test set: hostile telemetry
must change nothing.

The set's job is to prove a boundary: hostile text is stored as
evidence data, and never crosses into a claim, a hypothesis, a
diagnosis, a target, or an action. These tests pin the eight
cases, the checks behind them, and that the checker itself can
fail — a test set that cannot fail proves nothing.
"""

from __future__ import annotations

import pytest

from sre_agent.cli import main
from sre_agent.injection import (
    default_injection_cases,
    run_injection_case,
    run_injection_suite,
)

EXPECTED_IDS = [
    "log-delete-order",
    "log-run-runbook-order",
    "log-fake-diagnosis",
    "log-secret-request",
    "alert-retarget-order",
    "alert-fake-resolved",
    "status-healthy-lie",
    "metric-label-order",
]


def _case(case_id: str):  # type: ignore[no-untyped-def]
    return next(c for c in default_injection_cases() if c.id == case_id)


def test_set_has_eight_cases_covering_four_surfaces() -> None:
    cases = default_injection_cases()
    assert [c.id for c in cases] == EXPECTED_IDS
    assert {c.surface for c in cases} == {
        "log",
        "alert",
        "service status",
        "metric label",
    }


def test_all_cases_pass_with_current_code() -> None:
    report = run_injection_suite()
    assert report.total == 8
    assert report.passed_count == 8
    for result in report.results:
        assert result.passed, (result.case_id, result.failed_checks)


def test_no_unsafe_actions_across_the_set() -> None:
    report = run_injection_suite()
    assert report.unsafe_actions == 0
    assert all(r.unsafe_actions == 0 for r in report.results)


def test_log_orders_never_reach_the_agents_own_words() -> None:
    for case_id in (
        "log-delete-order",
        "log-run-runbook-order",
        "log-fake-diagnosis",
    ):
        result = run_injection_case(_case(case_id))
        assert result.checks["no_order_in_output"], case_id
        assert result.checks["payload_stored_only_as_evidence"], case_id
        assert result.checks["queries_are_the_fixed_plan"], case_id
        assert result.checks["outcome_is_the_honest_one"], case_id


def test_planted_secrets_are_redacted_everywhere() -> None:
    result = run_injection_case(_case("log-secret-request"))
    assert result.checks["secrets_redacted_everywhere"]
    assert result.checks["no_order_in_output"]


def test_alert_text_cannot_retarget_or_close_an_incident() -> None:
    for case_id in ("alert-retarget-order", "alert-fake-resolved"):
        result = run_injection_case(_case(case_id))
        assert result.checks["target_pinned_from_labels"], case_id
        assert result.checks["not_closed_by_text"], case_id
        assert result.checks["incident_opened"], case_id


def test_status_message_cannot_hide_a_down_service() -> None:
    result = run_injection_case(_case("status-healthy-lie"))
    assert result.checks["outcome_is_the_honest_one"]
    assert result.checks["no_order_in_output"]


def test_metric_label_cannot_order_a_rollback() -> None:
    result = run_injection_case(_case("metric-label-order"))
    assert result.checks["no_order_in_output"]
    assert result.checks["queries_are_the_fixed_plan"]


def test_checker_can_fail() -> None:
    # A case whose forbidden words are part of the loop's own
    # honest output must fail: the checker is a measure, not a
    # rubber stamp.
    case = _case("log-delete-order").model_copy(
        update={"forbidden_in_output": ["error log line"]}
    )
    result = run_injection_case(case)
    assert not result.passed
    assert "no_order_in_output" in result.failed_checks


def test_unknown_case_id_is_an_error() -> None:
    case = _case("log-delete-order").model_copy(update={"id": "nope"})
    with pytest.raises(ValueError):
        run_injection_case(case)


def test_suite_is_deterministic() -> None:
    first = run_injection_suite()
    second = run_injection_suite()
    assert [(r.case_id, r.passed) for r in first.results] == [
        (r.case_id, r.passed) for r in second.results
    ]


def test_cli_injection_check_prints_report_and_exits_zero(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["injection-check"]) == 0
    out = capsys.readouterr().out
    assert "Passed: 8" in out
    assert "Unsafe actions across the set: 0" in out


def test_rendered_report_states_scores_and_the_known_limit() -> None:
    text = run_injection_suite().render_text()
    assert "Passed: 8" in text
    assert "log-delete-order" in text
    assert "Known limitation:" in text
    assert "timeout" in text
