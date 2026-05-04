from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from moonwing.api.deps import get_db
from moonwing.db.models import Credential, Run, Finding, RuntimeProfileRecord, Target, User
from moonwing.schemas.runs import RunCreateRequest, RunCreateResponse
from moonwing.services.runs import create_run_snapshot

router = APIRouter()


class RunLaunchRequest(BaseModel):
    """Request to create and queue a run that the worker will execute."""
    job_family: str
    target_id: str | None = None
    credential_id: str
    runtime_profile_id: str
    provider: str
    model: str
    user_id: str
    execution_mode: str = "api"
    execution_snapshot: dict = {}


@router.post('', status_code=status.HTTP_201_CREATED)
def create_run(payload: RunLaunchRequest, db: Session = Depends(get_db)):
    # Validate foreign keys exist
    user = db.get(User, UUID(payload.user_id))
    if not user:
        raise HTTPException(status_code=422, detail=f'User {payload.user_id} not found')
    cred = db.get(Credential, UUID(payload.credential_id))
    if not cred:
        raise HTTPException(status_code=422, detail=f'Credential {payload.credential_id} not found')
    profile = db.get(RuntimeProfileRecord, UUID(payload.runtime_profile_id))
    if not profile:
        raise HTTPException(status_code=422, detail=f'Runtime profile {payload.runtime_profile_id} not found')
    if payload.target_id:
        target = db.get(Target, UUID(payload.target_id))
        if not target:
            raise HTTPException(status_code=422, detail=f'Target {payload.target_id} not found')

    run = Run(
        job_family=payload.job_family,
        status='queued',
        user_id=UUID(payload.user_id),
        credential_id=UUID(payload.credential_id),
        runtime_profile_id=UUID(payload.runtime_profile_id),
        target_id=UUID(payload.target_id) if payload.target_id else None,
        provider=payload.provider,
        model=payload.model,
        execution_mode=payload.execution_mode,
        execution_snapshot=payload.execution_snapshot,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return {
        'id': str(run.id),
        'status': run.status,
        'job_family': run.job_family,
        'created_at': run.created_at.isoformat() if run.created_at else None,
    }


@router.get('')
def list_runs(db: Session = Depends(get_db)):
    runs = db.query(Run).order_by(Run.created_at.desc()).all()
    return [
        {
            'id': str(r.id),
            'job_family': r.job_family,
            'status': r.status,
            'provider': r.provider,
            'model': r.model,
            'target_id': str(r.target_id) if r.target_id else None,
            'created_at': r.created_at.isoformat() if r.created_at else None,
        }
        for r in runs
    ]


@router.get('/{run_id}')
def get_run(run_id: UUID, db: Session = Depends(get_db)):
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail='Run not found')
    findings = db.query(Finding).filter(Finding.run_id == run.id).all()
    return {
        'id': str(run.id),
        'job_family': run.job_family,
        'status': run.status,
        'provider': run.provider,
        'model': run.model,
        'user_id': str(run.user_id),
        'credential_id': str(run.credential_id),
        'runtime_profile_id': str(run.runtime_profile_id),
        'target_id': str(run.target_id) if run.target_id else None,
        'execution_snapshot': run.execution_snapshot,
        'created_at': run.created_at.isoformat() if run.created_at else None,
        'findings': [
            {
                'id': str(f.id),
                'title': f.title,
                'severity': f.severity,
                'evidence_refs': f.evidence_refs,
                'created_at': f.created_at.isoformat() if f.created_at else None,
            }
            for f in findings
        ],
    }
