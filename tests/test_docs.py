"""The operator and runbook authoring guides: present and honest.

Phase 5's docs item is only done if the guides exist, the README
links them, and they say the plain things an operator or a
runbook author must not be misled about: the server must stay
local until the security findings are fixed, approving a live
server incident is not wired, and there is no ``sre-agent halt``
command in this build. These tests pin those sentences so a
later edit cannot quietly drop them.

The v0.1.0 release is pinned here too: the walkthrough must
exist, say what was measured and what was not run, and the
version must agree in the three places it lives — the package,
the project file, and the recorded regression baseline — so a
release can never be half-bumped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sre_agent import __version__

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing document: {path}"
    return path.read_text(encoding="utf-8")


def test_operator_guide_exists_and_covers_the_day_to_day() -> None:
    text = _read(DOCS / "operator-guide.md")
    for needle in (
        "uvicorn sre_agent.server:app --port 8080",
        "sre-agent incidents",
        "sre-agent approve-demo --by",
        "SRE_AGENT_BASE_URL",
        "15 minutes",
    ):
        assert needle in text


def test_operator_guide_states_the_hard_limits_plainly() -> None:
    text = _read(DOCS / "operator-guide.md")
    # F-01/F-02: the server stays on one operator's own machine.
    assert "no authentication" in text
    # Server-side diagnosis and live approvals are not wired yet.
    assert "Diagnosis: none yet" in text
    assert "not wired yet" in text
    # There is no halt command in this build; the guide must say so.
    assert "no\n  `sre-agent halt` command" in text or "no `sre-agent halt` command" in text


def test_runbook_guide_exists_and_names_the_four_places() -> None:
    text = _read(DOCS / "runbook-authoring-guide.md")
    for needle in (
        "RUNBOOK_CATALOG",
        "RUNBOOK_PARAMETERS",
        "validate_parameters",
        "RunbookExecutor.execute",
        "restart-workload",
        "scale-replicas",
        "rollback-deployment",
    ):
        assert needle in text


def test_runbook_guide_keeps_the_never_rules() -> None:
    text = _read(DOCS / "runbook-authoring-guide.md")
    assert "a runbook is never a\ncommand" in text or "never a command" in text
    assert "Tier 3" in text
    assert "replicas_affected" in text


def test_readme_links_both_guides() -> None:
    readme = _read(REPO_ROOT / "README.md")
    assert "docs/operator-guide.md" in readme
    assert "docs/runbook-authoring-guide.md" in readme


def test_release_walkthrough_exists_and_covers_the_scenario() -> None:
    text = _read(DOCS / "v0.1.0-walkthrough.md")
    for needle in (
        "sre-agent evaluate",
        "sre-agent regression-check",
        "sre-agent approve-demo --by",
        "pod kill",
        "0 unsafe actions",
    ):
        assert needle in text


def test_release_walkthrough_states_what_was_not_run() -> None:
    text = _read(DOCS / "v0.1.0-walkthrough.md")
    # The Compose walkthrough was not run in the release
    # environment, and the server limits ship with the release.
    assert "not** run in the release" in text
    assert "no authentication" in text


def test_version_agrees_in_package_project_file_and_baseline() -> None:
    assert __version__ == "0.1.0"
    project = _read(REPO_ROOT / "pyproject.toml")
    match = re.search(r'^version = "([^"]+)"', project, re.MULTILINE)
    assert match is not None
    assert match.group(1) == __version__
    baseline = json.loads(
        _read(REPO_ROOT / "evaluation" / "baseline.json")
    )
    assert baseline["version"] == __version__
    assert baseline["history"][-1]["version"] == __version__


def test_readme_links_the_release_walkthrough() -> None:
    readme = _read(REPO_ROOT / "README.md")
    assert "docs/v0.1.0-walkthrough.md" in readme
