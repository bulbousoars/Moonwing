"""JSON API for in-manager upgrade checks and apply."""

from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from moonwing.api.deps import _get_settings, get_db
from moonwing.services.iam import audit
from moonwing.services.system_updates import apply_system_update, build_git_update_status
from moonwing.services.web_terminal_status import build_web_terminal_status

router = APIRouter()


@router.get('/updates/status')
def api_updates_status():
    gs = build_git_update_status(_get_settings())
    return asdict(gs)


@router.get('/terminal/status')
def api_terminal_status(db: Session = Depends(get_db)):
    """Whether the in-browser host PTY can connect (for CLI & terminal page)."""
    return build_web_terminal_status(db, _get_settings())


class ApplyUpdatesBody(BaseModel):
    confirm: str = Field(min_length=1)


@router.post('/updates/apply')
def api_updates_apply(payload: ApplyUpdatesBody, request: Request, db: Session = Depends(get_db)):
    if payload.confirm.strip() != 'APPLY':
        raise HTTPException(status_code=400, detail='Field "confirm" must be exactly APPLY')
    outcome = apply_system_update(_get_settings())
    audit(
        db,
        action='system_updates_apply',
        resource_type='system',
        actor_user_id=UUID(request.state.current_user['id']),
        resource_id='moonwing',
        outcome='success' if outcome.success else 'failure',
        metadata={'message': outcome.message, 'steps': [s.model_dump() for s in outcome.steps]},
    )
    db.commit()
    return outcome.model_dump()
