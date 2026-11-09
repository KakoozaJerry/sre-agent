"""Tests for the evaluation harness: five chaos scenarios, scored.

The suite's job is to measure, not to flatter: these tests pin
the five named faults, the scoring rules, the safety count, and
the stated limits (no remediation or wall-clock scoring yet).
"""

from __future__ import annotations

from sre_agent.cli import main
from sre_agent.evaluation import (
    default_scenarios,
    run_evaluation,
    run_scenario,
)
from sre_agent.reasoning import DiagnosisOutcome


def test_suite_has_the_five_named_faults() -> None:
    ids = [s.id for s in default_scenarios()]
    assert ids == [
        "pod-kill",
        "latency-injection",
        "bad-deploy",
        "disk-fill",
        "config-error",
    ]


def test_all_five_scenarios_score_correct_with_current_loop() -> None:
    report = run_evaluation()
    assert report.total == 5
    assert report.correct == 5
    assert report.accuracy == 1.0
    for result in report.results:
        assert result.outcome_correct, result.scenario_id
        assert result.hypothesis_correct, result.scenario_id
        assert result.diagnosis_correct, result.scenario_id


def test_no_unsafe_actions_across_the_suite() -> None:
    report = run_evaluation()
    assert report.unsafe_actions == 0
    assert all(r.unsafe_actions == 0 for r in report.results)


def test_every_diagnosis_is_evidence_backed() -> None:
    report = run_evaluation()
    assert all(r.evidence_backed for r in report.results)


def test_timeout_scenario_is_strengthened_by_two_sources() -> None:
    scenario = next(
        s for s in default_scenarios() if s.id == "latency-injection"
    )
    result = run_scenario(scenario)
    assert result.actual_outcome is DiagnosisOutcome.DIAGNOSED
    assert result.top_hypothesis is not None
    assert "timing out" in result.top_hypothesis
    # Error logs plus a non-zero error metric agree: 0.85.
    assert result.top_hypothesis_confidence == 0.85


def test_pod_kill_scenario_names_missing_copies() -> None:
    scenario = next(s for s in default_scenarios() if s.id == "pod-kill")
    result = run_scenario(scenario)
    assert result.top_hypothesis is not None
    assert "Fewer copies" in result.top_hypothesis
    assert result.steps_used == 4


def test_tool_less_causes_carry_a_stated_limitation() -> None:
    by_id = {r.scenario_id: r for r in run_evaluation().results}
    # The loop has no disk or config tool, so those scenarios must
    # say what they cannot score, in the result itself.
    assert "no disk tool" in by_id["disk-fill"].known_limitation
    assert "no config tool" in by_id["config-error"].known_limitation
    assert "no deploy tool" in by_id["bad-deploy"].known_limitation


def test_scoring_can_fail_an_answer() -> None:
    # A wrong expectation must score as wrong: the harness is a
    # measure, not a rubber stamp.
    scenario = next(
        s for s in default_scenarios() if s.id == "latency-injection"
    )
    wrong = scenario.model_copy(
        update={"expected_top_hypothesis_contains": "Fewer copies"}
    )
    result = run_scenario(wrong)
    assert result.outcome_correct
    assert not result.hypothesis_correct
    assert not result.diagnosis_correct


def test_evaluation_is_deterministic() -> None:
    first = run_evaluation()
    second = run_evaluation()
    assert first.correct == second.correct
    assert first.accuracy == second.accuracy
    assert [
        (r.scenario_id, r.actual_outcome, r.top_hypothesis)
        for r in first.results
    ] == [
        (r.scenario_id, r.actual_outcome, r.top_hypothesis)
        for r in second.results
    ]


def test_cli_evaluate_prints_report_and_exits_zero(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["evaluate"]) == 0
    out = capsys.readouterr().out
    assert "Accuracy: 100%" in out
    assert "Unsafe actions across the suite: 0" in out


def test_rendered_report_states_scores_and_limits() -> None:
    text = run_evaluation().render_text()
    assert "Accuracy: 100%" in text
    assert "Unsafe actions across the suite: 0" in text
    assert "pod-kill" in text
    assert "Known limitation:" in text
    assert "Not scored yet" in text
