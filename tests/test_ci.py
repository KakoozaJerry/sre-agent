"""Fact pins for the CI workflow (Phase 11: CI at the project's own bar).

The workflow file itself can only be changed on the GitHub
website -- the connection used to build this project cannot
write workflow files -- so these tests pin the facts the file
must carry, in the local copy that is the source for that
website step. They read the file as text (no YAML library is a
project dependency) and pin facts, not exact wording, the same
rule the docs tests follow.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _read() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _quickstart_section() -> str:
    text = _read()
    marker = "\n  quickstart:\n"
    assert marker in text, "workflow has no quickstart job"
    return text.split(marker, 1)[1]


def test_ci_matrix_covers_supported_pythons_and_systems() -> None:
    text = _read()
    for version in ('"3.11"', '"3.12"', '"3.13"'):
        assert version in text
    assert "ubuntu-latest" in text
    assert "macos-latest" in text
    # One failing leg must not cancel the others: the point of
    # the matrix is to learn which combination breaks.
    assert "fail-fast: false" in text


def test_ci_runs_lint_types_and_tests() -> None:
    text = _read()
    assert "ruff check src tests" in text
    assert "mypy src" in text
    assert "pytest -q" in text
    assert 'pip install -e ".[dev]"' in text


def test_ci_quickstart_job_runs_script_then_scored_checks() -> None:
    section = _quickstart_section()
    # The quickstart must run on the clean checkout as checked
    # out: no install step before it in this job, or the job
    # would no longer prove a stranger's first run works.
    script_at = section.index("./quickstart.sh")
    assert "pip install" not in section[:script_at]
    # Every scored CLI check the contributing guide lists runs
    # against the quickstart's own install.
    for command in (
        "sre-agent evaluate",
        "sre-agent regression-check",
        "sre-agent injection-check",
        "sre-agent tier3-check",
        "sre-agent security-check",
    ):
        assert command in section
        assert section.index(command) > script_at


def test_ci_never_skips_a_failing_check() -> None:
    text = _read()
    assert "continue-on-error" not in text
    assert "|| true" not in text
