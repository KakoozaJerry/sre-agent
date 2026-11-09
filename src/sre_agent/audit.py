"""The audit log: every policy decision, written down before anything runs.

In plain words: when the agent proposes an action, the policy engine
decides whether it may run, needs a human's approval, or is denied.
That decision — allowed, approval-needed, or denied, with the reason —
is recorded here *before* any execution, including the denied ones.
If it is not in this log, it did not get decided, and it must not run.

Two care points, both from docs/safety-model.md:

- **No secret values.** A record names the runbook, the target, and a
  short fingerprint (a SHA-256 hash) of the parameters — never the
  parameter values themselves, so a password or token passed as a
  parameter cannot leak into the log.
- **Append-only.** Records can be added and read, never changed or
  removed. The in-memory list hands out copies, and the optional file
  form is one JSON object per line, appended line by line (JSONL), so
  the file can be read back with ``read_audit_file`` and checked.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import AuditRecord, Target
from .policy import EnvironmentPolicy, PolicyResult, ProposedAction, evaluate


def parameters_hash(action: ProposedAction) -> str:
    """A fingerprint of an action's parameters, safe to store and show."""
    canonical = json.dumps(action.parameters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def action_summary(action: ProposedAction) -> str:
    """One plain line naming an action, with no parameter values in it."""
    target = action.target
    return (
        f"{action.runbook_id} on {target.service}/{target.namespace} "
        f"({target.environment}), parameters sha256:{parameters_hash(action)[:12]}"
    )


class AuditLog:
    """An append-only list of audit records, optionally kept in a file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._records: list[AuditRecord] = []

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
        )
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
