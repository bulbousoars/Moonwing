from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from moonwing.api.deps import get_db
from moonwing.core.evidence import EvidenceTransitionError
from moonwing.db.models import Finding
from moonwing.services.evidence_ladder import (
    FindingNotFoundError,
    transition_finding,
)
from moonwing.services.permissions import require_role

router = APIRouter()


def _serialize(f: Finding) -> dict[str, Any]:
    return {
        'id': str(f.id),
        'run_id': str(f.run_id),
        'title': f.title,
        'severity': f.severity,
        'status': f.status,
        'evidence_level': f.evidence_level,
        'evidence_refs': f.evidence_refs,
        'evidence_history': f.evidence_history,
        'last_transition_at': f.last_transition_at.isoformat() if f.last_transition_at else None,
        'last_transition_by': str(f.last_transition_by) if f.last_transition_by else None,
        'created_at': f.created_at.isoformat() if f.created_at else None,
    }


@router.get('')
def list_findings(
    db: Session = Depends(get_db),
    severity: str | None = Query(None),
    run_id: str | None = Query(None),
    evidence_level: str | None = Query(None),
):
    q = db.query(Finding)
    if severity:
        q = q.filter(Finding.severity == severity)
    if run_id:
        q = q.filter(Finding.run_id == UUID(run_id))
    if evidence_level:
        q = q.filter(Finding.evidence_level == evidence_level)
    findings = q.order_by(Finding.created_at.desc()).all()
    return [_serialize(f) for f in findings]


@router.get('/{finding_id}')
def get_finding(finding_id: UUID, db: Session = Depends(get_db)):
    finding = db.query(Finding).filter(Finding.id == finding_id).first()
    if not finding:
        raise HTTPException(status_code=404, detail='Finding not found')
    return _serialize(finding)


class TransitionRequest(BaseModel):
    new_level: str = Field(..., description="Target evidence level")
    reason: str | None = Field(None, max_length=2000)
    evidence_ref: dict[str, Any] | None = None


@router.post('/{finding_id}/transition')
def transition_evidence(
    finding_id: UUID,
    request: Request,
    body: TransitionRequest = Body(...),
    db: Session = Depends(get_db),
):
    current_user = getattr(request.state, 'current_user', None)
    role = current_user.get('role') if current_user else None
    try:
        require_role(role, 'validate_findings')
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    actor_id = UUID(current_user['id']) if current_user else None

    try:
        finding = transition_finding(
            db,
            finding_id=finding_id,
            new_level=body.new_level,
            actor_id=actor_id,
            reason=body.reason,
            evidence_ref=body.evidence_ref,
        )
    except FindingNotFoundError:
        raise HTTPException(status_code=404, detail='Finding not found')
    except EvidenceTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    db.commit()
    db.refresh(finding)
    return _serialize(finding)
