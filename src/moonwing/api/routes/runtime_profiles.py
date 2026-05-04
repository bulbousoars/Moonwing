from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from moonwing.api.deps import get_db
from moonwing.db.models import RuntimeProfileRecord

router = APIRouter()


class RuntimeProfileCreate(BaseModel):
    name: str
    allow_exploits: bool = False
    settings: dict = {}


@router.get('')
def list_profiles(db: Session = Depends(get_db)):
    profiles = db.query(RuntimeProfileRecord).order_by(RuntimeProfileRecord.created_at.desc()).all()
    return [
        {
            'id': str(p.id),
            'name': p.name,
            'allow_exploits': p.allow_exploits,
            'settings': p.settings,
            'created_at': p.created_at.isoformat() if p.created_at else None,
        }
        for p in profiles
    ]


@router.get('/{profile_id}')
def get_profile(profile_id: UUID, db: Session = Depends(get_db)):
    profile = db.query(RuntimeProfileRecord).filter(RuntimeProfileRecord.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail='Runtime profile not found')
    return {
        'id': str(profile.id),
        'name': profile.name,
        'allow_exploits': profile.allow_exploits,
        'settings': profile.settings,
        'created_at': profile.created_at.isoformat() if profile.created_at else None,
    }


@router.post('', status_code=201)
def create_profile(payload: RuntimeProfileCreate, db: Session = Depends(get_db)):
    profile = RuntimeProfileRecord(
        name=payload.name,
        allow_exploits=payload.allow_exploits,
        settings=payload.settings,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return {'id': str(profile.id), 'name': profile.name}
