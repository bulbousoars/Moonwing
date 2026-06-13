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
from moonwing.services.host_cli_discovery import scan_host_ai_tools

router = APIRouter()


@router.get('/updates/status')
def api_updates_status():
    gs = build_git_update_status(_get_settings())
    return asdict(gs)


@router.get('/host-ai-tools')
def api_host_ai_tools():
    """Read-only scan of AI CLIs on the Docker host (bind-mounted bin directories)."""
    return scan_host_ai_tools(_get_settings())


@router.post('/host-ai-tools/scan')
def api_host_ai_tools_scan():
    """Run a fresh host AI tools scan (same payload as GET /host-ai-tools)."""
    return scan_host_ai_tools(_get_settings())


@router.get('/cli/visibility', include_in_schema=False)
def api_cli_visibility_legacy():
    """Deprecated alias for /host-ai-tools."""
    return scan_host_ai_tools(_get_settings())


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
