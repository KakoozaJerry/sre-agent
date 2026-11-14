"""Security review of the agent itself: probes, plus the findings.

In plain words, Phase 5 starts by turning the red team on this
project itself. Earlier phases checked that hostile *telemetry*
cannot steer the agent (the prompt-injection set, ``injection.py``).
This module checks the rest of the agent's own surface — the
policy gate, the runbook library, the approval flow, the audit
log, the tool layer's cleaning, and the query checks — with
probes an attacker would try first, and it carries the review's
findings in the open, in code, next to those probes.

Two halves, kept separate on purpose:

- **Probes** (``run_security_review``). Ten checks, each one an
  attack or a misuse run against the real code paths: propose a
  made-up runbook, smuggle a free-form command in as a parameter,
  aim a fix at a target other than the incident's, plant a secret
  where the audit log could leak it, plant secrets in log lines
  and a status message, replay a used approval, approve into a
  halt, push past the Tier 3 limits, send an Azure management
  command, send a broken or oversized metrics query, scan the
  package source for any shell or code-execution call, and let
  alert text claim an incident is over. A probe passes only if
  every one of its sub-checks holds. The set runs with no server,
  no Docker and no network, so it runs in pytest, in CI, and as
  ``sre-agent security-check``.

- **Findings** (``SECURITY_FINDINGS``). The things the probes
  cannot prove away, written down with a severity and a status.
  The largest was plain: at review time the webhook server had
  no authentication at all — anyone who could reach it could
  open incidents, close them with a "resolved" alert, and read
  every incident back. That was accepted for a server that only
  ever runs on one person's own machine in the demo. After
  v0.1.0 the first two findings were fixed: the server takes a
  shared token (required on every route except ``/health`` when
  one is set; with none set it stays a local-only server), and
  ingestion is bounded by payload, field and store caps. The
  third finding (F-03) was then improved: the secret cleaning
  now also catches JSON web tokens, passwords inside connection
  strings and inside URLs, and more provider token shapes; what
  it still cannot promise is stated in that finding's summary.
  The fourth finding (F-04) was then fixed: audit records are
  hash-chained, so an edited, deleted or reordered record is
  reported by the verifier in ``audit.py``; what a chain alone
  cannot detect is stated in that finding's summary too.
  The fifth finding (F-05) was then fixed: every live backend
  address, and the CLI's server address, must pass the rules in
  ``backend_url.py`` — https, or plain http to this machine
  only, no credentials inside the address, and an optional host
  allow-list that can narrow but never widen those rules —
  before any credential can be sent to it.
  The sixth finding (F-06) was then improved: both demo images
  now run as a named non-root user, and the agent image installs
  its dependencies from a lock file (``requirements.lock``) with
  a hash for every package, checked as it installs. Pinning the
  Compose images by digest was not done, because no verified
  manifest digest could be produced in the build environment —
  an invented digest would be worse than the tag it replaces —
  and that remainder is stated in that finding's summary.
  The statuses below say so, and the full write-up, with the
  reasoning and the fix for each finding, is the red-team report
  in ``docs/security-review.md``; this list and that document
  name the same findings with the same ids.

Scope, stated honestly: this is a self-review of the code in
this repository, run against the demo backends like everything
else in the harness. No live Kubernetes cluster, Datadog account,
Azure workspace or Docker run was attacked, no external tester
was involved, and the reasoning loop calls no language model, so
model-specific attacks beyond the injection set are out of scope.
Nothing here is a claim that the agent is safe to run against
real systems — the findings say the opposite for the server.

Since the Phase 12 restructure this is a package, split with
no behaviour change: ``findings.py`` holds the findings (the
data — ``SecurityFinding`` and ``SECURITY_FINDINGS``), and
``probes.py`` holds the probes, their result models and
``run_security_review``. Every name the old single module
exported is re-exported here, so
``from sre_agent.security_review import ...`` works exactly as
before. The one necessary change inside the move: the
no-shell probe now scans the whole package recursively and
excludes only its own file by path, because its file moved
into this subpackage — scanning only its new folder would
have checked almost nothing, and a top-level scan would have
missed the adapters subpackage.
"""

from __future__ import annotations

from .findings import SECURITY_FINDINGS, SecurityFinding
from .probes import (
    CHECKOUT,
    PLANTED_JWT,
    PLANTED_SECRET,
    SecurityCheckResult,
    SecurityReport,
    run_security_review,
)

__all__ = [
    "CHECKOUT",
    "PLANTED_JWT",
    "PLANTED_SECRET",
    "SECURITY_FINDINGS",
    "SecurityCheckResult",
    "SecurityFinding",
    "SecurityReport",
    "run_security_review",
]
