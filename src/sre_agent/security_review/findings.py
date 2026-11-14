"""Security findings: the written-down weaknesses, as data.

In plain words, this module holds only the findings half of the
security review (see the ``security_review`` package): the things
the probes cannot prove away, written down with a severity and a
status, in the same words and with the same ids as the red-team
report in ``docs/security-review.md``. It runs no probe and
attacks nothing — the probes live in ``probes.py`` next to it.
"""

from __future__ import annotations

from pydantic import BaseModel


class SecurityFinding(BaseModel):
    """One written-down weakness, with its severity and status."""

    id: str
    title: str
    severity: str  # "high" | "medium" | "low" | "info"
    status: str
    summary: str


# The findings, in the same words and with the same ids as the
# red-team report in docs/security-review.md. Severity is judged
# for the case that matters — the server or a live backend used
# beyond one operator's own machine — and the status says what
# holds for the demo as it stands today.
SECURITY_FINDINGS: list[SecurityFinding] = [
    SecurityFinding(
        id="F-01",
        title="The webhook server has no authentication",
        severity="high",
        status="fixed after v0.1.0 — set a token before any shared use",
        summary=(
            "As reviewed, anyone who could reach the server could post "
            "alerts (opening incidents, or closing them with a resolved "
            "alert) and read every incident back. The server now takes "
            "a shared token (SRE_AGENT_API_TOKEN): when one is set, "
            "every endpoint except /health requires it, webhooks and "
            "reads alike. With no token set the server is still "
            "unauthenticated and stays a local, one-operator server."
        ),
    ),
    SecurityFinding(
        id="F-02",
        title="Ingestion has no size or rate limits",
        severity="medium",
        status="fixed after v0.1.0 — intake is capped; no per-sender rate limit",
        summary=(
            "As reviewed, an alert payload could carry any number of "
            "alerts with any size of labels and annotations, grouped "
            "alerts added evidence without a cap, and the in-memory "
            "store grew without a bound. Ingestion now caps alerts per "
            "payload, label/annotation count and size, evidence per "
            "incident, and incidents in the store, refusing past the "
            "caps with a clear error. A per-sender rate limit was not "
            "added: a flood can fill the store to its cap and then "
            "gets refusals, but it cannot grow memory without bound."
        ),
    ),
    SecurityFinding(
        id="F-03",
        title="Secret cleaning is a pattern list, not a guarantee",
        severity="medium",
        status=(
            "improved after v0.1.0 — JWTs and more shapes added; "
            "still a pattern list, not a guarantee"
        ),
        summary=(
            "redact_secrets catches common shapes (password=, token=, "
            "bearer and basic credentials, AWS access key ids, GitHub "
            "tokens, private-key blocks and headers) and, since the "
            "post-release improvement, JSON web tokens, passwords "
            "inside connection strings and inside URLs, and GitLab, "
            "Slack and Stripe token shapes. It still does not catch "
            "an unlabelled secret with no known prefix, or a private "
            "key's body lines shown without their header, so a secret "
            "in one of those shapes can reach an evidence record."
        ),
    ),
    SecurityFinding(
        id="F-04",
        title="The audit log file has no tamper protection",
        severity="low",
        status=(
            "fixed after v0.1.0 — records are hash-chained and "
            "verifiable; a whole-file rewrite is still not detectable"
        ),
        summary=(
            "As reviewed, audit records were append-only in the code's "
            "own use, but the JSON-lines file itself was not "
            "hash-chained or signed — someone who could edit the file "
            "could rewrite history without detection. Every record now "
            "carries the hash of the record before it and its own "
            "hash, and verify_audit_records / verify_audit_file report "
            "an edited, deleted or reordered record by position. What "
            "a chain alone cannot do, stated plainly: deleting the "
            "last records, or replacing the whole file with a freshly "
            "recomputed chain, is still not detectable — that would "
            "need a signature or a copy kept elsewhere."
        ),
    ),
    SecurityFinding(
        id="F-05",
        title="Live backend addresses are caller-supplied, with no allow-list",
        severity="low",
        status=(
            "fixed after v0.1.0 — https required beyond this machine, "
            "with an optional host allow-list"
        ),
        summary=(
            "As reviewed, the Prometheus, Kubernetes, Datadog and "
            "Azure backends, and the CLI's server address, took a "
            "base URL from their caller with no checks and sent "
            "credentials to it as headers. Every one of those "
            "addresses is now checked when the backend is built "
            "(backend_url.py): https is required for any host beyond "
            "this machine (plain http is allowed only to localhost "
            "or a loopback address, where the demo backends live), "
            "an address carrying a user or password is refused, and "
            "a caller may pass an allow-list of hosts that the "
            "address must be on — a list can narrow where the agent "
            "may call, never widen the https rule. Addresses still "
            "come only from operator code or configuration, never "
            "from telemetry."
        ),
    ),
    SecurityFinding(
        id="F-06",
        title="Demo containers and dependencies are not hardened",
        severity="low",
        status=(
            "improved after v0.1.0 — images run as non-root and "
            "dependencies are locked with hashes; Compose images "
            "are still pinned by tag, not by digest"
        ),
        summary=(
            "As reviewed, the demo image ran as root, installed "
            "dependencies from version ranges with no lock file or "
            "hashes, and the Compose file pinned Prometheus and "
            "Alertmanager by tag, not by digest. Both demo images "
            "now run as a named non-root user, and the agent image "
            "installs from requirements.lock — exact versions with "
            "a SHA-256 hash for every package, checked with "
            "--require-hashes as it installs (the hashes are for "
            "the wheels pip resolved for CPython 3.12 on Linux "
            "x86_64, the demo image's platform). What remains, "
            "stated plainly: the Compose file still pins "
            "Prometheus and Alertmanager by tag, not by manifest "
            "digest, because no verified digest could be produced "
            "in the build environment, and an invented or "
            "unverified digest would be worse than the tag."
        ),
    ),
    SecurityFinding(
        id="F-07",
        title="The log-file backend reads a caller-chosen path",
        severity="info",
        status="accepted, with the boundary stated",
        summary=(
            "FileLogsBackend reads whatever file path its caller gives "
            "it. Only demo and test code chooses that path today; the "
            "path must never come from telemetry or an API request."
        ),
    ),
    SecurityFinding(
        id="F-08",
        title="A crafted log line can spoof one symptom signal",
        severity="info",
        status="known limitation, carried over from the injection set",
        summary=(
            "The reasoning loop reads the word 'timeout' in error logs "
            "as a symptom by plain rule, so a planted line can raise "
            "that one hypothesis. It cannot add wording, actions or "
            "targets beyond the loop's fixed set."
        ),
    ),
]
