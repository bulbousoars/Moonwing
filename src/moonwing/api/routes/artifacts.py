from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from moonwing.api.deps import get_db
from moonwing.db.models import Artifact

router = APIRouter()


@router.get('')
def list_artifacts(
    db: Session = Depends(get_db),
    target_id: str | None = Query(None),
    artifact_type: str | None = Query(None),
):
    q = db.query(Artifact)
    if target_id:
        q = q.filter(Artifact.target_id == UUID(target_id))
    if artifact_type:
        q = q.filter(Artifact.artifact_type == artifact_type)
    artifacts = q.order_by(Artifact.created_at.desc()).all()
    return [
        {
            'id': str(a.id),
            'target_id': str(a.target_id) if a.target_id else None,
            'artifact_type': a.artifact_type,
            'object_key': a.object_key,
            'provenance': a.provenance,
            'created_at': a.created_at.isoformat() if a.created_at else None,
        }
        for a in artifacts
    ]


@router.get('/{artifact_id}')
def get_artifact(artifact_id: UUID, db: Session = Depends(get_db)):
    artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
    if not artifact:
        raise HTTPException(status_code=404, detail='Artifact not found')
    return {
        'id': str(artifact.id),
        'target_id': str(artifact.target_id) if artifact.target_id else None,
        'artifact_type': artifact.artifact_type,
        'object_key': artifact.object_key,
        'provenance': artifact.provenance,
        'created_at': artifact.created_at.isoformat() if artifact.created_at else None,
    }
