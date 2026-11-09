"""Memory: past incidents, found again; runbook stats, kept honestly.

In plain words, this is the agent's memory of its own past work.
When a new incident arrives, an on-call engineer asks "have we
seen this before, and what fixed it last time?" This module is
that question, in code. It has three parts:

- **Storage** (``IncidentMemory``). Past incidents are kept in
  memory, behind a small interface — the same pattern the
  incident store, the tools and the runbooks use. Records are
  stored as copies and handed back as match summaries, so a
  caller cannot rewrite history by editing what it was given. A
  database behind the same shape comes later, when incidents
  themselves are stored in one.

- **Retrieval** (``find_similar``). A new incident is scored
  against each past one with fixed, explainable points: the
  same service counts most, then the same namespace, then the
  same environment, then words shared between the two records'
  titles, diagnoses and recorded possible causes (a few points
  at most, so wording can never outweigh the target). Every
  match lists the reasons for its score, in plain words. There
  is no model and no embedding here, on purpose — the same
  choice the reasoning loop made — so every ranking can be
  checked exactly in a test. A past record of the query incident
  itself (same id) is never returned as its own match.

- **Runbook statistics and suggestions** (``runbook_stats``,
  ``suggest_runbooks``). Every runbook run leaves an action
  record on its incident (``runbooks.py`` builds it), so the
  stats are counted from those records, never from a separate
  tally that could drift. A run counts as "ended resolved" when
  its incident's final status is resolved, and as "not resolved"
  otherwise. That wording is deliberate, and it is this pass's
  one design lesson: the record shows the incident *ended*
  resolved after the run — it does not prove the run *caused*
  the resolution, so the stats never claim it did. An incident
  that is still open, or was escalated to a human, is counted as
  not resolved, never as a failure of the runbook.

How suggestions use the stats — the "influence on proposals"
the roadmap asks for, kept inside its proper bounds:

- Suggestions are ranked on the stats of *same-service* past
  incidents only. Retrieval also returns neighbours — the same
  namespace, a similar title — because a human may learn from
  them; but a fix's track record may only influence a proposal
  for the service it actually ran on, so neighbours never count
  toward a suggestion.
- A suggestion is advice, Tier 1: it names the runbook, its
  record on similar incidents, and the past incidents behind
  that record. It runs nothing, proposes nothing through the
  policy engine, and approves nothing — a human or a later
  proposal step still takes any suggested fix through the
  normal Tier 2 approval or Tier 3 grant. Memory can inform a
  proposal; it can never become one by itself. (The
  architecture doc already states this rule.)
- Only runbooks in the runbook library are ever suggested, and
  only when at least one similar past incident actually ran
  them. No history, no suggestion — memory never invents a
  track record.

Scope, stated honestly: memory here is in-process and seeded
for the demo (``demo_memory``). It is not wired into the
reasoning loop or the live server yet — suggestions are
reported, through ``sre-agent memory-demo`` and this module's
report, not yet fed into a live diagnosis. Nothing here claims
otherwise.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from .logging_config import get_logger
from .models import Evidence, Incident, IncidentStatus, Target
from .runbooks import RUNBOOK_PARAMETERS

log = get_logger(__name__)

# The fixed retrieval points. Same service is the strongest
# signal — "last time this service broke" is the question being
# answered — then where it lives, then the words the two records
# share, capped so wording can never outweigh the target.
POINTS_SAME_SERVICE = 4
POINTS_SAME_NAMESPACE = 2
POINTS_SAME_ENVIRONMENT = 1
POINTS_PER_SHARED_WORD = 1
MAX_WORD_POINTS = 3
# A match must be worth reporting: same environment alone (1) or
# a couple of shared words alone is not a memory, it is a guess.
MIN_MATCH_SCORE = 3

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "is", "it", "of", "on", "or", "the", "to", "was", "were",
    "with", "this", "that", "its",
}


def _words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOP_WORDS and len(w) > 2}


def _record_words(incident: Incident) -> set[str]:
    words = _words(incident.title)
    if incident.diagnosis:
        words |= _words(incident.diagnosis)
    for hyp in incident.hypotheses:
        words |= _words(hyp.statement)
    return words


def _action_runbook_ids(incident: Incident) -> list[str]:
    """The runbooks that ran on *incident*, from its own records.

    A runbook run leaves an evidence record of kind ``action``
    whose query begins ``runbook:<id> `` (built in
    ``runbooks.py``). Anything else is not a run and is ignored.
    """
    ids: list[str] = []
    for ev in incident.evidence:
        if ev.kind != "action" or not ev.query.startswith("runbook:"):
            continue
        runbook_id = ev.query.removeprefix("runbook:").split(" ", 1)[0].strip()
        if runbook_id:
            ids.append(runbook_id)
    return ids


class PastIncidentMatch(BaseModel):
    """One past incident that looks like the query, and why."""

    incident_id: str
    title: str
    target: Target
    status: IncidentStatus
    diagnosis: str | None = None
    score: int
    reasons: list[str] = Field(default_factory=list)
    runbooks_run: list[str] = Field(default_factory=list)


class RunbookStats(BaseModel):
    """One runbook's record, counted from incident action records."""

    runbook_id: str
    attempts: int = 0
    ended_resolved: int = 0
    not_resolved: int = 0

    @property
    def success_rate(self) -> float:
        """Share of runs whose incident ended resolved (0 if none)."""
        return self.ended_resolved / self.attempts if self.attempts else 0.0


