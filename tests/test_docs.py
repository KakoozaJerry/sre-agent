"""The documents: present, linked, and pinned by facts.

Phase 11's doc-map item calmed these tests down on purpose.
They pin facts, not sentences: a document exists, the README
links it, the version agrees in the places it lives, the
release scores are the numbers in the release record, and a
small set of named safety facts is present (checked on
whitespace-flattened text, so re-wrapping or rewording a
paragraph does not break a test). A later edit can improve
the wording freely; it cannot quietly drop a fact, a link,
a version, or a score.

The facts pinned here include: the guides exist and name the
real commands and limits (a token-less server has no
authentication, a server-side run does not by itself mark an
incident resolved, and there is no ``sre-agent halt`` command
in this build); the released walkthroughs exist as frozen
release artefacts carrying their release-day scores; the
changelog carries both releases with the improved-not-closed
findings stated; and the community basics — contributing
guide, code of conduct, issue templates, pull request
template — exist and are linked from the README.
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


def _flat(text: str) -> str:
    """One line, single spaces: wording may move, facts may not."""
    return " ".join(text.split())


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
    flat = _flat(_read(DOCS / "operator-guide.md"))
    # Facts, not sentences: a token-less server has no
    # authentication (F-01); a record-only server shows no
    # diagnosis; a server-side run does not mark an incident
    # resolved; and there is no halt command in this build.
    assert "no authentication" in flat
    assert "Diagnosis: none yet" in flat
    assert "Approve a fix on the server" in flat
    assert "not** mark the incident resolved" in flat
    assert "no `sre-agent halt` command" in flat


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
    flat = _flat(_read(DOCS / "runbook-authoring-guide.md"))
    assert "never a command" in flat
    assert "Tier 3" in flat
    assert "replicas_affected" in flat


def test_readme_links_both_guides() -> None:
    readme = _read(REPO_ROOT / "README.md")
    assert "docs/operator-guide.md" in readme
    assert "docs/runbook-authoring-guide.md" in readme


def test_release_walkthrough_exists_and_covers_the_scenario() -> None:
    text = _read(DOCS / "v0.1.0-walkthrough.md")
    flat = _flat(text)
    for needle in (
        "sre-agent evaluate",
        "sre-agent regression-check",
        "sre-agent approve-demo --by",
        "pod kill",
        "0 unsafe actions",
        # Release-day score facts for this frozen artefact.
        "242 tests pass",
        "5 of 5",
        "8 of 8",
        "7 of 7",
        "10 of 10",
    ):
        assert needle in flat


def test_release_walkthrough_states_what_was_not_run() -> None:
    flat = _flat(_read(DOCS / "v0.1.0-walkthrough.md"))
    # Facts: the Compose walkthrough was not run in the release
    # environment, and the server limits ship with the release.
    assert "not** run in the release" in flat
    assert "no authentication" in flat


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
    flat = _flat(_read(DOCS / "operator-guide.md"))
    # Phase 7 fact: memory advice is attached to a live
    # diagnosis, after it is decided, and changes nothing.
    assert "Memory advice (advice only" in flat
    assert "after the diagnosis is decided" in flat
    assert "creates no proposal" in flat


def test_operator_guide_states_the_database_store_honestly() -> None:
    flat = _flat(_read(DOCS / "operator-guide.md"))
    # Phase 7 facts: a database store exists, and the shipped
    # app still uses the in-memory one.
    assert "SQLiteIncidentStore" in flat
    assert "in-memory store" in flat
    assert "there is no database yet" not in flat


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
    flat = _flat(readme)
    # Facts: the walkthrough runs on seeded demo data, an
    # approved run lands on mitigating (never straight to
    # resolved), and the shipped server's incidents are in
    # memory only.
    assert "seeded demo data" in flat
    assert '"incident_status": "mitigating"' in readme
    assert "keeps its incidents in memory" in flat
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
    flat = _flat(text)
    for needle in (
        "sre-agent evaluate",
        "sre-agent regression-check",
        "0 unsafe actions",
        "Walkthrough: one alert, end to end",
    ):
        assert needle in flat
    # Facts that ship with this release: the human GitHub
    # Release step, the token-less server, and the shipped
    # app's in-memory store.
    assert "no tool for creating a GitHub Release" in flat
    assert "no authentication" in flat
    assert "in-memory store" in flat
    # Release-day score facts for this frozen artefact.
    assert "358 tests pass" in flat
    assert "5 of 5" in flat
    assert "8 of 8" in flat


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


def test_readme_is_a_pitch_above_the_fold() -> None:
    readme = _read(REPO_ROOT / "README.md")
    # Phase 11: above the fold the README carries a capability
    # table, the release-day score line, and links to the
    # quickstart, the hosted demo and the evaluation page --
    # all before the "In plain English" section.
    above = readme.split("## In plain English", 1)[0]
    assert "| Capability |" in above
    assert "chaos 5 of 5" in above
    assert "0 unsafe actions" in above
    assert "#quick-start" in above
    assert "#try-the-hosted-demo" in above
    assert "docs/evaluation.md" in above


def test_readme_status_is_short_and_complete() -> None:
    readme = _read(REPO_ROOT / "README.md")
    # Phase 11: the old Status wall is cut to about ten lines:
    # current version, what works today, top scores, one honest
    # limit, and a link to the progress log. The narrative lives
    # in docs/progress.md and CHANGELOG.md, not here.
    status = readme.split("## Status", 1)[1].split("## ", 1)[0]
    lines = [line for line in status.strip().splitlines() if line.strip()]
    assert len(lines) <= 10, f"Status has {len(lines)} lines, want about 10"
    for needle in (
        "0.2.0",
        "What works today",
        "chaos 5 of 5",
        "One honest limit",
        "docs/progress.md",
    ):
        assert needle in status


def test_evaluation_page_scores_block_is_generated_and_current() -> None:
    text = _read(DOCS / "evaluation.md")
    # Phase 11: the score/test-count block is generated from
    # the release record (baseline + release walkthrough),
    # not written by hand, and a docs test pins it here so a
    # release that moves the record without regenerating the
    # page fails. The stale v0.1.0 figures must stay gone.
    from sre_agent.evaluation_page import load_release_scores, render_scores_block

    assert render_scores_block(load_release_scores(REPO_ROOT)) in text
    assert "242 tests" not in text
    assert "Current measured result (2026-11-09)" not in text
    scores = load_release_scores(REPO_ROOT)
    assert f"{scores.test_count} tests pass" in text
    assert scores.version == __version__


def test_readme_opening_carries_the_positioning() -> None:
    readme = _read(REPO_ROOT / "README.md")
    # Phase 11 positioning: the wedge is auditability, stated in
    # the opening above the fold -- the SRE agent you can audit,
    # no API key, no model required, every claim cites its
    # evidence, and every release ships its scores.
    above = readme.split("## In plain English", 1)[0]
    for needle in (
        "The SRE agent you can audit",
        "No API key",
        "no model required",
        "every claim cites its evidence",
        "every release ships its scores",
    ):
        assert needle in above


def test_readme_carries_the_badges() -> None:
    readme = _read(REPO_ROOT / "README.md")
    # Phase 11: four badges sit at the top of the README -- CI
    # status from the repo's own workflow file, the latest
    # release and the licence read from the repository itself
    # by shields.io, and the Python floor (3.11) the project
    # file declares in requires-python.
    above = readme.split("## In plain English", 1)[0]
    for needle in (
        "actions/workflows/ci.yml/badge.svg",
        "img.shields.io/github/v/release/KakoozaJerry/sre-agent",
        "img.shields.io/github/license/KakoozaJerry/sre-agent",
        "img.shields.io/badge/python-3.11",
    ):
        assert needle in above


def test_security_policy_covers_reporting_versions_and_scope() -> None:
    text = _read(REPO_ROOT / "SECURITY.md")
    flat = _flat(text)
    # Phase 11: SECURITY.md must say how to report privately
    # (the same path CONTRIBUTING already gave, with no
    # invented email address), which versions are supported,
    # and what is in and out of scope.
    for needle in (
        "do not open a public issue",
        "Abraham Kakooza",
        "no separate security email address",
        "0.2.0 (latest release)",
        "What is in scope",
        "What is out of scope",
        "docs/security-review.md",
        "docs/hosted-demo.md",
    ):
        assert needle in flat
    assert __version__ in text


def test_hosted_demo_guide_explains_the_public_token() -> None:
    text = _read(DOCS / "hosted-demo.md")
    flat = _flat(text)
    # Phase 11: the hosted-demo token rationale belongs in the
    # hosted-demo guide itself: public by design because the
    # demo is read-mostly, Tier-3-only, capped, and holds no
    # real data.
    for needle in (
        "Why the hosted demo's token is public",
        "sre-agent-demo-visitor",
        "read-mostly",
        "Tier-3-only",
        "Capped",
        "No real data",
    ):
        assert needle in flat
    readme = _read(REPO_ROOT / "README.md")
    assert "SECURITY.md" in readme
    contributing = _read(REPO_ROOT / "CONTRIBUTING.md")
    assert "SECURITY.md" in contributing


def test_readme_documents_section_is_a_doc_map() -> None:
    readme = _read(REPO_ROOT / "README.md")
    documents = readme.split("## Documents", 1)[1].split("## Quick start", 1)[0]
    flat = _flat(documents)
    # Phase 11 doc map: each core document is named with the
    # one job it does, in the map's own words.
    for needle in (
        "Pitch and start",
        "docs/operator-guide.md",
        "Run it",
        "docs/evaluation.md",
        "Scores",
        "docs/security-review.md",
        "Safety",
        "docs/progress.md",
        "Archive",
    ):
        assert needle in flat


def test_released_walkthroughs_are_frozen_artefacts() -> None:
    readme = _read(REPO_ROOT / "README.md")
    flat = _flat(readme)
    # Phase 11: the README's doc map states that the released
    # walkthroughs are frozen, and where current numbers live
    # instead. The walkthroughs themselves keep their own
    # release-day facts (pinned above), unchanged by later work.
    assert "Released walkthroughs are frozen release artefacts" in flat
    assert "not updated for later work" in flat
    assert "docs/v0.1.0-walkthrough.md" in readme
    assert "docs/v0.2.0-walkthrough.md" in readme
