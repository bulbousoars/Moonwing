from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from moonwing.api.deps import get_db
from moonwing.db.models import Target, Artifact

router = APIRouter()


class TargetCreate(BaseModel):
    target_type: str
    display_name: str
    source_metadata: dict = {}


@router.get('')
def list_targets(db: Session = Depends(get_db)):
    targets = db.query(Target).order_by(Target.created_at.desc()).all()
    return [
        {
            'id': str(t.id),
            'target_type': t.target_type,
            'display_name': t.display_name,
            'source_metadata': t.source_metadata,
            'created_at': t.created_at.isoformat() if t.created_at else None,
        }
        for t in targets
    ]


@router.get('/{target_id}')
def get_target(target_id: UUID, db: Session = Depends(get_db)):
    target = db.query(Target).filter(Target.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail='Target not found')
    artifacts = db.query(Artifact).filter(Artifact.target_id == target.id).all()
    return {
        'id': str(target.id),
        'target_type': target.target_type,
        'display_name': target.display_name,
        'source_metadata': target.source_metadata,
        'created_at': target.created_at.isoformat() if target.created_at else None,
        'artifacts': [
            {
                'id': str(a.id),
                'artifact_type': a.artifact_type,
                'object_key': a.object_key,
                'provenance': a.provenance,
                'created_at': a.created_at.isoformat() if a.created_at else None,
            }
            for a in artifacts
        ],
    }


@router.post('', status_code=201)
def create_target(payload: TargetCreate, db: Session = Depends(get_db)):
    target = Target(
        target_type=payload.target_type,
        display_name=payload.display_name,
        source_metadata=payload.source_metadata,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return {'id': str(target.id), 'display_name': target.display_name}
