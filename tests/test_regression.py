"""Tests for the regression gate: scores may not quietly get worse.

The gate's rules are pinned here one by one — the absolute
zero-unsafe-actions rule, evidence backing, the accuracy floor,
named per-scenario regressions, missing scenarios — plus the
baseline file's round trip and the rule that a bad run can never
be recorded as a new baseline.
"""

from __future__ import annotations

import pytest

from sre_agent.cli import main
from sre_agent.evaluation import EvaluationReport, ScenarioResult, run_evaluation
from sre_agent.reasoning import DiagnosisOutcome
from sre_agent.regression import (
    baseline_from_report,
    check_regression,
    load_baseline,
    record_new_baseline,
    save_baseline,
)


def _scenario_result(
    scenario_id: str,
    *,
    correct: bool = True,
    evidence_backed: bool = True,
    unsafe_actions: int = 0,
) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=scenario_id,
        title=f"Scenario {scenario_id}",
        expected_outcome=DiagnosisOutcome.DIAGNOSED,
        actual_outcome=DiagnosisOutcome.DIAGNOSED,
        outcome_correct=correct,
        top_hypothesis="a cause" if correct else None,
        top_hypothesis_confidence=0.8 if correct else 0.0,
        hypothesis_correct=correct,
        diagnosis_correct=correct,
        evidence_backed=evidence_backed,
        unsafe_actions=unsafe_actions,
        steps_used=4,
        summary="summary",
    )


def _report(results: list[ScenarioResult]) -> EvaluationReport:
    correct = sum(1 for r in results if r.diagnosis_correct)
    total = len(results)
    return EvaluationReport(
        results=results,
        total=total,
        correct=correct,
        accuracy=(correct / total) if total else 0.0,
        unsafe_actions=sum(r.unsafe_actions for r in results),
    )


def test_current_suite_passes_against_its_own_baseline() -> None:
    report = run_evaluation()
    baseline = baseline_from_report(report, version="test", recorded_on="2026-11-09")
    result = check_regression(report, baseline, current_version="test")
    assert result.passed
    assert result.failures == []
    assert result.unsafe_actions == 0
    assert result.accuracy == result.baseline_accuracy == 1.0


def test_any_unsafe_action_fails_even_at_full_accuracy() -> None:
    baseline = baseline_from_report(
        _report([_scenario_result("a")]), version="v1", recorded_on="2026-11-09"
    )
    bad = _report([_scenario_result("a", unsafe_actions=1)])
    result = check_regression(bad, baseline)
    assert not result.passed
    assert any("unsafe" in f for f in result.failures)


def test_unbacked_evidence_fails_the_gate() -> None:
    baseline = baseline_from_report(
        _report([_scenario_result("a")]), version="v1", recorded_on="2026-11-09"
    )
    bad = _report([_scenario_result("a", evidence_backed=False)])
    result = check_regression(bad, baseline)
    assert not result.passed
    assert any("evidence" in f for f in result.failures)


def test_accuracy_drop_fails_and_says_by_how_much() -> None:
    baseline = baseline_from_report(
        _report([_scenario_result("a"), _scenario_result("b")]),
        version="v1",
        recorded_on="2026-11-09",
    )
    worse = _report([_scenario_result("a"), _scenario_result("b", correct=False)])
    result = check_regression(worse, baseline)
    assert not result.passed
    assert any("accuracy dropped from 100%" in f for f in result.failures)


def test_named_scenario_regression_is_named() -> None:
    baseline = baseline_from_report(
        _report([_scenario_result("a"), _scenario_result("b")]),
        version="v1",
        recorded_on="2026-11-09",
    )
    worse = _report([_scenario_result("a"), _scenario_result("b", correct=False)])
    result = check_regression(worse, baseline)
    assert any(
        "b" in f and "was correct in the baseline" in f for f in result.failures
    )


def test_scenario_missing_from_run_fails() -> None:
    baseline = baseline_from_report(
        _report([_scenario_result("a"), _scenario_result("b")]),
        version="v1",
        recorded_on="2026-11-09",
    )
    short = _report([_scenario_result("a")])
    result = check_regression(short, baseline)
    assert not result.passed
    assert any("missing" in f for f in result.failures)


def test_new_scenario_is_a_note_not_a_failure() -> None:
    baseline = baseline_from_report(
        _report([_scenario_result("a")]), version="v1", recorded_on="2026-11-09"
    )
    bigger = _report([_scenario_result("a"), _scenario_result("b")])
    result = check_regression(bigger, baseline)
    assert result.passed
    assert any("new" in n for n in result.notes)


def test_baseline_file_round_trips(tmp_path) -> None:  # type: ignore[no-untyped-def]
    report = run_evaluation()
    baseline = baseline_from_report(report, version="v9", recorded_on="2026-11-09")
    path = tmp_path / "nested" / "baseline.json"
    save_baseline(baseline, path)
    loaded = load_baseline(path)
    assert loaded == baseline
    assert loaded.history[-1].accuracy == 1.0
    assert loaded.history[-1].unsafe_actions == 0


def test_record_new_baseline_appends_release_history(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "baseline.json"
    report = run_evaluation()
    first = record_new_baseline(report, path, version="v1", recorded_on="2026-11-09")
    assert [h.version for h in first.history] == ["v1"]
    second = record_new_baseline(report, path, version="v2", recorded_on="2026-12-01")
    assert [h.version for h in second.history] == ["v1", "v2"]
    assert load_baseline(path).version == "v2"


def test_record_new_baseline_refuses_unsafe_run(tmp_path) -> None:  # type: ignore[no-untyped-def]
    bad = _report([_scenario_result("a", unsafe_actions=2)])
    with pytest.raises(ValueError, match="unsafe"):
        record_new_baseline(bad, tmp_path / "baseline.json")
    assert not (tmp_path / "baseline.json").exists()


def test_committed_baseline_matches_current_suite() -> None:
    # The file in the repo is the record the gate checks against;
    # this test fails if the suite drifts from it unrecorded.
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "evaluation" / "baseline.json"
    baseline = load_baseline(path)
    result = check_regression(run_evaluation(), baseline)
    assert result.passed, result.failures
    assert baseline.unsafe_actions == 0


def test_cli_regression_check_passes_and_prints_verdict(capsys, tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "baseline.json"
    record_new_baseline(run_evaluation(), path, version="v1", recorded_on="2026-11-09")
    assert main(["regression-check", "--baseline", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Regression gate: PASS" in out
    assert "Unsafe actions: 0" in out


def test_cli_regression_check_without_baseline_file_is_a_plain_error(capsys, tmp_path) -> None:  # type: ignore[no-untyped-def]
    missing = tmp_path / "nope.json"
    assert main(["regression-check", "--baseline", str(missing)]) == 1
    err = capsys.readouterr().err
    assert "no baseline file" in err
