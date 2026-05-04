from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from moonwing.api.deps import get_db
from moonwing.db.models import Finding

router = APIRouter()


@router.get('')
def list_findings(
    db: Session = Depends(get_db),
    severity: str | None = Query(None),
    run_id: str | None = Query(None),
):
    q = db.query(Finding)
    if severity:
        q = q.filter(Finding.severity == severity)
    if run_id:
        q = q.filter(Finding.run_id == UUID(run_id))
    findings = q.order_by(Finding.created_at.desc()).all()
    return [
        {
            'id': str(f.id),
            'run_id': str(f.run_id),
            'title': f.title,
            'severity': f.severity,
            'evidence_refs': f.evidence_refs,
            'created_at': f.created_at.isoformat() if f.created_at else None,
        }
        for f in findings
    ]


@router.get('/{finding_id}')
def get_finding(finding_id: UUID, db: Session = Depends(get_db)):
    finding = db.query(Finding).filter(Finding.id == finding_id).first()
    if not finding:
        raise HTTPException(status_code=404, detail='Finding not found')
    return {
        'id': str(finding.id),
        'run_id': str(finding.run_id),
        'title': finding.title,
        'severity': finding.severity,
        'evidence_refs': finding.evidence_refs,
        'created_at': finding.created_at.isoformat() if finding.created_at else None,
    }
