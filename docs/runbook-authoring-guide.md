# Runbook authoring guide

How to write a new runbook — a fixed, pre-written fix the agent
may propose — and how to change an existing one safely.

The rule that shapes everything below: **a runbook is never a
command.** There is no shell anywhere in this project, and a
runbook is not a script a caller passes in. It is a named,
fixed recipe in code, with a named, checked list of parameters,
that only runs after the policy engine allows it. If a fix
cannot be expressed that way, it does not belong in the library
yet.

## The library today

Three runbooks, all reversible, all for demo targets only
(`src/sre_agent/runbooks.py`):

| Runbook | What it does | Parameters |
|---|---|---|
| `restart-workload` | Restarts the workload; clears its failing state and counts the restart | none |
| `scale-replicas` | Sets how many copies of the workload run | `replicas`: a whole number, 1 to 5 |
| `rollback-deployment` | Goes back to the previous version; refused when there is none | none |

## The four places a runbook must agree

A runbook exists in four places, and they must name the same
thing. A test checks the first two match exactly; the rest is
on you and your tests.

1. **The policy catalogue** — `RUNBOOK_CATALOG` in
   `src/sre_agent/policy.py`. This is the allow-list. Name the
   runbook here, with its class: `READ_ONLY` if it changes
   nothing, `REVERSIBLE_MUTATION` if it changes something that
   can be undone. A runbook that is not in the catalogue is
   **denied outright** by the policy engine — never sent to a
   human for approval. There is no third class, on purpose:
   a fix that cannot be undone does not get a class until the
   safety model grows one, with its own review.
2. **The parameter list** — `RUNBOOK_PARAMETERS` in
   `runbooks.py`. Name every parameter the runbook takes; an
   empty set means it takes none. Any parameter not named here
   is refused, including one that tries to carry a free-form
   command.
3. **The validation** — `validate_parameters` in `runbooks.py`.
   Check each parameter's type and range here, and raise
   `RunbookError` with a plain reason for anything that must
   not run. One trap, already handled for `replicas` and worth
   copying: in Python `True` is a kind of integer, so a boolean
   must be refused explicitly where a count is expected.
4. **The executor** — `RunbookExecutor.execute` in
   `runbooks.py`. The branch that actually does the work,
   against the workload backend. It receives the action only
   after validation has run again at its own door, and only
   with an ALLOW decision in hand.

## Write a new runbook, step by step

1. **Say what it fixes, and how it is undone.** Write both down
   in the runbook's entry in the module docstring of
   `runbooks.py`. If you cannot name the undo, stop: the
   catalogue has no class for it.
2. **Add it to the catalogue** (place 1 above) with the
   matching class.
3. **Add its parameter list** (place 2). Keep it as small as
   the fix allows — every parameter is a thing a proposer can
   get wrong and a thing validation must check. No parameter
   may be a command, a script, a file path the fix will run,
   or free-form text that a backend would interpret.
4. **Add its validation** (place 3). Types, ranges, and
   required parameters, each refusal with a plain reason.
5. **Work out its blast radius honestly.** In `build_action`,
   decide how many copies the action touches, from the
   workload's real state — never from what the proposer claims.
   A restart or rollback touches every running copy; a scale
   touches the difference between what runs and what was asked
   for. The policy engine's size check is only as honest as
   this number, so a new runbook that touches something else —
   say, one copy at a time — must count that, in code, with a
   test.
6. **Add the executor branch** (place 4). Work against the
   backend interface, not around it. Record the before and
   after state the existing branches record (copies running,
   version, failing, restarts — or the equivalent for your
   fix), and write the plain one-line summary a person will
   read in the incident record. If the fix cannot run — like
   a rollback with no previous version — raise `RunbookError`
   and change nothing.
7. **Seed it in the demo, if it should be triable there.**
   `demo_runbooks()` builds the demo workloads; a runbook that
   no seeded workload can exercise cannot be shown, approved,
   or drilled, and the guide for it is just words.
8. **Decide its tier, in writing.** A new mutation starts at
   Tier 2: a human approves each run. It joins the Tier 3
   pre-approved set (`tier3.py`) only as a deliberate, separate
   change, with the grant, its limits and its drills updated
   together — the way rollback was deliberately kept out of
   the demo's Tier 3 grant because it changes which version
   runs.
9. **Write the tests** (below), then **update the docs**: this
   guide's table, [safety-model.md](safety-model.md),
   the README if the library's shape changed, and
   [progress.md](progress.md).

## The tests a new runbook must have

In `tests/test_runbooks.py` style, at minimum:

- The library and the policy catalogue name the same set
  (the existing test covers this for free — it fails until
  place 1 and place 2 agree).
- Each parameter is checked: a missing required one, a wrong
  type, an out-of-range value, and an extra, unnamed parameter
  are all refused — once when the proposal is built, and again
  through the executor, so a hand-made proposal cannot skip
  the checks.
- A proposal's `replicas_affected` matches what the action
  would really touch, from the workload's state.
- The executor refuses a decision that is not ALLOW — denied,
  and still waiting for approval — and changes nothing.
- A successful run changes exactly what the summary says, and
  its result becomes an evidence record naming the runbook and
  its target.
- The cannot-run case (like rollback with no previous version)
  is refused and changes nothing.

And the wider suites must stay green, because they guard the
paths a new runbook joins: the policy tests, the approval
tests, the Tier 3 drills, the security probes (which include a
scan proving no module can run a shell) and the evaluation
suite. Run everything: `pytest`, plus `ruff check` and
`mypy` on the package.

## Changing an existing runbook

- **Widening a parameter range** (say, scale to 10 copies) is
  a safety change, not a detail: the runbook's own limits and
  each environment's blast-radius limit are separate checks,
  and both must still make sense together. Update both
  validations' tests and say why in [progress.md](progress.md).
- **Renaming a runbook** renames it in the catalogue, the
  parameter list, the executor, memory's records and every
  doc — and orphans the runbook statistics memory keeps under
  the old name. Prefer adding a new runbook and retiring the
  old one in the open, over a silent rename.
- **Removing a runbook** means removing it from the catalogue
  first: from that moment the policy engine denies it, which
  is the safe direction. Then remove the rest, and keep the
  removal noted in [progress.md](progress.md).

## What never to do

- Never add a parameter that is, or could carry, a shell
  command, a script, or code to evaluate. The security review's
  no-shell scan must keep finding nothing.
- Never let a proposer supply the blast-radius number, a
  target taken from alert or log text, or a parameter the
  runbook did not name.
- Never give a new runbook a Tier 3 grant in the same change
  that introduces it. It earns bounded autonomy later, in its
  own change, with drills.
- Never claim a runbook works against a real system until it
  has run against one. Today's library acts on in-memory demo
  workloads behind an interface; a real backend sits behind
  the same shape later and must pass the same validation and
  the same policy gate.
