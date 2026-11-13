## What this changes, in plain words

<!-- One or two sentences a reader who has never seen the code can follow. -->

## Why

<!-- The roadmap item, issue, or bug this addresses. Link it if there is one. -->

## Checks — what each one printed

<!-- Paste the result line of each check, not just "all green". A change that touches scoring must show the before and after numbers. -->

- [ ] `pytest` —
- [ ] `ruff check src tests` —
- [ ] `mypy src` —
- [ ] `sre-agent evaluate` —
- [ ] `sre-agent regression-check` —
- [ ] `sre-agent injection-check` —
- [ ] `sre-agent tier3-check` —
- [ ] `sre-agent security-check` —

## Safety and honesty

- [ ] No free-form command surface was added; fixes stay allow-listed runbooks.
- [ ] Telemetry is still treated as untrusted input.
- [ ] Nothing is claimed that a test or a scored suite has not measured; any limit this change ships with is stated in the docs.
- [ ] The README or the guide this change touches was updated, and a short entry was added to `docs/progress.md`.
- [ ] The regression baseline was **not** updated (it moves only on purpose, at a release).
