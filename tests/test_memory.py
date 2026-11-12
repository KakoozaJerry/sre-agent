"""Tests for memory: retrieval over past incidents, per-runbook
stats counted from action records, and suggestions ranked on
similar incidents only.

Memory is advice, so these tests pin both directions: it finds
the right past and ranks the right fix first — and it invents
no track record, suggests no runbook outside the library, and
changes nothing when it answers.
"""

from __future__ import annotations

import pytest

from sre_agent.cli import main
from sre_agent.memory import IncidentMemory, demo_memory
from sre_agent.models import Evidence, Incident, IncidentStatus, Target

CHECKOUT = Target(environment="demo", namespace="shop", service="checkout")
SEARCH = Target(environment="demo", namespace="shop", service="search")


def _past(
    incident_id: str,
    target: Target,
    *,
    title: str = "Checkout errors are above 10%",
    runbook_id: str | None = "restart-workload",
    resolved: bool = True,
) -> Incident:
    incident = Incident(id=incident_id, title=title, target=target)
    incident.transition(IncidentStatus.TRIAGING)
    incident.diagnosis = "Most likely: checkout is failing on some requests"
    incident.transition(IncidentStatus.DIAGNOSED)
    if runbook_id is not None:
        incident.add_evidence(
            Evidence(
                kind="action",
                query=f"runbook:{runbook_id} on {target.service}/shop (demo)",
                summary=f"{runbook_id} ran on {target.service}.",
            )
        )
    if resolved:
        incident.transition(IncidentStatus.MITIGATING)
        incident.transition(IncidentStatus.RESOLVED)
    else:
        incident.transition(IncidentStatus.ESCALATED)
    return incident


def _query() -> Incident:
    return Incident(id="new-1", title="Checkout errors are above 10%", target=CHECKOUT)


def test_find_similar_ranks_same_service_first_and_explains_why() -> None:
    memory = IncidentMemory(
        [_past("past-search", SEARCH, title="Search errors"), _past("past-checkout", CHECKOUT)]
    )
    matches = memory.find_similar(_query())
    assert [m.incident_id for m in matches] == ["past-checkout", "past-search"]
    top = matches[0]
    # 4 service + 2 namespace + 1 environment + shared words (capped 3)
    assert top.score > matches[1].score
    assert any("same service (checkout)" in r for r in top.reasons)
    assert top.runbooks_run == ["restart-workload"]


def test_find_similar_never_returns_the_query_incident_itself() -> None:
    memory = IncidentMemory([_past("new-1", CHECKOUT), _past("past-1", CHECKOUT)])
    ids = [m.incident_id for m in memory.find_similar(_query())]
    assert ids == ["past-1"]


def test_find_similar_ignores_unrelated_incidents() -> None:
    other = Target(environment="prod", namespace="billing", service="payments")
    memory = IncidentMemory([_past("past-other", other, title="Payments latency in billing")])
    assert memory.find_similar(_query()) == []


def test_find_similar_respects_the_limit() -> None:
    memory = IncidentMemory([_past(f"past-{i}", CHECKOUT) for i in range(4)])
    assert len(memory.find_similar(_query(), limit=2)) == 2
    with pytest.raises(ValueError):
        memory.find_similar(_query(), limit=0)


def test_memory_stores_copies_history_cannot_be_rewritten() -> None:
    original = _past("past-1", CHECKOUT)
    memory = IncidentMemory([original])
    original.add_evidence(
        Evidence(
            kind="action",
            query="runbook:scale-replicas on checkout/shop (demo)",
            summary="a later edit must not enter memory",
        )
    )
    stats = {s.runbook_id: s for s in memory.runbook_stats()}
    assert set(stats) == {"restart-workload"}


def test_runbook_stats_count_action_records_only() -> None:
    incident = _past("past-1", CHECKOUT)
    incident_with_note = _past("past-2", CHECKOUT, runbook_id=None)
    incident_with_note.add_evidence(
        Evidence(kind="log", query="logs: ERROR checkout", summary="not a run")
    )
    memory = IncidentMemory([incident, incident_with_note])
    stats = memory.runbook_stats()
    assert len(stats) == 1
    assert stats[0].runbook_id == "restart-workload"
    assert stats[0].attempts == 1 and stats[0].ended_resolved == 1
    assert stats[0].success_rate == 1.0


