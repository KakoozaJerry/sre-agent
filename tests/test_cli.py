"""CLI v1: list incidents, show one in full, run the demo diagnosis.

The CLI talks to the running server, so these tests pass a fake
``fetch`` that returns the same shapes the server serves (see
test_server.py for the real endpoints). No server is started.
"""

import pytest

from sre_agent.cli import CliError, format_incident_detail, main
from sre_agent.reasoning import demo_diagnosis

SUMMARY_ROW = {
    "id": "inc-1",
    "title": "Checkout errors are above 10%",
    "status": "triaging",
    "target": {"environment": "demo", "namespace": "shop", "service": "checkout"},
    "evidence_count": 1,
    "duplicate_count": 2,
}

DETAIL = {
    "id": "inc-1",
    "title": "Checkout errors are above 10%",
    "status": "diagnosed",
    "target": {"environment": "demo", "namespace": "shop", "service": "checkout"},
    "created_at": "2026-11-09T00:00:00Z",
    "updated_at": "2026-11-09T00:05:00Z",
    "duplicate_count": 2,
    "diagnosis": "Most likely: a call checkout depends on is timing out",
    "hypotheses": [
        {
            "statement": "A call that checkout depends on is timing out",
            "confidence": 0.85,
            "supporting_evidence": ["ev-1", "ev-2"],
            "contradicting_evidence": [],
        }
    ],
    "evidence": [
        {
            "id": "ev-1",
            "kind": "log",
            "query": "log search: service=checkout level=ERROR",
            "summary": "3 error log line(s) matched",
        }
    ],
}


def _fetch_returning(payload, seen_urls=None):
    def fetch(url):
        if seen_urls is not None:
            seen_urls.append(url)
        return payload

    return fetch


def _fetch_raising(error):
    def fetch(url):
        raise error

    return fetch


# --- basics -------------------------------------------------------------------


def test_version(capsys):
    assert main(["--version"]) == 0
    assert "sre-agent 0.1.0" in capsys.readouterr().out


def test_no_args_prints_usage(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "incidents" in out and "show" in out and "diagnose" in out


def test_unknown_command_is_an_error(capsys):
    assert main(["frobnicate"]) == 2
    assert "unknown command" in capsys.readouterr().err


# --- incidents ------------------------------------------------------------------


def test_incidents_lists_what_the_server_returns(capsys):
    urls: list[str] = []
    code = main(["incidents"], fetch=_fetch_returning([SUMMARY_ROW], urls))
    assert code == 0
    assert urls == ["http://127.0.0.1:8080/incidents"]
    out = capsys.readouterr().out
    assert "1 incident(s)" in out
    assert "inc-1" in out and "triaging" in out and "checkout/shop" in out
    assert "duplicates: 2" in out


def test_incidents_empty_list_says_so_plainly(capsys):
    assert main(["incidents"], fetch=_fetch_returning([])) == 0
    assert "No incidents" in capsys.readouterr().out


def test_incidents_honours_base_url_flag(capsys):
    urls: list[str] = []
    code = main(
        ["incidents", "--base-url", "http://localhost:9999/"],
        fetch=_fetch_returning([], urls),
    )
    assert code == 0
    assert urls == ["http://localhost:9999/incidents"]


def test_unreachable_server_is_a_plain_error_not_an_empty_list(capsys):
    fetch = _fetch_raising(CliError("could not reach the agent server"))
    assert main(["incidents"], fetch=fetch) == 1
    captured = capsys.readouterr()
    assert "could not reach" in captured.err
    assert "No incidents" not in captured.out


def test_incidents_rejects_a_non_list_answer(capsys):
    assert main(["incidents"], fetch=_fetch_returning({"nope": 1})) == 1
    assert "not with a list" in capsys.readouterr().err


# --- show -----------------------------------------------------------------------


def test_show_prints_diagnosis_causes_and_evidence_queries(capsys):
    urls: list[str] = []
    code = main(["show", "inc-1"], fetch=_fetch_returning(DETAIL, urls))
    assert code == 0
    assert urls == ["http://127.0.0.1:8080/incidents/inc-1"]
    out = capsys.readouterr().out
    assert "Diagnosis: Most likely" in out
    assert "timing out" in out and "confidence 0.85" in out
    # The exact query behind the evidence is shown, so it can be re-run.
    assert "log search: service=checkout level=ERROR" in out


def test_show_without_a_diagnosis_says_none_yet():
    detail = {**DETAIL, "diagnosis": None, "hypotheses": [], "evidence": []}
    text = format_incident_detail(detail)
    assert "Diagnosis: none yet" in text
    assert "No evidence recorded yet" in text


def test_show_unknown_incident_is_a_plain_error(capsys):
    fetch = _fetch_raising(CliError("the server says that incident does not exist"))
    assert main(["show", "nope"], fetch=fetch) == 1
    assert "does not exist" in capsys.readouterr().err


def test_show_needs_exactly_one_id(capsys):
    assert main(["show"], fetch=_fetch_returning(DETAIL)) == 1
    assert "exactly one incident id" in capsys.readouterr().err


# --- diagnose -------------------------------------------------------------------


def test_diagnose_runs_the_demo_locally(capsys):
    code = main(["diagnose"], run_demo_diagnosis=demo_diagnosis)
    assert code == 0
    out = capsys.readouterr().out
    assert "seeded demo data" in out
    assert "Outcome: diagnosed" in out
    assert "evidence: promql: http_request_errors_total" in out


def test_diagnose_rejects_extra_arguments(capsys):
    assert main(["diagnose", "inc-1"]) == 1
    assert "no extra arguments" in capsys.readouterr().err


# --- end to end through the real server app --------------------------------------


def test_cli_reads_a_real_incident_from_the_server_app(capsys):
    """Wire the CLI's fetch to the FastAPI app in-process: an alert
    goes in through the webhook, and the CLI lists and shows it."""
    from fastapi.testclient import TestClient

    from sre_agent.ingestion import IncidentStore
    from sre_agent.server import create_app

    client = TestClient(create_app(IncidentStore()))
    created = client.post(
        "/webhooks/generic",
        json={
            "alert_name": "HighErrorRate",
            "service": "checkout",
            "namespace": "shop",
            "environment": "demo",
            "severity": "critical",
            "summary": "Checkout errors are above 10%",
            "fingerprint": "abc123",
        },
    ).json()

    def fetch(url):
        path = url.removeprefix("http://127.0.0.1:8080")
        response = client.get(path)
        if response.status_code == 404:
            raise CliError("the server says that incident does not exist")
        return response.json()

    assert main(["incidents"], fetch=fetch) == 0
    assert created["incident_id"] in capsys.readouterr().out

    assert main(["show", created["incident_id"]], fetch=fetch) == 0
    out = capsys.readouterr().out
    assert "Checkout errors are above 10%" in out
    assert "Diagnosis: none yet" in out  # the server does not diagnose yet


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
