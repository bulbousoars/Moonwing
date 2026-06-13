"""Evidence-ladder transition service.

Mutates a ``Finding``'s ``evidence_level``, appends a history row, and
emits an audit event. All state-machine rules live in ``core.evidence`` —
this module is the persistence + auditing seam.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from moonwing.core.evidence import (
    EvidenceLevel,
    EvidenceTransitionError,
    assert_transition,
    parse_level,
)
from moonwing.db.models import Finding
from moonwing.services.iam import audit


class FindingNotFoundError(LookupError):
    pass


def _history_entry(
    *,
    from_level: EvidenceLevel,
    to_level: EvidenceLevel,
    actor_id: UUID | None,
    reason: str | None,
    evidence_ref: dict[str, Any] | None,
    at: datetime,
) -> dict[str, Any]:
    return {
        "from": from_level.value,
        "to": to_level.value,
        "at": at.isoformat(),
        "by": str(actor_id) if actor_id else None,
        "reason": (reason or "").strip() or None,
        "evidence_ref": evidence_ref,
    }


def transition_finding(
    session: Session,
    *,
    finding_id: UUID,
    new_level: str | EvidenceLevel,
    actor_id: UUID | None,
    reason: str | None = None,
    evidence_ref: dict[str, Any] | None = None,
) -> Finding:
    """Move a finding to a new evidence level.

    Raises:
      FindingNotFoundError: no row matches ``finding_id``.
      EvidenceTransitionError: transition is disallowed (terminal state,
        backwards move, or unknown level string).
    """
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise FindingNotFoundError(str(finding_id))

    target = parse_level(new_level)
    current = parse_level(finding.evidence_level or EvidenceLevel.SUSPICION.value)
    assert_transition(current, target)

    now = datetime.now(timezone.utc)
    history = list(finding.evidence_history or [])
    history.append(
        _history_entry(
            from_level=current,
            to_level=target,
            actor_id=actor_id,
            reason=reason,
            evidence_ref=evidence_ref,
            at=now,
        )
    )

    finding.evidence_level = target.value
    finding.evidence_history = history
    finding.last_transition_at = now
    finding.last_transition_by = actor_id

    audit(
        session,
        action="finding.evidence_transition",
        resource_type="finding",
        actor_user_id=actor_id,
        resource_id=str(finding.id),
        metadata={
            "from": current.value,
            "to": target.value,
            "reason": reason,
            "evidence_ref": evidence_ref,
        },
    )

    return finding