def test_unresolved_incidents_count_as_not_resolved_not_failures() -> None:
    memory = IncidentMemory(
        [_past("past-1", CHECKOUT), _past("past-2", CHECKOUT, resolved=False)]
    )
    stats = memory.runbook_stats()[0]
    assert stats.attempts == 2
    assert stats.ended_resolved == 1 and stats.not_resolved == 1
    assert stats.success_rate == 0.5


def test_suggestions_rank_by_record_on_similar_incidents_only() -> None:
    memory = IncidentMemory(
        [
            _past("past-checkout-restart", CHECKOUT, runbook_id="restart-workload"),
            _past("past-checkout-scale", CHECKOUT, runbook_id="scale-replicas", resolved=False),
            # Scale worked on a different service; that must not lift
            # it for a checkout query.
            _past("past-search-scale", SEARCH, runbook_id="scale-replicas"),
            _past("past-search-scale-2", SEARCH, runbook_id="scale-replicas"),
        ]
    )
    suggestions = memory.suggest_runbooks(_query())
    assert [s.runbook_id for s in suggestions] == ["restart-workload", "scale-replicas"]
    restart, scale = suggestions
    assert restart.success_rate_on_similar == 1.0
    assert restart.supporting_incident_ids == ["past-checkout-restart"]
    assert scale.attempts_on_similar == 1 and scale.ended_resolved_on_similar == 0


def test_no_history_means_no_suggestion() -> None:
    assert IncidentMemory().suggest_runbooks(_query()) == []
    memory = IncidentMemory([_past("past-1", CHECKOUT, runbook_id=None)])
    assert memory.suggest_runbooks(_query()) == []


def test_suggestions_never_name_a_runbook_outside_the_library() -> None:
    memory = IncidentMemory([_past("past-1", CHECKOUT, runbook_id="delete-everything")])
    assert memory.suggest_runbooks(_query()) == []
    # …but the stats still report what the record actually holds.
    assert memory.runbook_stats()[0].runbook_id == "delete-everything"


def test_answering_changes_nothing_in_memory() -> None:
    memory = IncidentMemory([_past("past-1", CHECKOUT)])
    before = [i.model_dump() for i in memory.incidents()]
    memory.report(_query())
    assert [i.model_dump() for i in memory.incidents()] == before


def test_demo_memory_report_has_the_seeded_numbers() -> None:
    report = demo_memory()
    assert report.past_incidents_held == 4
    # The two past incidents whose titles match the query's word
    # for word score highest; the first checkout incident, whose
    # title differs, follows; the search incident ranks last.
    assert [m.incident_id for m in report.matches] == [
        "past-checkout-2",
        "past-checkout-3",
        "past-checkout-1",
        "past-search-1",
    ]
    stats = {s.runbook_id: s for s in report.overall_stats}
    assert stats["restart-workload"].attempts == 3
    assert stats["restart-workload"].ended_resolved == 3
    assert stats["scale-replicas"].attempts == 1
    assert stats["scale-replicas"].ended_resolved == 0
    assert [s.runbook_id for s in report.suggestions] == [
        "restart-workload",
        "scale-replicas",
    ]
    text = report.render_text()
    assert "advice only" in text
    assert "does not by itself" in text


def test_cli_memory_demo_prints_report_and_exits_zero(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["memory-demo"]) == 0
    out = capsys.readouterr().out
    assert "Memory report" in out
    assert "restart-workload" in out


def test_cli_memory_demo_rejects_extra_arguments(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["memory-demo", "extra"]) == 1
    assert "no extra arguments" in capsys.readouterr().err


def test_demo_incident_memory_holds_the_seeded_past() -> None:
    from sre_agent.memory import demo_incident_memory

    memory = demo_incident_memory()
    assert len(memory) == 4
    assert [i.id for i in memory.incidents()] == [
        "past-checkout-1",
        "past-checkout-2",
        "past-checkout-3",
        "past-search-1",
    ]
    # The factory and the demo report describe the same past.
    assert demo_memory().past_incidents_held == len(memory)
