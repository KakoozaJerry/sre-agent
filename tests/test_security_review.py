"""Tests for the security review of the agent itself.

The probes attack the agent's own surface the way an attacker
would — made-up runbooks, smuggled commands, retargeted fixes,
planted secrets, replayed approvals — so these tests pin both
halves of the review: every probe passes on the current code,
and the findings the probes cannot prove away (above all, the
unauthenticated webhook server) are recorded, not hidden.
"""

from __future__ import annotations

from sre_agent.cli import main
from sre_agent.security_review import SECURITY_FINDINGS, run_security_review

EXPECTED_IDS = [
    "unknown-runbook-denied",
    "free-form-command-refused",
    "target-stays-pinned",
    "audit-log-holds-no-secrets",
    "planted-secrets-redacted",
    "approval-single-use-and-rechecked",
    "tier3-limits-hold",
    "query-guards-hold",
    "no-shell-surface",
    "alert-text-cannot-close",
]


def test_all_probes_pass_with_current_code() -> None:
    report = run_security_review()
    assert report.total == 10
    assert [r.check_id for r in report.results] == EXPECTED_IDS
    assert report.passed_count == 10
    for result in report.results:
        assert result.passed, (result.check_id, result.failed_checks)


def test_probes_are_deterministic() -> None:
    first = run_security_review()
    second = run_security_review()
    assert [(r.check_id, r.passed) for r in first.results] == [
        (r.check_id, r.passed) for r in second.results
    ]


def test_findings_record_the_unauthenticated_server_first() -> None:
    report = run_security_review()
    assert report.findings == SECURITY_FINDINGS
    assert len(report.findings) == 8
    first = report.findings[0]
    assert first.id == "F-01"
    assert first.severity == "high"
    assert "no authentication" in first.title


def test_f04_status_records_the_post_release_fix() -> None:
    f04 = next(f for f in SECURITY_FINDINGS if f.id == "F-04")
    assert "fixed after v0.1.0" in f04.status
    assert "hash-chained" in f04.summary
    assert "whole file" in f04.summary


def test_f05_status_records_the_post_release_fix() -> None:
    f05 = next(f for f in SECURITY_FINDINGS if f.id == "F-05")
    assert "fixed after v0.1.0" in f05.status
    assert "https" in f05.summary
    assert "allow-list" in f05.summary
    assert "never from telemetry" in f05.summary


def test_f06_status_records_the_post_release_improvement() -> None:
    f06 = next(f for f in SECURITY_FINDINGS if f.id == "F-06")
    assert "improved after v0.1.0" in f06.status
    assert "non-root" in f06.summary
    assert "requirements.lock" in f06.summary
    assert "still pins" in f06.summary or "still pinned" in f06.summary


def test_f03_status_records_the_post_release_improvement() -> None:
    f03 = next(f for f in SECURITY_FINDINGS if f.id == "F-03")
    assert "improved after v0.1.0" in f03.status
    assert "JSON web tokens" in f03.summary
    assert "still does not catch" in f03.summary


def test_every_finding_has_a_severity_and_a_status() -> None:
    for finding in SECURITY_FINDINGS:
        assert finding.severity in {"high", "medium", "low", "info"}
        assert finding.status
        assert finding.summary


def test_rendered_report_shows_probes_and_findings() -> None:
    text = run_security_review().render_text()
    assert "Passed: 10" in text
    assert "F-01 [high] The webhook server has no authentication" in text
    assert "docs/security-review.md" in text
    assert "must not be shared" in text


def test_cli_security_check_prints_report_and_exits_zero(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["security-check"]) == 0
    out = capsys.readouterr().out
    assert "Passed: 10" in out
    assert "no-shell-surface" in out
    assert "F-01" in out


def test_cli_security_check_rejects_extra_arguments(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["security-check", "extra"]) == 1
    assert "no extra arguments" in capsys.readouterr().err


# --- Phase 12 restructure: probes and their data, same import path ----------


def test_security_review_is_a_package_split_into_probes_and_findings() -> None:
    import sre_agent.security_review as review_pkg

    assert hasattr(review_pkg, "__path__")  # a package, not one module


def test_each_half_lives_in_its_own_module() -> None:
    from sre_agent.security_review import findings, probes

    assert findings.SECURITY_FINDINGS is SECURITY_FINDINGS
    assert probes.run_security_review is run_security_review


def test_package_imports_are_the_same_objects_as_the_half_modules() -> None:
    import sre_agent.security_review as review_pkg
    from sre_agent.security_review import findings, probes

    assert review_pkg.SECURITY_FINDINGS is findings.SECURITY_FINDINGS
    assert review_pkg.SecurityFinding is findings.SecurityFinding
    assert review_pkg.run_security_review is probes.run_security_review
    assert review_pkg.SecurityReport is probes.SecurityReport
