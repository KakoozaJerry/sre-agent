# Progress log — in plain English

What has been built, what changed, and what is next. Newest first. Kept deliberately readable: if you only read one file to follow this project, read this one.

## 8 November 2026 (night) — Phase 1 starts: the agent can receive alerts

- The agent has a front door now. Monitoring tools can send it alerts two ways: the shape Prometheus Alertmanager already sends, and a simple "generic" shape (one alert as a small piece of JSON) for tools that do not speak Alertmanager.
- Each alert becomes an incident — the agent's record of a problem — and the incident goes straight into "triaging", which means the agent has started looking at it. The alert itself is kept on the record as the first piece of evidence.
- Alerts repeat a lot in real life, so two rules keep the record clean. If the exact same alert fires again, it is counted as a copy (a "duplicate") on the same incident instead of opening a new one. If a different alert arrives for the same service with the same alert name, it joins the open incident as extra evidence ("grouping") instead of starting a second file for the same problem.
- When an alert says it has resolved, the incident closes. If the same problem fires again later, it opens a fresh incident, as it should.
- For now the incidents are kept in memory, behind a small, simple store. A database comes later; the way alerts arrive will not change when it does.
- There are also simple ways to look at what the agent knows: a health check, a list of incidents, and the full record for one incident.
- Fourteen new tests cover all of this. All 33 tests pass, including the safety tests, and the style and type checks are clean.
- Next in Phase 1: the agent learns to read evidence — metrics, logs, and service status — still looking only, never touching anything.

## 8 November 2026 (evening) — Phase 0 is finished: logging is in

- The last missing piece of the basic skeleton was logging — a clear record of what the agent does while it works. That is now in place. Set it up once, and every part of the agent writes log lines the same way: one JSON object per line, with the time, how serious the line is, which part wrote it, and the message. A person can read it, and a log tool can search it.
- It is safe to set up twice (no doubled lines), and a misspelled level like "chatty" is rejected with a clear error instead of being silently ignored.
- Five new tests cover this. All 19 tests now pass, including the safety tests, and the style check is clean.
- With that, Phase 0 — the foundation — is complete and ticked off in the plan: repository, licence and README; the plan itself; the design and safety docs; the project skeleton; and the incident record with its life cycle, all with tests.
- One honest correction to the plan: it said the automatic checks (CI) include a type check. They do not yet — CI runs the style check and the tests. The type checker is in the developer tools and runs locally for now; the plan now says exactly that.
- Next: Phase 1 starts — the agent learns to receive alerts.

## 8 November 2026 (later) — the project is public, and CI is green

- The repository went live: github.com/KakoozaJerry/sre-agent. Abraham added it to the Muse app's access himself; the plan, the plain-English docs, the first code and the tests are all in.
- The first automatic check (CI) caught three small style problems in the code before anything else ran — exactly what it is there for. They were fixed, and run #4 of CI passed cleanly: lint green, all 14 tests green, including the safety tests.
- Two honest footnotes: the bulk-upload tool cannot write to this repo (a GitHub app limitation), so files go in one at a time; and the CI workflow file had to be added through the website for the same reason. Neither changes the result.
- Next: Phase 0 finishes with the remaining skeleton checks, then Phase 1 — the agent learns to receive alerts and read metrics and logs (looking only, nothing touched).

## 8 November 2026 — project launched

- Abraham set the direction: build an SRE agent in the open, document everything plainly, treat income as a long-term goal (open-core: the core stays free; paid services and an enterprise tier may come later), and let the public record itself be the proof of reliability.
- The full plan was written ([PLAN.md](../PLAN.md)): what the agent does, how it is kept safe, the roadmap in phases, and how it will be measured.
- The first code landed: the incident record and its life cycle (open → triaging → diagnosed → fixing → resolved), and the safety policy engine that decides what the agent may do on its own, what needs a human's approval, and what is never allowed. Both come with tests, and the safety tests must pass before any change can merge.
- Next: the repository goes live on GitHub, then the agent learns to receive alerts and read metrics and logs (still without touching anything — looking only, until looking is proven good).
