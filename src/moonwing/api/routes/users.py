from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from moonwing.api.deps import get_db
from moonwing.db.models import User

router = APIRouter()


class UserCreate(BaseModel):
    email: str
    display_name: str


@router.get('')
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [
        {
            'id': str(u.id),
            'email': u.email,
            'display_name': u.display_name,
            'created_at': u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.get('/{user_id}')
def get_user(user_id: UUID, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    return {
        'id': str(user.id),
        'email': user.email,
        'display_name': user.display_name,
        'created_at': user.created_at.isoformat() if user.created_at else None,
    }


@router.post('', status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    user = User(email=payload.email, display_name=payload.display_name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {'id': str(user.id), 'email': user.email}