class RunbookSuggestion(BaseModel):
    """Advice only: a runbook worth considering, and its record.

    Carrying a suggestion changes nothing and runs nothing; any
    fix still goes through the policy engine and its tier gate.
    """

    runbook_id: str
    attempts_on_similar: int
    ended_resolved_on_similar: int
    success_rate_on_similar: float
    supporting_incident_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class MemoryReport(BaseModel):
    """What memory can say about one new incident, in one report."""

    query_incident_id: str
    query_target: Target
    past_incidents_held: int
    matches: list[PastIncidentMatch] = Field(default_factory=list)
    overall_stats: list[RunbookStats] = Field(default_factory=list)
    suggestions: list[RunbookSuggestion] = Field(default_factory=list)

    def render_text(self) -> str:
        """The report as plain text a human can check."""
        target = self.query_target
        lines = [
            (
                "Memory report (past incidents held in memory; advice only — "
                "nothing here runs or approves a fix)"
            ),
            (
                f"Query: incident {self.query_incident_id} — "
                f"{target.service}/{target.namespace} ({target.environment})"
            ),
            f"Past incidents held: {self.past_incidents_held}",
            "Similar past incidents, most similar first:",
        ]
        if not self.matches:
            lines.append("- None similar enough to report.")
        for match in self.matches:
            lines.append(
                f"- {match.incident_id} [{match.status.value}] score {match.score}: "
                f"{match.title} — {'; '.join(match.reasons)}"
            )
            if match.diagnosis:
                lines.append(f"  diagnosis then: {match.diagnosis}")
            if match.runbooks_run:
                lines.append(f"  runbook(s) run then: {', '.join(match.runbooks_run)}")
        lines.append("Runbook record across all past incidents held:")
        if not self.overall_stats:
            lines.append("- No runbook runs are recorded on any past incident.")
        for stats in self.overall_stats:
            lines.append(
                f"- {stats.runbook_id}: {stats.attempts} run(s), "
                f"{stats.ended_resolved} on incidents that ended resolved, "
                f"{stats.not_resolved} not resolved "
                f"(resolved share {stats.success_rate:.0%})"
            )
        lines.append(
            "Suggested runbooks for this incident (from same-service past incidents only):"
        )
        if not self.suggestions:
            lines.append("- None: no similar past incident ran a runbook.")
        for suggestion in self.suggestions:
            lines.append(f"- {suggestion.runbook_id}: {suggestion.reason}")
        lines.append(
            "Note: 'ended resolved' means the incident's final status was "
            "resolved after the run was recorded; it does not by itself "
            "prove the run caused the resolution."
        )
        return "\n".join(lines)


