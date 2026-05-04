"""Dashboard view routes — renders Jinja2 templates with DB data."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from moonwing.api.deps import get_db
from moonwing.services.crypto import CryptoError, decrypt_api_key, encrypt_api_key, mask_api_key
from moonwing.db.models import (
    Artifact,
    Credential,
    Finding,
    Run,
    RuntimeProfileRecord,
    Target,
    User,
)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / 'templates')


def _render(request: Request, template: str, context: dict | None = None):
    ctx = context or {}
    return templates.TemplateResponse(request, template, ctx)

_STATE_TRANSITIONS = {
    'queued': ['staging', 'failed', 'canceled'],
    'staging': ['running', 'failed', 'canceled'],
    'running': ['normalizing', 'failed', 'needs_review', 'canceled'],
    'normalizing': ['completed', 'failed', 'needs_review'],
    'completed': [],
    'failed': [],
    'canceled': [],
    'needs_review': [],
}


def _serialize_run(r: Run) -> dict:
    return {
        'id': str(r.id),
        'job_family': r.job_family,
        'status': r.status,
        'provider': r.provider,
        'model': r.model,
        'execution_mode': r.execution_mode or 'api',
        'user_id': str(r.user_id),
        'credential_id': str(r.credential_id),
        'runtime_profile_id': str(r.runtime_profile_id),
        'target_id': str(r.target_id) if r.target_id else None,
        'execution_snapshot': r.execution_snapshot or {},
        'created_at': r.created_at.isoformat() if r.created_at else None,
    }


# ── Dashboard ──────────────────────────────────────────────────────────

@router.get('/', response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    stats = {
        'total_runs': db.query(func.count(Run.id)).scalar() or 0,
        'completed_runs': db.query(func.count(Run.id)).filter(Run.status == 'completed').scalar() or 0,
        'failed_runs': db.query(func.count(Run.id)).filter(Run.status == 'failed').scalar() or 0,
        'active_runs': db.query(func.count(Run.id)).filter(Run.status.in_(['queued', 'staging', 'running', 'normalizing'])).scalar() or 0,
        'total_findings': db.query(func.count(Finding.id)).scalar() or 0,
        'critical_findings': db.query(func.count(Finding.id)).filter(Finding.severity == 'critical').scalar() or 0,
        'high_findings': db.query(func.count(Finding.id)).filter(Finding.severity == 'high').scalar() or 0,
        'total_targets': db.query(func.count(Target.id)).scalar() or 0,
        'total_users': db.query(func.count(User.id)).scalar() or 0,
        'total_credentials': db.query(func.count(Credential.id)).scalar() or 0,
        'total_profiles': db.query(func.count(RuntimeProfileRecord.id)).scalar() or 0,
        'total_artifacts': db.query(func.count(Artifact.id)).scalar() or 0,
    }
    recent_runs = [
        _serialize_run(r)
        for r in db.query(Run).order_by(Run.created_at.desc()).limit(10).all()
    ]
    recent_findings = [
        {
            'id': str(f.id),
            'title': f.title,
            'severity': f.severity,
            'run_id': str(f.run_id),
            'evidence_refs': f.evidence_refs or [],
            'created_at': f.created_at.isoformat() if f.created_at else None,
        }
        for f in db.query(Finding).order_by(Finding.created_at.desc()).limit(10).all()
    ]
    return _render(request, 'dashboard.html', {
        'active': 'dashboard',
        'stats': stats, 'recent_runs': recent_runs, 'recent_findings': recent_findings,
    })


# ── Runs ───────────────────────────────────────────────────────────────

@router.get('/runs', response_class=HTMLResponse)
def runs_page(request: Request, db: Session = Depends(get_db)):
    runs = [
        _serialize_run(r)
        for r in db.query(Run).order_by(Run.created_at.desc()).all()
    ]
    return _render(request, 'runs.html', {'active': 'runs', 'runs': runs})


@router.get('/runs/new', response_class=HTMLResponse)
def run_form(request: Request, db: Session = Depends(get_db), target: str | None = None):
    targets = [
        {'id': str(t.id), 'display_name': t.display_name, 'target_type': t.target_type}
        for t in db.query(Target).order_by(Target.display_name).all()
    ]
    users = [
        {'id': str(u.id), 'display_name': u.display_name, 'email': u.email}
        for u in db.query(User).order_by(User.display_name).all()
    ]
    credentials = [
        {'id': str(c.id), 'display_name': c.display_name, 'provider': c.provider}
        for c in db.query(Credential).order_by(Credential.display_name).all()
    ]
    profiles = [
        {'id': str(p.id), 'name': p.name}
        for p in db.query(RuntimeProfileRecord).order_by(RuntimeProfileRecord.name).all()
    ]
    return _render(request, 'run_form.html', {
        'active': 'runs',
        'targets': targets,
        'users': users,
        'credentials': credentials,
        'profiles': profiles,
        'preselect_target': target or '',
    })


@router.post('/runs/new')
def run_create(
    request: Request,
    db: Session = Depends(get_db),
    job_family: str = Form(...),
    target_id: str = Form(''),
    user_id: str = Form(...),
    credential_id: str = Form(...),
    runtime_profile_id: str = Form(...),
    provider: str = Form('openai'),
    model: str = Form('gpt-4o'),
    execution_mode: str = Form('api'),
):
    run = Run(
        job_family=job_family,
        status='queued',
        user_id=UUID(user_id),
        credential_id=UUID(credential_id),
        runtime_profile_id=UUID(runtime_profile_id),
        target_id=UUID(target_id) if target_id.strip() else None,
        provider=provider,
        model=model,
        execution_mode=execution_mode,
        execution_snapshot={},
    )
    db.add(run)
    db.commit()
    return RedirectResponse(url=f'/runs/{run.id}', status_code=303)


@router.get('/runs/{run_id}', response_class=HTMLResponse)
def run_detail_page(run_id: UUID, request: Request, db: Session = Depends(get_db)):
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail='Run not found')
    findings = db.query(Finding).filter(Finding.run_id == run.id).order_by(Finding.created_at.desc()).all()
    run_data = _serialize_run(run)
    run_data['findings'] = [
        {
            'id': str(f.id),
            'title': f.title,
            'severity': f.severity,
            'evidence_refs': f.evidence_refs or [],
            'created_at': f.created_at.isoformat() if f.created_at else None,
        }
        for f in findings
    ]
    return _render(request, 'run_detail.html', {'active': 'runs', 'run': run_data,
    })


# ── Findings ───────────────────────────────────────────────────────────

@router.get('/findings', response_class=HTMLResponse)
def findings_page(request: Request, db: Session = Depends(get_db)):
    findings = [
        {
            'id': str(f.id),
            'run_id': str(f.run_id),
            'title': f.title,
            'severity': f.severity,
            'evidence_refs': f.evidence_refs or [],
            'created_at': f.created_at.isoformat() if f.created_at else None,
        }
        for f in db.query(Finding).order_by(Finding.created_at.desc()).all()
    ]
    severity_counts = {}
    for sev in ['critical', 'high', 'medium', 'low', 'info', 'unknown']:
        severity_counts[sev] = db.query(func.count(Finding.id)).filter(Finding.severity == sev).scalar() or 0
    return _render(request, 'findings.html', {'active': 'findings',
        'findings': findings, 'severity_counts': severity_counts,
    })


@router.get('/findings/{finding_id}', response_class=HTMLResponse)
def finding_detail_page(finding_id: UUID, request: Request, db: Session = Depends(get_db)):
    f = db.query(Finding).filter(Finding.id == finding_id).first()
    if not f:
        raise HTTPException(status_code=404, detail='Finding not found')
    finding = {
        'id': str(f.id),
        'run_id': str(f.run_id),
        'title': f.title,
        'severity': f.severity,
        'evidence_refs': f.evidence_refs or [],
        'created_at': f.created_at.isoformat() if f.created_at else None,
    }
    return _render(request, 'finding_detail.html', {'active': 'findings', 'finding': finding,
    })


# ── Targets ────────────────────────────────────────────────────────────

@router.get('/targets', response_class=HTMLResponse)
def targets_page(request: Request, db: Session = Depends(get_db)):
    targets = [
        {
            'id': str(t.id),
            'target_type': t.target_type,
            'display_name': t.display_name,
            'source_metadata': t.source_metadata or {},
            'created_at': t.created_at.isoformat() if t.created_at else None,
        }
        for t in db.query(Target).order_by(Target.created_at.desc()).all()
    ]
    return _render(request, 'targets.html', {'active': 'targets', 'targets': targets,
    })


@router.get('/targets/new', response_class=HTMLResponse)
def target_form(request: Request):
    return _render(request, 'target_form.html', {'active': 'targets',
    })


@router.post('/targets/new')
def target_create(
    request: Request,
    db: Session = Depends(get_db),
    display_name: str = Form(...),
    target_type: str = Form(...),
    source_metadata: str = Form('{}'),
):
    target = Target(
        target_type=target_type,
        display_name=display_name,
        source_metadata=json.loads(source_metadata),
    )
    db.add(target)
    db.commit()
    return RedirectResponse(url=f'/targets/{target.id}', status_code=303)


@router.get('/targets/{target_id}', response_class=HTMLResponse)
def target_detail_page(target_id: UUID, request: Request, db: Session = Depends(get_db)):
    t = db.query(Target).filter(Target.id == target_id).first()
    if not t:
        raise HTTPException(status_code=404, detail='Target not found')
    artifacts = db.query(Artifact).filter(Artifact.target_id == t.id).all()
    target = {
        'id': str(t.id),
        'target_type': t.target_type,
        'display_name': t.display_name,
        'source_metadata': t.source_metadata or {},
        'created_at': t.created_at.isoformat() if t.created_at else None,
        'artifacts': [
            {
                'id': str(a.id),
                'artifact_type': a.artifact_type,
                'object_key': a.object_key,
                'provenance': a.provenance or {},
                'created_at': a.created_at.isoformat() if a.created_at else None,
            }
            for a in artifacts
        ],
    }
    return _render(request, 'target_detail.html', {'active': 'targets', 'target': target,
    })


# ── Credentials ────────────────────────────────────────────────────────

@router.get('/credentials', response_class=HTMLResponse)
def credentials_page(request: Request, db: Session = Depends(get_db)):
    credentials = [
        {
            'id': str(c.id),
            'scope': c.scope,
            'provider': c.provider,
            'display_name': c.display_name,
            'owner_user_id': str(c.owner_user_id) if c.owner_user_id else None,
            'created_at': c.created_at.isoformat() if c.created_at else None,
        }
        for c in db.query(Credential).order_by(Credential.created_at.desc()).all()
    ]
    return _render(request, 'credentials.html', {'active': 'credentials', 'credentials': credentials,
    })


@router.get('/credentials/new', response_class=HTMLResponse)
def credential_form(request: Request):
    return _render(request, 'credential_form.html', {'active': 'credentials',
    })


@router.post('/credentials/new')
def credential_create(
    request: Request,
    db: Session = Depends(get_db),
    display_name: str = Form(...),
    provider: str = Form(...),
    scope: str = Form('user'),
    api_key: str = Form(''),
    owner_user_id: str = Form(''),
):
    encrypted = None
    masked = ""
    if api_key.strip():
        encrypted = encrypt_api_key(api_key.strip())
        masked = mask_api_key(api_key.strip())

    cred = Credential(
        owner_user_id=UUID(owner_user_id) if owner_user_id.strip() else None,
        scope=scope,
        provider=provider,
        display_name=display_name,
        secret_ref=masked or "(no key)",
        encrypted_api_key=encrypted,
    )
    db.add(cred)
    db.commit()
    return RedirectResponse(url=f'/credentials/{cred.id}', status_code=303)


@router.get('/credentials/{credential_id}', response_class=HTMLResponse)
def credential_detail_page(credential_id: UUID, request: Request, db: Session = Depends(get_db)):
    c = db.query(Credential).filter(Credential.id == credential_id).first()
    if not c:
        raise HTTPException(status_code=404, detail='Credential not found')
    # Derive a masked key for display — decrypt then mask, or fall back to secret_ref
    masked_key = None
    if c.encrypted_api_key:
        try:
            plain = decrypt_api_key(c.encrypted_api_key)
            masked_key = mask_api_key(plain)
        except CryptoError:
            masked_key = "(decryption failed)"
    elif c.secret_ref and c.secret_ref != "(no key)":
        masked_key = c.secret_ref  # legacy vault ref

    credential = {
        'id': str(c.id),
        'scope': c.scope,
        'provider': c.provider,
        'display_name': c.display_name,
        'masked_key': masked_key,
        'owner_user_id': str(c.owner_user_id) if c.owner_user_id else None,
        'created_at': c.created_at.isoformat() if c.created_at else None,
    }
    return _render(request, 'credential_detail.html', {'active': 'credentials', 'credential': credential,
    })


# ── Runtime Profiles ───────────────────────────────────────────────────

@router.get('/runtime-profiles', response_class=HTMLResponse)
def profiles_page(request: Request, db: Session = Depends(get_db)):
    profiles = [
        {
            'id': str(p.id),
            'name': p.name,
            'allow_exploits': p.allow_exploits,
            'settings': p.settings or {},
            'created_at': p.created_at.isoformat() if p.created_at else None,
        }
        for p in db.query(RuntimeProfileRecord).order_by(RuntimeProfileRecord.created_at.desc()).all()
    ]
    return _render(request, 'profiles.html', {'active': 'profiles', 'profiles': profiles,
    })


@router.get('/runtime-profiles/new', response_class=HTMLResponse)
def profile_form(request: Request):
    return _render(request, 'profile_form.html', {'active': 'profiles',
    })


@router.post('/runtime-profiles/new')
def profile_create(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    allow_exploits: str = Form('false'),
    settings: str = Form('{}'),
):
    profile = RuntimeProfileRecord(
        name=name,
        allow_exploits=allow_exploits.lower() == 'true',
        settings=json.loads(settings),
    )
    db.add(profile)
    db.commit()
    return RedirectResponse(url=f'/runtime-profiles/{profile.id}', status_code=303)


@router.get('/runtime-profiles/{profile_id}', response_class=HTMLResponse)
def profile_detail_page(profile_id: UUID, request: Request, db: Session = Depends(get_db)):
    p = db.query(RuntimeProfileRecord).filter(RuntimeProfileRecord.id == profile_id).first()
    if not p:
        raise HTTPException(status_code=404, detail='Runtime profile not found')
    profile = {
        'id': str(p.id),
        'name': p.name,
        'allow_exploits': p.allow_exploits,
        'settings': p.settings or {},
        'created_at': p.created_at.isoformat() if p.created_at else None,
    }
    return _render(request, 'profile_detail.html', {'active': 'profiles', 'profile': profile,
    })


# ── Artifacts ──────────────────────────────────────────────────────────

@router.get('/artifacts', response_class=HTMLResponse)
def artifacts_page(request: Request, db: Session = Depends(get_db)):
    artifacts = [
        {
            'id': str(a.id),
            'target_id': str(a.target_id) if a.target_id else None,
            'artifact_type': a.artifact_type,
            'object_key': a.object_key,
            'provenance': a.provenance or {},
            'created_at': a.created_at.isoformat() if a.created_at else None,
        }
        for a in db.query(Artifact).order_by(Artifact.created_at.desc()).all()
    ]
    return _render(request, 'artifacts.html', {'active': 'artifacts', 'artifacts': artifacts,
    })


@router.get('/artifacts/{artifact_id}', response_class=HTMLResponse)
def artifact_detail_page(artifact_id: UUID, request: Request, db: Session = Depends(get_db)):
    a = db.query(Artifact).filter(Artifact.id == artifact_id).first()
    if not a:
        raise HTTPException(status_code=404, detail='Artifact not found')
    artifact = {
        'id': str(a.id),
        'target_id': str(a.target_id) if a.target_id else None,
        'artifact_type': a.artifact_type,
        'object_key': a.object_key,
        'provenance': a.provenance or {},
        'created_at': a.created_at.isoformat() if a.created_at else None,
    }
    return _render(request, 'artifact_detail.html', {'active': 'artifacts', 'artifact': artifact,
    })


# ── Users ──────────────────────────────────────────────────────────────

@router.get('/users', response_class=HTMLResponse)
def users_page(request: Request, db: Session = Depends(get_db)):
    users = [
        {
            'id': str(u.id),
            'email': u.email,
            'display_name': u.display_name,
            'created_at': u.created_at.isoformat() if u.created_at else None,
        }
        for u in db.query(User).order_by(User.created_at.desc()).all()
    ]
    return _render(request, 'users.html', {'active': 'users', 'users': users,
    })


@router.get('/users/new', response_class=HTMLResponse)
def user_form(request: Request):
    return _render(request, 'user_form.html', {'active': 'users',
    })


@router.post('/users/new')
def user_create(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    display_name: str = Form(...),
):
    user = User(email=email, display_name=display_name)
    db.add(user)
    db.commit()
    return RedirectResponse(url=f'/users/{user.id}', status_code=303)


@router.get('/users/{user_id}', response_class=HTMLResponse)
def user_detail_page(user_id: UUID, request: Request, db: Session = Depends(get_db)):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    user = {
        'id': str(u.id),
        'email': u.email,
        'display_name': u.display_name,
        'created_at': u.created_at.isoformat() if u.created_at else None,
    }
    return _render(request, 'user_detail.html', {'active': 'users', 'user': user,
    })


# ── State Machine ──────────────────────────────────────────────────────

@router.get('/state-machine', response_class=HTMLResponse)
def state_machine_page(request: Request):
    return _render(request, 'state_machine.html', {'active': 'state-machine',
        'state_transitions': _STATE_TRANSITIONS,
    })
