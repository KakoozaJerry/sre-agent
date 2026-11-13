"""The generated evaluation-page scores block.

Phase 11: the score and test-count block on
``docs/evaluation.md`` is generated from the release record
(the recorded baseline and the release walkthrough), not
written by hand, so it cannot go stale the way the v0.1.0
figures did. These tests pin the generation itself: the
record is read, the block says exactly what the record
says, a missing number fails loudly, and regenerating the
page is idempotent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sre_agent.evaluation_page import (
    BEGIN_MARKER,
    END_MARKER,
    load_release_scores,
    render_scores_block,
    update_evaluation_page,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

WALKTHROUGH = """# v9.9.9 — release notes

| Check | Command | Result |
|---|---|---|
| Full test suite | `pytest` | 123 tests pass, safety suite included |
| Chaos scenarios | `sre-agent evaluate` | 5 of 5 correct, 0 unsafe actions |
| Regression gate | `sre-agent regression-check` | PASS against the v9.9.9 baseline |
| Prompt injection | `sre-agent injection-check` | 8 of 8 cases pass, 0 unsafe actions |
| Tier 3 and kill switch | `sre-agent tier3-check` | 7 of 7 drills pass |
| Security probes | `sre-agent security-check` | 10 of 10 probes pass |
"""


def _make_record(root: Path, walkthrough: str = WALKTHROUGH) -> Path:
    (root / "evaluation").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    baseline = {
        "version": "9.9.9",
        "recorded_on": "2026-01-02",
        "total": 5,
        "correct": 5,
        "accuracy": 1.0,
        "unsafe_actions": 0,
    }
    (root / "evaluation" / "baseline.json").write_text(
        json.dumps(baseline), encoding="utf-8"
    )
    (root / "docs" / "v9.9.9-walkthrough.md").write_text(
        walkthrough, encoding="utf-8"
    )
    return root


def test_load_release_scores_reads_the_record(tmp_path: Path) -> None:
    scores = load_release_scores(_make_record(tmp_path))
    assert scores.version == "9.9.9"
    assert scores.recorded_on == "2026-01-02"
    assert scores.test_count == 123
    assert scores.chaos_correct == 5
    assert scores.chaos_total == 5
    assert scores.unsafe_actions == 0
    assert scores.regression_passed is True
    assert (scores.injection_passed, scores.injection_total) == (8, 8)
    assert (scores.tier3_passed, scores.tier3_total) == (7, 7)
    assert (scores.security_passed, scores.security_total) == (10, 10)


def test_rendered_block_says_exactly_what_the_record_says(tmp_path: Path) -> None:
    block = render_scores_block(load_release_scores(_make_record(tmp_path)))
    assert block.startswith(BEGIN_MARKER)
    assert block.endswith(END_MARKER)
    for needle in (
        "v9.9.9",
        "2026-01-02",
        "123 tests pass",
        "5 of 5",
        "8 of 8",
        "7 of 7",
        "10 of 10",
        "0 unsafe actions",
        "do not edit by hand",
    ):
        assert needle in block


def test_missing_number_in_the_record_fails_loudly(tmp_path: Path) -> None:
    broken = WALKTHROUGH.replace("123 tests pass", "tests pass")
    with pytest.raises(ValueError, match="test count"):
        load_release_scores(_make_record(tmp_path, walkthrough=broken))


def test_missing_walkthrough_fails_loudly(tmp_path: Path) -> None:
    root = _make_record(tmp_path)
    (root / "docs" / "v9.9.9-walkthrough.md").unlink()
    with pytest.raises(ValueError, match="no release walkthrough"):
        load_release_scores(root)


def test_update_evaluation_page_is_idempotent(tmp_path: Path) -> None:
    root = _make_record(tmp_path)
    page = root / "docs" / "evaluation.md"
    page.write_text(
        f"before\n\n{BEGIN_MARKER}\nstale\n{END_MARKER}\n\nafter\n",
        encoding="utf-8",
    )
    first = update_evaluation_page(root)
    text_first = page.read_text(encoding="utf-8")
    second = update_evaluation_page(root)
    assert first == second
    assert page.read_text(encoding="utf-8") == text_first
    assert "123 tests pass" in text_first
    assert "stale" not in text_first
    assert text_first.startswith("before")
    assert text_first.rstrip().endswith("after")


def test_real_page_block_matches_the_real_record() -> None:
    page = (REPO_ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")
    assert render_scores_block(load_release_scores(REPO_ROOT)) in page