def _score(query: Incident, past: Incident) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if past.target.service == query.target.service:
        score += POINTS_SAME_SERVICE
        reasons.append(f"same service ({query.target.service})")
    if past.target.namespace == query.target.namespace:
        score += POINTS_SAME_NAMESPACE
        reasons.append(f"same namespace ({query.target.namespace})")
    if past.target.environment == query.target.environment:
        score += POINTS_SAME_ENVIRONMENT
        reasons.append(f"same environment ({query.target.environment})")
    shared = sorted(_record_words(query) & _record_words(past))
    if shared:
        points = min(len(shared) * POINTS_PER_SHARED_WORD, MAX_WORD_POINTS)
        score += points
        shown = ", ".join(shared[:MAX_WORD_POINTS])
        reasons.append(f"shared words in the records: {shown} (+{points})")
    return score, reasons


def _stats_for(incidents: list[Incident]) -> list[RunbookStats]:
    by_id: dict[str, RunbookStats] = {}
    for incident in incidents:
        for runbook_id in _action_runbook_ids(incident):
            stats = by_id.setdefault(runbook_id, RunbookStats(runbook_id=runbook_id))
            stats.attempts += 1
            if incident.status is IncidentStatus.RESOLVED:
                stats.ended_resolved += 1
            else:
                stats.not_resolved += 1
    return [by_id[key] for key in sorted(by_id)]


