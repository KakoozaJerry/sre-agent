"""The operator and runbook authoring guides: present and honest.

Phase 5's docs item is only done if the guides exist, the README
links them, and they say the plain things an operator or a
runbook author must not be misled about: the server must stay
local until the security findings are fixed, a server-side run
does not by itself mark an incident resolved, and there is no
``sre-agent halt`` command in this build. These tests pin those
sentences so a later edit cannot quietly drop them.

The v0.1.0 release is pinned here too: the walkthrough must
exist, say what was measured and what was not run, and the
version must agree in the three places it lives — the package,
the project file, and the recorded regression baseline — so a
release can never be half-bumped.

The v0.2.0 release adds its own pins: a changelog carrying both
releases with the improved-not-closed findings stated, a
v0.2.0 walkthrough, and the community basics — a contributing
guide that names the real checks and the rules that do not
bend, a code of conduct, the two issue templates and the pull
request template — all linked from the README, so a later edit
cannot quietly drop the way in for a new contributor.
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
    # A record-only server shows no diagnosis; the guide says why.
    assert "Diagnosis: none yet" in text
    # Live approvals are served by the server, and a run does not
    # claim the incident is resolved.
    assert "Approve a fix on the server" in text
    assert "does **not** mark the incident" in text
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
    assert __version__ == "0.2.0"
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


def test_operator_guide_states_memory_is_advice_only() -> None:
    text = _read(DOCS / "operator-guide.md")
    # Phase 7: memory advice is attached to a live diagnosis,
    # after it is decided; the guide must keep that boundary.
    assert "Memory advice (advice only" in text
    assert "it changes no claim or cause, it creates no proposal" in text


def test_operator_guide_states_the_database_store_honestly() -> None:
    text = _read(DOCS / "operator-guide.md")
    # Phase 7: a database store now exists, and the shipped app
    # still uses the in-memory one; the guide must say both, and
    # must no longer say there is no database.
    assert "SQLiteIncidentStore" in text
    assert "shipped server app still uses the in-memory store" in text
    assert "there is no database yet" not in text


def test_quickstart_script_is_the_one_command_install() -> None:
    script = _read(REPO_ROOT / "quickstart.sh")
    # Phase 8: one command must do the whole install and prove it:
    # private folder, install with server parts, version, a demo
    # diagnosis, and the scored scenarios -- in that order.
    for needle in (
        "python3",
        "-m venv .venv",
        'pip install -e ".[server]"',
        "sre-agent --version",
        "sre-agent diagnose",
        "sre-agent evaluate",
        "set -eu",
        # The 2026-11-12 field report's two fixes: a leftover
        # .venv on an old Python is remade, not kept, and pip is
        # refreshed before the install (an old pip cannot do an
        # editable install).
        "sys.version_info >= (3, 11)",
        "rm -rf .venv",
        "pip install --upgrade pip",
    ):
        assert needle in script


def test_readme_and_operator_guide_give_the_quickstart() -> None:
    readme = _read(REPO_ROOT / "README.md")
    assert "./quickstart.sh" in readme
    assert "clean environment" in readme
    guide = _read(DOCS / "operator-guide.md")
    assert "./quickstart.sh" in guide
    assert "clean environment" in guide


def test_readme_walkthrough_covers_the_live_path_end_to_end() -> None:
    readme = _read(REPO_ROOT / "README.md")
    # Phase 8: the walkthrough must take one alert through the
    # whole live path, in order: start the shipped server, send
    # the demo alert, read the diagnosis, propose and approve
    # the restart, and close the incident with a resolved alert.
    for needle in (
        "Walkthrough: one alert, end to end",
        "uvicorn sre_agent.server:app --port 8080",
        "/webhooks/generic",
        '"alert_name":"HighErrorRate","service":"checkout"',
        "sre-agent show <incident-id>",
        "Most likely: A call that checkout",
        "/actions",
        '"runbook_id":"restart-workload"',
        "/approvals/<pending-id>/approve",
        '"status":"resolved"',
    ):
        assert needle in readme


def test_readme_walkthrough_states_the_honest_limits() -> None:
    readme = _read(REPO_ROOT / "README.md")
    # The walkthrough runs on seeded demo data, an approved run
    # lands on mitigating (never straight to resolved), and the
    # shipped server's incidents are in memory only.
    assert "seeded demo data" in readme
    assert '"incident_status": "mitigating"' in readme
    assert "keeps its\nincidents in memory" in readme or "keeps its incidents in memory" in readme
    guide = _read(DOCS / "operator-guide.md")
    assert "Walkthrough: one alert" in guide


def test_changelog_carries_both_releases_honestly() -> None:
    text = _read(REPO_ROOT / "CHANGELOG.md")
    # Phase 8: the changelog is the release record a stranger
    # reads first; both releases are in it, newest first, and
    # the two findings that were improved rather than closed
    # are stated that way, not upgraded to fixed.
    assert text.index("## [0.2.0]") < text.index("## [0.1.0]")
    for needle in (
        "2026-11-12",
        "2026-11-09",
        "improved, not closed",
        "live path",
        "quickstart.sh",
    ):
        assert needle in text


def test_v020_walkthrough_exists_and_states_its_limits() -> None:
    text = _read(DOCS / "v0.2.0-walkthrough.md")
    for needle in (
        "sre-agent evaluate",
        "sre-agent regression-check",
        "0 unsafe actions",
        "Walkthrough: one alert, end to end",
    ):
        assert needle in text
    # The limits that ship with this release are stated in it:
    # the human GitHub Release step, the token-less server, and
    # the shipped app's in-memory store.
    assert "no tool for creating a GitHub Release" in text
    assert "no authentication" in text
    assert "in-memory store" in text


def test_contributing_guide_names_the_real_checks() -> None:
    text = _read(REPO_ROOT / "CONTRIBUTING.md")
    for needle in (
        "./quickstart.sh",
        'pip install -e ".[dev,server]"',
        "sre-agent injection-check",
        "sre-agent security-check",
        "No free-form commands, ever",
        "Telemetry is untrusted input",
        "--update-baseline",
        "CODE_OF_CONDUCT.md",
    ):
        assert needle in text


def test_code_of_conduct_and_templates_exist() -> None:
    conduct = _read(REPO_ROOT / "CODE_OF_CONDUCT.md")
    assert "harassment" in conduct
    assert "Contributor Covenant" in conduct
    github = REPO_ROOT / ".github"
    bug = _read(github / "ISSUE_TEMPLATE" / "bug_report.md")
    assert "name: Bug report" in bug
    assert "sre-agent --version" in bug
    feature = _read(github / "ISSUE_TEMPLATE" / "feature_request.md")
    assert "name: Feature request" in feature
    assert "How it stays safe" in feature
    config = _read(github / "ISSUE_TEMPLATE" / "config.yml")
    assert "CONTRIBUTING.md" in config
    pr = _read(github / "PULL_REQUEST_TEMPLATE.md")
    assert "sre-agent regression-check" in pr
    assert "docs/progress.md" in pr


def test_readme_links_the_release_and_community_pages() -> None:
    readme = _read(REPO_ROOT / "README.md")
    for needle in (
        "CHANGELOG.md",
        "docs/v0.2.0-walkthrough.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
    ):
        assert needle in readme


def test_operator_guide_states_the_phase7_evaluation_gate() -> None:
    text = _read(DOCS / "operator-guide.md")
    # Phase 7's last item: the five suites were re-run with the
    # live path wired, and a gate test file pins them together
    # with a fully wired server; the guide must keep that claim.
    assert "evaluation gate" in text
    assert "test_evaluation_gate.py" in text
    assert "fully wired" in text
