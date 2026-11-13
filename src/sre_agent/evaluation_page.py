"""The generated release-scores block for the evaluation page.

In plain words, Phase 11's rule for ``docs/evaluation.md``:
the score and test-count numbers on that page must not be
written by hand, because hand-written numbers go stale — the
page carried v0.1.0's numbers (242 tests, 2026-11-09) long
after v0.2.0 shipped. Instead, one block on the page is
generated from the release record, and a docs test checks
that the block on the page is exactly what this module
generates, so a release that moves the record without
regenerating the page fails its tests.

The release record is two files, both already in the repo:

- ``evaluation/baseline.json`` — the recorded baseline the
  regression gate checks against: version, recorded date,
  chaos total, correct count, accuracy and unsafe actions.
- ``docs/v<version>-walkthrough.md`` — that release's
  walkthrough, whose release-day table holds the test
  count and the injection, Tier 3 and security-probe
  scores for the same release.

What this module does:

- ``load_release_scores`` reads both files and refuses
  (raises ``ValueError``) if a number it needs is missing,
  so generation fails loudly instead of writing a partial
  or invented block.
- ``render_scores_block`` turns those scores into the
  markdown block, markers included.
- ``update_evaluation_page`` replaces the block between
  the markers in ``docs/evaluation.md``; running it twice
  changes nothing the second time.
- ``python -m sre_agent.evaluation_page`` regenerates the
  page in a checkout. It prints the block it wrote.

Honest scope: these are release-day scores, frozen at the
release. Tests added after a release are not in its count;
the numbers move only when a new release records a new
baseline and walkthrough and this generator is run again.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pydantic import BaseModel

from .logging_config import get_logger

log = get_logger(__name__)

BEGIN_MARKER = "<!-- BEGIN GENERATED SCORES -->"
END_MARKER = "<!-- END GENERATED SCORES -->"


class ReleaseScores(BaseModel):
    """One release's scores, read from its release record."""

    version: str
    recorded_on: str
    test_count: int
    chaos_correct: int
    chaos_total: int
    accuracy: float
    unsafe_actions: int
    regression_passed: bool
    injection_passed: int
    injection_total: int
    tier3_passed: int
    tier3_total: int
    security_passed: int
    security_total: int


def _need(pattern: str, text: str, what: str) -> re.Match[str]:
    match = re.search(pattern, text)
    if match is None:
        raise ValueError(f"release walkthrough is missing {what}")
    return match


def load_release_scores(repo_root: Path) -> ReleaseScores:
    """Read one release's scores from its record in ``repo_root``.

    Raises ``ValueError`` if the baseline or the walkthrough
    for the baseline's version is missing a number the block
    needs — generation must fail, never guess.
    """
    baseline_path = repo_root / "evaluation" / "baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    version = str(baseline["version"])
    walkthrough_path = repo_root / "docs" / f"v{version}-walkthrough.md"
    if not walkthrough_path.is_file():
        raise ValueError(f"no release walkthrough for version {version}: {walkthrough_path}")
    walkthrough = walkthrough_path.read_text(encoding="utf-8")

    test_count = int(
        _need(r"\| Full test suite \| `pytest` \| (\d+) tests pass", walkthrough, "the test count").group(1)
    )
    injection = _need(
        r"\| Prompt injection \| `sre-agent injection-check` \| (\d+) of (\d+)",
        walkthrough,
        "the prompt-injection score",
    )
    tier3 = _need(
        r"\| Tier 3 and kill switch \| `sre-agent tier3-check` \| (\d+) of (\d+)",
        walkthrough,
        "the Tier 3 drill score",
    )
    security = _need(
        r"\| Security probes \| `sre-agent security-check` \| (\d+) of (\d+)",
        walkthrough,
        "the security-probe score",
    )
    regression_passed = f"PASS against the v{version} baseline" in walkthrough
    if not regression_passed:
        raise ValueError("release walkthrough is missing the regression PASS line")

    return ReleaseScores(
        version=version,
        recorded_on=str(baseline["recorded_on"]),
        test_count=test_count,
        chaos_correct=int(baseline["correct"]),
        chaos_total=int(baseline["total"]),
        accuracy=float(baseline["accuracy"]),
        unsafe_actions=int(baseline["unsafe_actions"]),
        regression_passed=regression_passed,
        injection_passed=int(injection.group(1)),
        injection_total=int(injection.group(2)),
        tier3_passed=int(tier3.group(1)),
        tier3_total=int(tier3.group(2)),
        security_passed=int(security.group(1)),
        security_total=int(security.group(2)),
    )


def render_scores_block(scores: ReleaseScores) -> str:
    """Render the generated markdown block for one release's scores."""
    regression = "PASS" if scores.regression_passed else "FAIL"
    lines = [
        BEGIN_MARKER,
        "## Current release scores — generated, do not edit by hand",
        "",
        (
            "This block is generated from the release record "
            "(`evaluation/baseline.json` and the release walkthrough) by "
            "`python -m sre_agent.evaluation_page`. A docs test checks that "
            "it matches the record, so the numbers cannot drift by hand."
        ),
        "",
        (
            f"Release **v{scores.version}**, recorded {scores.recorded_on}: "
            f"{scores.test_count} tests pass; chaos scenarios "
            f"{scores.chaos_correct} of {scores.chaos_total} correct "
            f"(accuracy {scores.accuracy:.0%}); regression gate {regression}; "
            f"prompt-injection set {scores.injection_passed} of "
            f"{scores.injection_total}; Tier 3 drills {scores.tier3_passed} of "
            f"{scores.tier3_total}; security probes {scores.security_passed} of "
            f"{scores.security_total}; {scores.unsafe_actions} unsafe actions."
        ),
        "",
        (
            "These are the release-day scores, frozen at the release. "
            "Tests added since the release are not in this count; the "
            "numbers move only when a new release is recorded and the "
            "block is regenerated. The scores are on seeded demo "
            "scenarios only — they are not a claim about real systems."
        ),
        END_MARKER,
    ]
    return "\n".join(lines)


def update_evaluation_page(repo_root: Path) -> str:
    """Regenerate the scores block in ``docs/evaluation.md``.

    Returns the block that was written. Raises ``ValueError``
    if the page has no generated block to replace.
    """
    page_path = repo_root / "docs" / "evaluation.md"
    page = page_path.read_text(encoding="utf-8")
    if BEGIN_MARKER not in page or END_MARKER not in page:
        raise ValueError("docs/evaluation.md has no generated scores block to replace")
    block = render_scores_block(load_release_scores(repo_root))
    start = page.index(BEGIN_MARKER)
    end = page.index(END_MARKER) + len(END_MARKER)
    page_path.write_text(page[:start] + block + page[end:], encoding="utf-8")
    log.info("evaluation page scores block regenerated for v%s", load_release_scores(repo_root).version)
    return block


def main(argv: list[str] | None = None) -> int:
    """Regenerate the block in the checkout this is run from."""
    repo_root = Path.cwd()
    try:
        block = update_evaluation_page(repo_root)
    except (OSError, ValueError, KeyError) as exc:
        print(f"sre-agent evaluation page: could not generate the scores block: {exc}")
        return 1
    print(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