class IncidentMemory:
    """Past incidents, stored as copies, behind a small interface."""

    def __init__(self, incidents: list[Incident] | None = None) -> None:
        self._incidents: list[Incident] = []
        for incident in incidents or []:
            self.add(incident)

    def __len__(self) -> int:
        return len(self._incidents)

    def add(self, incident: Incident) -> None:
        """Store a copy of *incident* as a past incident."""
        self._incidents.append(incident.model_copy(deep=True))

    def incidents(self) -> list[Incident]:
        """Copies of everything held, oldest first."""
        return [incident.model_copy(deep=True) for incident in self._incidents]

    def find_similar(self, query: Incident, *, limit: int = 5) -> list[PastIncidentMatch]:
        """Past incidents similar to *query*, most similar first.

        The query incident itself is never its own match. Only
        scores at or above ``MIN_MATCH_SCORE`` are reported.
        Ties are broken by incident id, so the order is stable
        and a test can check it exactly.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        matches: list[PastIncidentMatch] = []
        for past in self._incidents:
            if past.id == query.id:
                continue
            score, reasons = _score(query, past)
            if score < MIN_MATCH_SCORE:
                continue
            matches.append(
                PastIncidentMatch(
                    incident_id=past.id,
                    title=past.title,
                    target=past.target,
                    status=past.status,
                    diagnosis=past.diagnosis,
                    score=score,
                    reasons=reasons,
                    runbooks_run=_action_runbook_ids(past),
                )
            )
        matches.sort(key=lambda m: (-m.score, m.incident_id))
        return matches[:limit]

    def runbook_stats(self) -> list[RunbookStats]:
        """Every runbook's record across all past incidents held."""
        return _stats_for(self._incidents)

    def suggest_runbooks(self, query: Incident, *, limit: int = 3) -> list[RunbookSuggestion]:
        """Runbooks worth considering for *query*, from same-service past.

        Only matches on the query's own service count toward a
        suggestion (see the module note: neighbours are shown for
        reading, never counted here). Ranked by resolved share on
        those incidents, then by how many runs back that share,
        then by runbook id — a fixed order a test can check.
        Advice only: this builds no proposal and runs nothing.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        matches = self.find_similar(query, limit=len(self._incidents) or 1)
        match_ids = {
            m.incident_id for m in matches if m.target.service == query.target.service
        }
        similar = [i for i in self._incidents if i.id in match_ids]
        suggestions: list[RunbookSuggestion] = []
        for stats in _stats_for(similar):
            if stats.runbook_id not in RUNBOOK_PARAMETERS:
                continue
            supporting = [
                incident.id
                for incident in similar
                if stats.runbook_id in _action_runbook_ids(incident)
            ]
            suggestions.append(
                RunbookSuggestion(
                    runbook_id=stats.runbook_id,
                    attempts_on_similar=stats.attempts,
                    ended_resolved_on_similar=stats.ended_resolved,
                    success_rate_on_similar=stats.success_rate,
                    supporting_incident_ids=sorted(supporting),
                    reason=(
                        f"on same-service past incidents it ran {stats.attempts} time(s) and "
                        f"{stats.ended_resolved} of those incidents ended resolved "
                        f"({stats.success_rate:.0%}); past incident(s): "
                        f"{', '.join(sorted(supporting))}"
                    ),
                )
            )
        suggestions.sort(
            key=lambda s: (-s.success_rate_on_similar, -s.attempts_on_similar, s.runbook_id)
        )
        return suggestions[:limit]

    def report(self, query: Incident) -> MemoryReport:
        """One report: similar past, overall stats, suggestions."""
        report = MemoryReport(
            query_incident_id=query.id,
            query_target=query.target,
            past_incidents_held=len(self._incidents),
            matches=self.find_similar(query),
            overall_stats=self.runbook_stats(),
            suggestions=self.suggest_runbooks(query),
        )
        log.info(
            "memory report for incident %s: %d match(es), %d suggestion(s)",
            query.id,
            len(report.matches),
            len(report.suggestions),
        )
        return report


def _past_incident(
    incident_id: str,
    *,
    service: str,
    title: str,
    diagnosis: str,
    runbook_id: str,
    resolved: bool,
) -> Incident:
    """One seeded past incident, walked through its real life cycle."""
    target = Target(environment="demo", namespace="shop", service=service)
    incident = Incident(id=incident_id, title=title, target=target)
    incident.transition(IncidentStatus.TRIAGING)
    incident.diagnosis = diagnosis
    incident.transition(IncidentStatus.DIAGNOSED)
    incident.add_evidence(
        Evidence(
            kind="action",
            query=f"runbook:{runbook_id} on {service}/shop (demo)",
            summary=f"{runbook_id} ran on {service} in the demo.",
        )
    )
    if resolved:
        incident.transition(IncidentStatus.MITIGATING)
        incident.transition(IncidentStatus.RESOLVED)
    else:
        incident.transition(IncidentStatus.ESCALATED)
    return incident


def demo_memory() -> MemoryReport:
    """A seeded memory report for a new checkout incident.

    The past is four demo incidents: checkout broke twice and a
    restart was run both times, and both incidents ended
    resolved; checkout broke once more, a scale was run, and
    that incident was escalated, not resolved; and the search
    service broke once, a restart was run, and it ended
    resolved. The query is a fresh checkout incident, so the
    report shows retrieval favouring the same service, overall
    restart stats of 3 runs and 3 resolved, and a suggestion
    order — restart before scale — that comes only from the
    same-service (checkout) past, not from the search record.
    """
    memory = IncidentMemory(
        [
            _past_incident(
                "past-checkout-1",
                service="checkout",
                title="HighErrorRate on checkout",
                diagnosis="Most likely: a call that checkout depends on is timing out",
                runbook_id="restart-workload",
                resolved=True,
            ),
            _past_incident(
                "past-checkout-2",
                service="checkout",
                title="Checkout errors are above 10%",
                diagnosis="Most likely: checkout is failing on some requests",
                runbook_id="restart-workload",
                resolved=True,
            ),
            _past_incident(
                "past-checkout-3",
                service="checkout",
                title="Checkout errors are above 10%",
                diagnosis="Most likely: checkout is failing on some requests",
                runbook_id="scale-replicas",
                resolved=False,
            ),
            _past_incident(
                "past-search-1",
                service="search",
                title="Search errors are above 10%",
                diagnosis="Most likely: search is failing on some requests",
                runbook_id="restart-workload",
                resolved=True,
            ),
        ]
    )
    query = Incident(
        id="new-checkout-1",
        title="Checkout errors are above 10%",
        target=Target(environment="demo", namespace="shop", service="checkout"),
    )
    return memory.report(query)
