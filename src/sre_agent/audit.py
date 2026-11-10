"""The audit log: every policy decision, written down before anything runs.

In plain words: when the agent proposes an action, the policy engine
decides whether it may run, needs a human's approval, or is denied.
That decision — allowed, approval-needed, or denied, with the reason —
is recorded here *before* any execution, including the denied ones.
If it is not in this log, it did not get decided, and it must not run.

Three care points, all from docs/safety-model.md and the security
review in docs/security-review.md:

- **No secret values.** A record names the runbook, the target, and a
  short fingerprint (a SHA-256 hash) of the parameters — never the
  parameter values themselves, so a password or token passed as a
  parameter cannot leak into the log.
- **Append-only.** Records can be added and read, never changed or
  removed. The in-memory list hands out copies, and the optional file
  form is one JSON object per line, appended line by line (JSONL), so
  the file can be read back with ``read_audit_file`` and checked.
- **Tamper-evident (finding F-04, fixed after v0.1.0).** Every record
  is hash-chained: it carries the hash of the record before it
  (``prev_hash`` — the first record carries ``GENESIS_HASH``) and its
  own SHA-256 hash (``record_hash``), computed over every other field,
  including ``prev_hash``. Editing a record, deleting one from the
  middle, or reordering records breaks the chain, and
  ``verify_audit_records`` / ``verify_audit_file`` report exactly
  where. The honest limits of a chain alone, stated here so nobody
  over-claims it: it does not detect deleting the *last* records or
  replacing the whole file with a freshly recomputed chain — that
  would need a signature or a copy kept elsewhere — and it does not
  stop anyone editing the file; it makes the common edits show.
  Records written before the chain existed have empty hashes; the
  verifier reports them as unchained rather than passing them, so an
  old file must be kept as an old file and a new log started.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import AuditRecord, Target
from .policy import EnvironmentPolicy, PolicyResult, ProposedAction, evaluate

# The ``prev_hash`` of the first record in every log: 64 zero
# characters, so a real record hash (also 64 hex characters) can
# never be mistaken for it.
GENESIS_HASH = "0" * 64


def parameters_hash(action: ProposedAction) -> str:
    """A fingerprint of an action's parameters, safe to store and show."""
    canonical = json.dumps(action.parameters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_hash(record: AuditRecord) -> str:
    """A record's own hash, over every field except ``record_hash``.

    ``prev_hash`` is inside the hashed fields, so this one hash ties
    the record to the exact record that came before it. The JSON is
    canonical (keys sorted, no extra spaces, dates as text) so the
    same record always hashes to the same value, in any process.
    """
    payload = record.model_dump(mode="json", exclude={"record_hash"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def action_summary(action: ProposedAction) -> str:
    """One plain line naming an action, with no parameter values in it."""
    target = action.target
    return (
        f"{action.runbook_id} on {target.service}/{target.namespace} "
        f"({target.environment}), parameters sha256:{parameters_hash(action)[:12]}"
    )


def verify_audit_records(records: list[AuditRecord]) -> list[str]:
    """Check a list of audit records is one unbroken hash chain.

    Returns a list of problems, in plain words; an empty list means
    the chain is intact. Each record is checked for: carrying chain
    hashes at all (a record written before F-04 was fixed, or one
    whose hashes were stripped, is reported, not passed), pointing
    at the hash of the record before it, and matching its own hash
    (any edited field breaks that match). An empty list of records
    has no problems — there is nothing in it to have been edited.
    """
    problems: list[str] = []
    expected_prev = GENESIS_HASH
    for index, record in enumerate(records):
        label = f"record {index + 1} (id {record.id})"
        if not record.prev_hash or not record.record_hash:
            problems.append(f"{label} has no chain hashes (unchained record)")
            # Without this record's own hash the next record's link
            # cannot be checked against it; keep expecting the last
            # hash we actually saw so one gap does not hide the rest.
            continue
        if record.prev_hash != expected_prev:
            problems.append(
                f"{label} does not point at the record before it "
                "(a record may have been deleted, added or reordered)"
            )
        if record_hash(record) != record.record_hash:
            problems.append(
                f"{label} does not match its own hash "
                "(a field in it may have been edited)"
            )
        expected_prev = record.record_hash
    return problems


def verify_audit_file(path: str | Path) -> list[str]:
    """Read an audit file back and check its hash chain.

    Returns the problems ``verify_audit_records`` found; an empty
    list means the file's chain is intact.
    """
    return verify_audit_records(read_audit_file(path))


class AuditLog:
    """An append-only, hash-chained list of records, optionally in a file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._records: list[AuditRecord] = []
        self._last_hash = self._chain_tail(self._path)

    @staticmethod
    def _chain_tail(path: Path | None) -> str:
        """The hash a new record must point at, given an existing file.

        A log that appends to a file written earlier continues that
        file's chain, so a restart does not break it. A file whose
        last record is unchained (written before F-04) offers no
        hash to continue from; the new records start from the
        genesis value, and verification will report the old records
        as unchained — the honest outcome for a mixed file.
        """
        if path is None or not path.exists():
            return GENESIS_HASH
        try:
            lines = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if not lines:
                return GENESIS_HASH
            last = AuditRecord.model_validate_json(lines[-1])
        except (OSError, ValueError):
            return GENESIS_HASH
        return last.record_hash or GENESIS_HASH

    @property
    def records(self) -> list[AuditRecord]:
        """All records, oldest first. Copies: changing them changes nothing."""
        return [record.model_copy() for record in self._records]

    def record_decision(
        self,
        *,
        incident_id: str,
        action: ProposedAction,
        result: PolicyResult,
        approved_by: str | None = None,
    ) -> AuditRecord:
        """Append one record for a policy decision and return it."""
        record = AuditRecord(
            incident_id=incident_id,
            action_summary=action_summary(action),
            decision=result.decision.value,
            reason=result.reason,
            approved_by=approved_by,
            parameters_hash=parameters_hash(action),
            prev_hash=self._last_hash,
        )
        record.record_hash = record_hash(record)
        self._last_hash = record.record_hash
        self._records.append(record)
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(record.model_dump_json() + "\n")
        return record.model_copy()

    def for_incident(self, incident_id: str) -> list[AuditRecord]:
        """The records for one incident, oldest first."""
        return [r for r in self.records if r.incident_id == incident_id]


def evaluate_and_audit(
    action: ProposedAction,
    policy: EnvironmentPolicy,
    audit_log: AuditLog,
    *,
    incident_id: str,
    incident_target: Target | None = None,
    approved_by: str | None = None,
) -> PolicyResult:
    """Decide an action and record the decision before returning it.

    This is the normal way to use the policy engine: the decision is in
    the audit log whichever way it goes, before anything can run.
    """
    result = evaluate(action, policy, incident_target=incident_target)
    audit_log.record_decision(
        incident_id=incident_id,
        action=action,
        result=result,
        approved_by=approved_by,
    )
    return result


def read_audit_file(path: str | Path) -> list[AuditRecord]:
    """Read back a JSONL audit file written by :class:`AuditLog`."""
    records: list[AuditRecord] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(AuditRecord.model_validate_json(line))
    return records
