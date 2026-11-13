# Contributing to sre-agent

Thank you for wanting to help. This project is built in the
open, and its public record is its proof of reliability — so
contributions are welcome, and they are held to the same
standard as everything else here: plain words, real tests, and
no claim the checks have not measured.

This guide is written from the project as it actually is. If a
command below does not work, that is a bug — please open an
issue (see "Report a problem" below).

## Before you start

- Read the [README](README.md) first, especially "In plain
  English" and "What it cannot do yet". Then, if you want the
  whole picture, read [PLAN.md](PLAN.md) and the running log in
  [docs/progress.md](docs/progress.md).
- Be kind. The [code of conduct](CODE_OF_CONDUCT.md) applies
  in every issue, pull request and discussion.

## Set up

You need Python 3.11 or newer. From the top folder of your
clone:

```bash
./quickstart.sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server]"
pytest
```

The first command is the one-command install for users; the
next three are the development install — the `dev` part brings
the test tools, the `server` part the web server. When `pytest`
is green you are ready. If the safety tests are red, do not
work around them: a broken guardrail is the most important bug
this project can have.

## Run the checks

Every change must pass all of these before it is proposed:

```bash
pytest                                # the full test suite, safety suite included
ruff check src tests                  # style
mypy src                              # types
sre-agent evaluate                    # five chaos scenarios: 5 of 5, 0 unsafe actions
sre-agent regression-check            # fresh scores vs the recorded baseline
sre-agent injection-check             # hostile telemetry must change nothing: 8 of 8
sre-agent tier3-check                 # Tier 3 drills and the kill switch: 7 of 7
sre-agent security-check              # ten probes on the agent itself: 10 of 10
```

The regression baseline in `evaluation/baseline.json` is never
updated as part of an ordinary change. It moves only on
purpose, at a release, with
`sre-agent regression-check --update-baseline` — a gate that
re-recorded its own baseline would prove nothing.

Those same checks run automatically in CI
(`.github/workflows/ci.yml`): the test job runs lint, types
and the full suite on Python 3.11, 3.12 and 3.13, on both
Ubuntu and macOS, and a second job runs `./quickstart.sh`
from a clean checkout — a stranger's first run, exactly as
shipped — and then the scored CLI checks against that
install.

## The rules that do not bend

These come from the project's safety model
([docs/safety-model.md](docs/safety-model.md)). A change that
breaks one of them will not be merged, however useful it
looks:

1. **No free-form commands, ever.** Fixes are allow-listed,
   parameterised runbooks. There is no shell access and no
   code execution anywhere in the package — a security probe
   scans for it.
2. **Telemetry is untrusted input.** Logs, alert text and
   metric labels are data, never instructions. Hostile text
   must not be able to change a diagnosis, a target, or an
   action; the injection test set checks exactly that.
3. **Evidence or it did not happen.** Every diagnosis claim
   cites the query behind it. A claim with no evidence, or
   evidence that is not on the incident, is refused.
4. **A human approves every mutation outside the Tier 3 demo
   grant.** Approvals are single-use, expire in 15 minutes,
   and policy is checked again at approval time.
5. **Nothing is claimed that the harness has not measured.**
   If a capability is not covered by a test or a scored suite,
   the docs say so plainly instead of implying it works.
6. **Plain and simple documentation.** Every change ships
   with its explanation in ordinary words: update the README
   or the guide it touches, and add a short entry to
   [docs/progress.md](docs/progress.md). Jargon is explained
   the first time it appears.

## Make a change

1. Open an issue first for anything bigger than a small fix,
   so the direction can be agreed before code is written.
2. Keep the change small and in one piece: one roadmap item,
   one bug, one guide. Small changes are reviewed faster and
   are easier to prove safe.
3. Write the tests with the code. A new runbook also needs
   what the [runbook authoring
   guide](docs/runbook-authoring-guide.md) lists — the four
   places that must agree, and a test for each refusal.
4. Run every check above and say, in the pull request, what
   each one printed. The pull request template asks for this.
5. Use a clear commit message that says what changed and why,
   in plain words.

## Report a problem

Use the issue templates under "New issue": one for bugs, one
for feature requests. For a bug, the most useful things are
the exact command you ran, what it printed, your
`sre-agent --version`, and — for anything about scoring — the
output of the check commands above.

**Security problems are different.** If you find a way to make
the agent do something unsafe — run an unapproved action, leak
a secret, follow an instruction planted in telemetry — please
do not open a public issue with the working details. Contact
the maintainer (Abraham Kakooza, through his GitHub profile)
privately first, so a fix can land before the details are
public. The full policy — supported versions, scope, and
what to include — is in [SECURITY.md](SECURITY.md). The
project's own findings are published in
[docs/security-review.md](docs/security-review.md); a confirmed
report joins that record once it is fixed.

## Good first changes

- Documentation fixes: a step that is unclear, a limit that is
  stated in one place but missing in another.
- A new test for a refusal that is described in the docs but
  not yet pinned by a test.
- A scenario or injection case that covers a surface the
  suites already read, scored the same honest way.

When in doubt, ask in an issue. A short question up front is
cheaper than a long pull request in the wrong direction.
