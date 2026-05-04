"""Dashboard view routes — renders Jinja2 templates with DB data."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from moonwing.api.deps import get_db
from moonwing.api.deps import _get_settings
from moonwing.services.ai_provider_probe import PROVIDER_DEFAULT_MODELS
from moonwing.services.auth import create_session_token, create_service_token, hash_password, hash_service_token, verify_password
from moonwing.services.crypto import CryptoError, decrypt_api_key, encrypt_api_key, mask_api_key
from moonwing.services.finding_detail_display import build_finding_details, format_evidence_refs
from moonwing.services.finding_enrichment import enrich_finding_display
from moonwing.services.finding_insights import build_finding_insights
from moonwing.services.iam import audit, create_replacement_admin
from moonwing.services.ldap_sync import (
    LDAP_PROVIDER_PRESETS,
    LdapSyncError,
    build_ldap_diagnostics,
    config_data_from_model,
    fetch_ldap_entries,
    preview_ldap_sync,
    run_ldap_sync,
)
from moonwing.services.oidc import (
    build_authorization_url,
    create_oidc_state_token,
    oidc_enabled,
    parse_oidc_state_token,
    upsert_oidc_user,
    validate_oidc_claims,
)
from moonwing.services.management import build_management_summary
from moonwing.services.notifications import (
    ALL_EVENT_TYPES,
    EVENT_FINDING_REMEDIATED,
    EVENT_LABELS,
    EVENT_USER_ADDED,
    EVENT_USER_DELETED,
    EVENT_USER_PERMISSIONS_CHANGED,
    notify,
)
from moonwing.services.permissions import ASSIGNABLE_ROLES, require_role
from moonwing.services.run_activity import ACTIVE_RUN_STATUSES, serialize_run_activity
from moonwing.services.targets import normalize_target_metadata
from moonwing.services.user_origin import describe_user_origin
from moonwing.db.models import (
    Artifact,
    AuditEvent,
    Credential,
    Finding,
    LdapConfig,
    NotificationPreference,
    PrivilegedAccessGrant,
    ServiceAccountToken,
    SmtpConfig,
    Run,
    RuntimeProfileRecord,
    SensorEndpoint,
    SensorEvent,
    SensorTask,
    Target,
    User,
)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / 'templates')


def _render(request: Request, template: str, context: dict | None = None):
    ctx = context or {}
    ctx.setdefault('current_user', getattr(request.state, 'current_user', None))
    return templates.TemplateResponse(request, template, ctx)


def _require(request: Request, permission: str) -> None:
    current_user = getattr(request.state, 'current_user', None)
    try:
        require_role(current_user.get('role') if current_user else None, permission)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _json_form_value(raw: str, *, default: dict | None = None) -> dict:
    value = (raw or "").strip()
    if not value:
        return default or {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("JSON value must be an object.")
    return parsed


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


@router.get('/login', response_class=HTMLResponse)
def login_page(request: Request, next: str = '/', error: str = ''):
    message = 'OIDC is not configured.' if error == 'oidc_not_configured' else ''
    return _render(request, 'login.html', {'next': next, 'error': message, 'oidc_enabled': oidc_enabled(_get_settings())})


@router.post('/login')
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    identifier: str = Form(''),
    email: str = Form(''),
    password: str = Form(...),
    next: str = Form('/'),
):
    login_identifier = (identifier or email).strip().lower()
    user = db.query(User).filter(User.email == login_identifier).first()
    if not user or user.status != 'active' or user.is_service_account or not verify_password(password, user.password_hash):
        audit(db, action='login_failed', resource_type='user', resource_id=login_identifier, outcome='failure')
        db.commit()
        return _render(request, 'login.html', {
            'next': next or '/',
            'error': 'Invalid email or password',
            'oidc_enabled': oidc_enabled(_get_settings()),
        })

    token = create_session_token(str(user.id), user.role, secret=_get_settings().session_secret)
    user.last_login_at = func.now()
    audit(db, action='login', resource_type='user', actor_user_id=user.id, resource_id=str(user.id))
    db.commit()
    response = RedirectResponse(url=next or '/', status_code=303)
    response.set_cookie(
        'moonwing_session',
        token,
        httponly=True,
        samesite='lax',
        secure=False,
        max_age=60 * 60 * 12,
    )
    return response


@router.get('/auth/oidc/login')
def oidc_login(next: str = '/'):
    settings = _get_settings()
    if not oidc_enabled(settings):
        return RedirectResponse(url='/login?next=/&error=oidc_not_configured', status_code=303)
    nonce = secrets.token_urlsafe(24)
    state = create_oidc_state_token(next_url=next, nonce=nonce, secret=settings.session_secret)
    response = RedirectResponse(url=build_authorization_url(settings, state=state, nonce=nonce), status_code=303)
    response.set_cookie(
        'moonwing_oidc_state',
        state,
        httponly=True,
        samesite='lax',
        secure=settings.oidc_redirect_uri.startswith('https://'),
        max_age=600,
    )
    return response


@router.get('/auth/oidc/callback')
def oidc_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str = Query(''),
    state: str = Query(''),
    error: str = Query(''),
):
    settings = _get_settings()
    if error:
        return _render(request, 'login.html', {'next': '/', 'error': f'OIDC sign-in failed: {error}', 'oidc_enabled': oidc_enabled(settings)})
    cookie_state = request.cookies.get('moonwing_oidc_state')
    parsed_state = parse_oidc_state_token(state, secret=settings.session_secret)
    parsed_cookie_state = parse_oidc_state_token(cookie_state, secret=settings.session_secret)
    if not code or not parsed_state or parsed_state != parsed_cookie_state:
        return _render(request, 'login.html', {'next': '/', 'error': 'OIDC state validation failed. Please try again.', 'oidc_enabled': oidc_enabled(settings)})

    try:
        with httpx.Client(timeout=10.0) as client:
            token_response = client.post(
                settings.oidc_token_endpoint,
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': settings.oidc_redirect_uri,
                    'client_id': settings.oidc_client_id,
                    'client_secret': settings.oidc_client_secret,
                },
                headers={'Accept': 'application/json'},
            )
            token_response.raise_for_status()
            access_token = token_response.json().get('access_token')
            id_token = token_response.json().get('id_token')
            if not access_token:
                raise ValueError('OIDC token response did not include an access token')
            userinfo_response = client.get(
                settings.oidc_userinfo_endpoint,
                headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'},
            )
            userinfo_response.raise_for_status()
            claims = validate_oidc_claims(
                userinfo_response.json(),
                id_token=id_token,
                expected_issuer=settings.oidc_issuer,
                expected_nonce=parsed_state['nonce'],
            )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return _render(request, 'login.html', {'next': '/', 'error': f'OIDC sign-in failed: {exc}', 'oidc_enabled': oidc_enabled(settings)})

    try:
        user = upsert_oidc_user(
            db,
            issuer=claims['issuer'],
            subject=claims['subject'],
            email=claims['email'],
            display_name=claims['display_name'],
            default_role=settings.oidc_default_role,
        )
    except ValueError as exc:
        return _render(request, 'login.html', {'next': '/', 'error': str(exc), 'oidc_enabled': oidc_enabled(settings)})

    db.flush()
    token = create_session_token(str(user.id), user.role, secret=settings.session_secret)
    user.last_login_at = func.now()
    audit(db, action='oidc_login', resource_type='user', actor_user_id=user.id, resource_id=str(user.id))
    db.commit()
    response = RedirectResponse(url=parsed_state['next'] or '/', status_code=303)
    response.delete_cookie('moonwing_oidc_state')
    response.set_cookie(
        'moonwing_session',
        token,
        httponly=True,
        samesite='lax',
        secure=settings.oidc_redirect_uri.startswith('https://'),
        max_age=60 * 60 * 12,
    )
    return response


@router.get('/logout')
def logout():
    response = RedirectResponse(url='/login', status_code=303)
    response.delete_cookie('moonwing_session')
    return response


@router.get('/setup/admin', response_class=HTMLResponse)
def first_run_admin_page(request: Request):
    current_user = getattr(request.state, 'current_user', None)
    if not current_user or not current_user.get('is_bootstrap'):
        return RedirectResponse(url='/', status_code=303)
    return _render(request, 'setup_admin.html', {'active': 'settings', 'error': ''})


@router.post('/setup/admin')
def first_run_admin_create(
    request: Request,
    db: Session = Depends(get_db),
    identifier: str = Form(...),
    display_name: str = Form(...),
    password: str = Form(...),
):
    current_user = getattr(request.state, 'current_user', None)
    if not current_user or not current_user.get('is_bootstrap'):
        return RedirectResponse(url='/', status_code=303)
    bootstrap_user = db.get(User, UUID(current_user['id']))
    if not bootstrap_user:
        raise HTTPException(status_code=404, detail='Bootstrap user not found')
    try:
        new_admin = create_replacement_admin(
            db,
            bootstrap_user=bootstrap_user,
            identifier=identifier,
            display_name=display_name,
            password=password,
        )
    except ValueError as exc:
        return _render(request, 'setup_admin.html', {'active': 'settings', 'error': str(exc)})

    token = create_session_token(str(new_admin.id), new_admin.role, secret=_get_settings().session_secret)
    response = RedirectResponse(url='/setup/user', status_code=303)
    response.set_cookie(
        'moonwing_session',
        token,
        httponly=True,
        samesite='lax',
        secure=False,
        max_age=60 * 60 * 12,
    )
    return response


@router.get('/setup/user', response_class=HTMLResponse)
def first_run_user_page(request: Request):
    _require(request, 'manage_users')
    return _render(request, 'setup_user.html', {'active': 'settings', 'error': ''})


@router.post('/setup/user')
def first_run_user_create(
    request: Request,
    db: Session = Depends(get_db),
    identifier: str = Form(''),
    display_name: str = Form(''),
    password: str = Form(''),
    skip: str = Form('false'),
):
    _require(request, 'manage_users')
    if skip.lower() == 'true':
        audit(db, action='first_run_regular_user_skip', resource_type='user', actor_user_id=UUID(request.state.current_user['id']))
        db.commit()
        return RedirectResponse(url='/', status_code=303)

    clean_identifier = identifier.strip().lower()
    if not clean_identifier or not display_name.strip() or not password:
        return _render(request, 'setup_user.html', {'active': 'settings', 'error': 'Identifier, display name, and password are required.'})
    if db.query(User).filter(User.email == clean_identifier).first():
        return _render(request, 'setup_user.html', {'active': 'settings', 'error': 'That identifier is already in use.'})

    user = User(
        email=clean_identifier,
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role='viewer',
        status='active',
        is_service_account=False,
        is_bootstrap=False,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    audit(db, action='first_run_regular_user_create', resource_type='user', actor_user_id=UUID(request.state.current_user['id']), resource_id=clean_identifier)
    db.commit()
    return RedirectResponse(url='/', status_code=303)


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
    _require(request, 'view')
    status_counts = {}
    for status in ['queued', 'staging', 'running', 'normalizing', 'completed', 'failed', 'canceled', 'needs_review']:
        status_counts[status] = db.query(func.count(Run.id)).filter(Run.status == status).scalar() or 0
    severity_counts = {}
    for sev in ['critical', 'high', 'medium', 'low', 'info', 'unknown']:
        severity_counts[sev] = db.query(func.count(Finding.id)).filter(Finding.severity == sev).scalar() or 0
    stats = {
        'total_runs': db.query(func.count(Run.id)).scalar() or 0,
        'completed_runs': status_counts['completed'],
        'failed_runs': status_counts['failed'],
        'active_runs': status_counts['queued'] + status_counts['staging'] + status_counts['running'] + status_counts['normalizing'],
        'total_findings': db.query(func.count(Finding.id)).scalar() or 0,
        'critical_findings': severity_counts['critical'],
        'high_findings': severity_counts['high'],
        'medium_findings': severity_counts['medium'],
        'low_findings': severity_counts['low'],
        'info_findings': severity_counts['info'],
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
        'stats': stats,
        'status_counts': status_counts,
        'severity_counts': severity_counts,
        'recent_runs': recent_runs,
        'recent_findings': recent_findings,
    })


# ── Runs ───────────────────────────────────────────────────────────────

@router.get('/runs', response_class=HTMLResponse)
def runs_page(request: Request, db: Session = Depends(get_db)):
    _require(request, 'view')
    runs = [
        _serialize_run(r)
        for r in db.query(Run).order_by(Run.created_at.desc()).all()
    ]
    return _render(request, 'runs.html', {'active': 'runs', 'runs': runs})


@router.get('/sensors', response_class=HTMLResponse)
def sensors_page(request: Request, db: Session = Depends(get_db)):
    _require(request, 'view')
    endpoints = db.query(SensorEndpoint).order_by(SensorEndpoint.last_seen_at.desc().nullslast()).limit(250).all()
    event_counts = dict(
        db.query(SensorEvent.sensor_id, func.count(SensorEvent.id))
        .group_by(SensorEvent.sensor_id)
        .all()
    )
    queued_counts = dict(
        db.query(SensorTask.sensor_id, func.count(SensorTask.id))
        .filter(SensorTask.status == "queued")
        .group_by(SensorTask.sensor_id)
        .all()
    )
    sensors = [
        {
            'id': str(sensor.id),
            'hostname': sensor.hostname,
            'platform': sensor.platform,
            'os_name': sensor.os_name,
            'status': sensor.status,
            'sensor_version': sensor.sensor_version,
            'labels': sensor.labels or [],
            'last_seen_at': sensor.last_seen_at.isoformat() if sensor.last_seen_at else '',
            'event_count': event_counts.get(sensor.id, 0),
            'queued_tasks': queued_counts.get(sensor.id, 0),
        }
        for sensor in endpoints
    ]
    return _render(request, 'sensors.html', {'active': 'sensors', 'sensors': sensors})


@router.get('/runs/new', response_class=HTMLResponse)
def run_form(request: Request, db: Session = Depends(get_db), target: str | None = None):
    _require(request, 'launch_scan')
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
        'active': 'launch',
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
    model: str = Form('gpt-5.5'),
    execution_mode: str = Form('api'),
):
    _require(request, 'launch_scan')
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
    db.flush()
    audit(db, action='scan_launch', resource_type='run', actor_user_id=UUID(request.state.current_user['id']), resource_id=str(run.id), metadata={'job_family': job_family, 'target_id': target_id})
    db.commit()
    return RedirectResponse(url=f'/runs/{run.id}', status_code=303)


@router.get('/runs/{run_id}', response_class=HTMLResponse)
def run_detail_page(run_id: UUID, request: Request, db: Session = Depends(get_db)):
    _require(request, 'view')
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail='Run not found')
    findings = db.query(Finding).filter(Finding.run_id == run.id).order_by(Finding.created_at.desc()).all()
    run_data = _serialize_run(run)
    run_data['is_active'] = run.status in ACTIVE_RUN_STATUSES
    run_data['activity'] = list((run.execution_snapshot or {}).get('activity') or [])
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

@router.get('/api/runs/{run_id}/activity')
def run_activity_api(run_id: UUID, request: Request, db: Session = Depends(get_db)):
    _require(request, 'view')
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail='Run not found')
    return serialize_run_activity(run)


@router.get('/findings', response_class=HTMLResponse)
def findings_page(request: Request, db: Session = Depends(get_db)):
    _require(request, 'view')
    rows = db.query(Finding, Run, Target).join(Run, Finding.run_id == Run.id).outerjoin(Target, Run.target_id == Target.id).order_by(Finding.created_at.desc()).all()
    findings = []
    product_options = set()
    for f, run, target in rows:
        enrichment = enrich_finding_display(
            title=f.title,
            evidence_refs=f.evidence_refs or [],
            target_display_name=target.display_name if target else None,
            target_metadata=target.source_metadata if target else {},
            job_family=run.job_family,
        )
        display_details = build_finding_details(stored_details=f.details or {}, enrichment=enrichment)
        product_affected = display_details['product_affected']
        product_options.add(product_affected)
        findings.append({
            'id': str(f.id),
            'run_id': str(f.run_id),
            'title': f.title,
            'severity': f.severity,
            'product_affected': product_affected,
            'host_affected': display_details['host_affected'],
            'scan_type': display_details['scan_type'],
            'evidence_refs': f.evidence_refs or [],
            'created_at': f.created_at.isoformat() if f.created_at else None,
        })
    severity_counts = {}
    for sev in ['critical', 'high', 'medium', 'low', 'info', 'unknown']:
        severity_counts[sev] = db.query(func.count(Finding.id)).filter(Finding.severity == sev).scalar() or 0
    return _render(request, 'findings.html', {'active': 'findings',
        'findings': findings,
        'severity_counts': severity_counts,
        'product_options': sorted(product_options),
    })


@router.get('/findings/{finding_id}', response_class=HTMLResponse)
def finding_detail_page(finding_id: UUID, request: Request, db: Session = Depends(get_db)):
    _require(request, 'view')
    row = db.query(Finding, Run, Target).join(Run, Finding.run_id == Run.id).outerjoin(Target, Run.target_id == Target.id).filter(Finding.id == finding_id).first()
    if not row:
        raise HTTPException(status_code=404, detail='Finding not found')
    f, run, target = row
    enrichment = enrich_finding_display(
        title=f.title,
        evidence_refs=f.evidence_refs or [],
        target_display_name=target.display_name if target else None,
        target_metadata=target.source_metadata if target else {},
        job_family=run.job_family,
    )
    display_details = build_finding_details(stored_details=f.details or {}, enrichment=enrichment)
    insights = build_finding_insights(
        title=f.title,
        severity=f.severity,
        finding_details=display_details,
        stored_details=f.details or {},
        evidence_refs=f.evidence_refs or [],
        target_metadata=target.source_metadata if target else {},
    )
    finding = {
        'id': str(f.id),
        'run_id': str(f.run_id),
        'title': f.title,
        'severity': f.severity,
        'evidence_refs': f.evidence_refs or [],
        'evidence_items': format_evidence_refs(f.evidence_refs or []),
        'product_affected': display_details['product_affected'],
        'host_affected': display_details['host_affected'],
        'affected_ports': display_details['affected_ports'],
        'scan_type': display_details['scan_type'],
        'description': display_details['description'],
        'impact': display_details['impact'],
        'remediation': display_details['remediation'],
        'confidence': display_details['confidence'],
        'references': display_details['references'],
        'insights': insights,
        'target_name': target.display_name if target else None,
        'run_job_family': run.job_family,
        'created_at': f.created_at.isoformat() if f.created_at else None,
    }
    return _render(request, 'finding_detail.html', {'active': 'findings', 'finding': finding,
    })


# ── Targets ────────────────────────────────────────────────────────────

@router.get('/targets', response_class=HTMLResponse)
def targets_page(request: Request, db: Session = Depends(get_db)):
    _require(request, 'view')
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
    return _render(request, 'targets.html', {'active': 'settings', 'targets': targets,
    })


@router.get('/targets/new', response_class=HTMLResponse)
def target_form(request: Request):
    _require(request, 'manage_targets')
    return _render(request, 'target_form.html', {'active': 'settings', 'target': None, 'action': '/targets/new', 'error': '',
    })


@router.post('/targets/new')
def target_create(
    request: Request,
    db: Session = Depends(get_db),
    display_name: str = Form(...),
    target_type: str = Form(...),
    source_metadata: str = Form('{}'),
):
    _require(request, 'manage_targets')
    try:
        parsed_metadata = _json_form_value(source_metadata)
        parsed_metadata = normalize_target_metadata(
            target_type=target_type,
            display_name=display_name,
            source_metadata=parsed_metadata,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        return _render(request, 'target_form.html', {
            'active': 'settings',
            'target': {'display_name': display_name, 'target_type': target_type, 'source_metadata': source_metadata},
            'action': '/targets/new',
            'error': f'Invalid metadata JSON: {exc}',
        })
    target = Target(
        target_type=target_type,
        display_name=display_name,
        source_metadata=parsed_metadata,
    )
    db.add(target)
    db.commit()
    return RedirectResponse(url=f'/targets/{target.id}', status_code=303)


@router.get('/targets/{target_id}', response_class=HTMLResponse)
def target_detail_page(target_id: UUID, request: Request, db: Session = Depends(get_db), error: str = ''):
    _require(request, 'view')
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
    return _render(request, 'target_detail.html', {'active': 'settings', 'target': target, 'error': error,
    })


@router.get('/targets/{target_id}/edit', response_class=HTMLResponse)
def target_edit_page(target_id: UUID, request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_targets')
    t = db.query(Target).filter(Target.id == target_id).first()
    if not t:
        raise HTTPException(status_code=404, detail='Target not found')
    return _render(request, 'target_form.html', {
        'active': 'settings',
        'target': {
            'id': str(t.id),
            'target_type': t.target_type,
            'display_name': t.display_name,
            'source_metadata': t.source_metadata or {},
        },
        'action': f'/targets/{t.id}/edit',
        'error': '',
    })


@router.post('/targets/{target_id}/edit')
def target_update(
    target_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    display_name: str = Form(...),
    target_type: str = Form(...),
    source_metadata: str = Form('{}'),
):
    _require(request, 'manage_targets')
    t = db.query(Target).filter(Target.id == target_id).first()
    if not t:
        raise HTTPException(status_code=404, detail='Target not found')
    try:
        parsed_metadata = _json_form_value(source_metadata)
        parsed_metadata = normalize_target_metadata(
            target_type=target_type,
            display_name=display_name,
            source_metadata=parsed_metadata,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        return _render(request, 'target_form.html', {
            'active': 'settings',
            'target': {'id': str(t.id), 'display_name': display_name, 'target_type': target_type, 'source_metadata': source_metadata},
            'action': f'/targets/{t.id}/edit',
            'error': f'Invalid metadata JSON: {exc}',
        })
    t.display_name = display_name.strip()
    t.target_type = target_type
    t.source_metadata = parsed_metadata
    audit(db, action='target_update', resource_type='target', actor_user_id=UUID(request.state.current_user['id']), resource_id=str(t.id))
    db.commit()
    return RedirectResponse(url=f'/targets/{t.id}', status_code=303)


@router.post('/targets/{target_id}/delete')
def target_delete(target_id: UUID, request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_targets')
    t = db.query(Target).filter(Target.id == target_id).first()
    if not t:
        raise HTTPException(status_code=404, detail='Target not found')
    db.delete(t)
    audit(db, action='target_delete', resource_type='target', actor_user_id=UUID(request.state.current_user['id']), resource_id=str(t.id))
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        return target_detail_page(target_id, request, db, error='Target cannot be deleted while it is referenced by runs or artifacts.')
    return RedirectResponse(url='/settings?tab=targets', status_code=303)


# ── Credentials ────────────────────────────────────────────────────────

@router.get('/credentials', response_class=HTMLResponse)
def credentials_page(request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_credentials')
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
    return _render(request, 'credentials.html', {'active': 'settings', 'credentials': credentials,
    })


@router.get('/credentials/new', response_class=HTMLResponse)
def credential_form(request: Request):
    _require(request, 'manage_credentials')
    return _render(request, 'credential_form.html', {'active': 'settings', 'credential': None, 'action': '/credentials/new', 'error': '',
    })


@router.get('/credentials/connect', response_class=HTMLResponse)
def credential_connect_form(request: Request):
    _require(request, 'manage_credentials')
    return _render(request, 'credential_connect.html', {
        'active': 'settings',
        'provider_defaults': PROVIDER_DEFAULT_MODELS,
    })


@router.post('/credentials/connect')
def credential_connect_create(
    request: Request,
    db: Session = Depends(get_db),
    display_name: str = Form(...),
    provider: str = Form(...),
    api_key: str = Form(''),
    owner_user_id: str = Form(''),
):
    _require(request, 'manage_credentials')
    encrypted = None
    masked = "(no key)"
    clean_key = api_key.strip()
    if clean_key:
        encrypted = encrypt_api_key(clean_key)
        masked = mask_api_key(clean_key)

    cred = Credential(
        owner_user_id=UUID(owner_user_id) if owner_user_id.strip() else None,
        scope='shared',
        provider=provider,
        display_name=display_name,
        secret_ref=masked,
        encrypted_api_key=encrypted,
    )
    db.add(cred)
    db.flush()
    audit(db, action='credential_create', resource_type='credential', actor_user_id=UUID(request.state.current_user['id']), resource_id=display_name, metadata={'provider': provider, 'scope': 'shared'})
    db.commit()
    return RedirectResponse(url=f'/credentials/{cred.id}', status_code=303)


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
    _require(request, 'manage_credentials')
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
    db.flush()
    audit(db, action='credential_create', resource_type='credential', actor_user_id=UUID(request.state.current_user['id']), resource_id=display_name, metadata={'provider': provider, 'scope': scope})
    db.commit()
    return RedirectResponse(url=f'/credentials/{cred.id}', status_code=303)


@router.get('/credentials/{credential_id}', response_class=HTMLResponse)
def credential_detail_page(credential_id: UUID, request: Request, db: Session = Depends(get_db), error: str = ''):
    _require(request, 'manage_credentials')
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
    return _render(request, 'credential_detail.html', {'active': 'settings', 'credential': credential, 'error': error,
    })


@router.get('/credentials/{credential_id}/edit', response_class=HTMLResponse)
def credential_edit_page(credential_id: UUID, request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_credentials')
    c = db.query(Credential).filter(Credential.id == credential_id).first()
    if not c:
        raise HTTPException(status_code=404, detail='Credential not found')
    return _render(request, 'credential_form.html', {
        'active': 'settings',
        'credential': {
            'id': str(c.id),
            'scope': c.scope,
            'provider': c.provider,
            'display_name': c.display_name,
            'owner_user_id': str(c.owner_user_id) if c.owner_user_id else '',
        },
        'action': f'/credentials/{c.id}/edit',
        'error': '',
    })


@router.post('/credentials/{credential_id}/edit')
def credential_update(
    credential_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    display_name: str = Form(...),
    provider: str = Form(...),
    scope: str = Form('user'),
    api_key: str = Form(''),
    owner_user_id: str = Form(''),
):
    _require(request, 'manage_credentials')
    c = db.query(Credential).filter(Credential.id == credential_id).first()
    if not c:
        raise HTTPException(status_code=404, detail='Credential not found')
    c.display_name = display_name.strip()
    c.provider = provider
    c.scope = scope
    c.owner_user_id = UUID(owner_user_id) if owner_user_id.strip() else None
    if api_key.strip():
        c.encrypted_api_key = encrypt_api_key(api_key.strip())
        c.secret_ref = mask_api_key(api_key.strip())
    audit(db, action='credential_update', resource_type='credential', actor_user_id=UUID(request.state.current_user['id']), resource_id=str(c.id), metadata={'provider': provider, 'scope': scope})
    db.commit()
    return RedirectResponse(url=f'/credentials/{c.id}', status_code=303)


@router.post('/credentials/{credential_id}/delete')
def credential_delete(credential_id: UUID, request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_credentials')
    c = db.query(Credential).filter(Credential.id == credential_id).first()
    if not c:
        raise HTTPException(status_code=404, detail='Credential not found')
    db.delete(c)
    audit(db, action='credential_delete', resource_type='credential', actor_user_id=UUID(request.state.current_user['id']), resource_id=str(c.id))
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        return credential_detail_page(credential_id, request, db, error='Credential cannot be deleted while it is referenced by runs.')
    return RedirectResponse(url='/settings?tab=credentials', status_code=303)


# ── Runtime Profiles ───────────────────────────────────────────────────

@router.get('/runtime-profiles', response_class=HTMLResponse)
def profiles_page(request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_runtime_profiles')
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
    return _render(request, 'profiles.html', {'active': 'settings', 'profiles': profiles,
    })


@router.get('/runtime-profiles/new', response_class=HTMLResponse)
def profile_form(request: Request):
    _require(request, 'manage_runtime_profiles')
    return _render(request, 'profile_form.html', {'active': 'settings', 'profile': None, 'action': '/runtime-profiles/new', 'error': '',
    })


@router.post('/runtime-profiles/new')
def profile_create(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    allow_exploits: str = Form('false'),
    settings: str = Form('{}'),
):
    _require(request, 'manage_runtime_profiles')
    try:
        parsed_settings = _json_form_value(settings)
    except (ValueError, json.JSONDecodeError) as exc:
        return _render(request, 'profile_form.html', {
            'active': 'settings',
            'profile': {'name': name, 'allow_exploits': allow_exploits.lower() == 'true', 'settings': settings},
            'action': '/runtime-profiles/new',
            'error': f'Invalid settings JSON: {exc}',
        })
    profile = RuntimeProfileRecord(
        name=name,
        allow_exploits=allow_exploits.lower() == 'true',
        settings=parsed_settings,
    )
    db.add(profile)
    db.commit()
    return RedirectResponse(url=f'/runtime-profiles/{profile.id}', status_code=303)


@router.get('/runtime-profiles/{profile_id}', response_class=HTMLResponse)
def profile_detail_page(profile_id: UUID, request: Request, db: Session = Depends(get_db), error: str = ''):
    _require(request, 'manage_runtime_profiles')
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
    return _render(request, 'profile_detail.html', {'active': 'settings', 'profile': profile, 'error': error,
    })


@router.get('/runtime-profiles/{profile_id}/edit', response_class=HTMLResponse)
def profile_edit_page(profile_id: UUID, request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_runtime_profiles')
    p = db.query(RuntimeProfileRecord).filter(RuntimeProfileRecord.id == profile_id).first()
    if not p:
        raise HTTPException(status_code=404, detail='Runtime profile not found')
    return _render(request, 'profile_form.html', {
        'active': 'settings',
        'profile': {
            'id': str(p.id),
            'name': p.name,
            'allow_exploits': p.allow_exploits,
            'settings': p.settings or {},
        },
        'action': f'/runtime-profiles/{p.id}/edit',
        'error': '',
    })


@router.post('/runtime-profiles/{profile_id}/edit')
def profile_update(
    profile_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    allow_exploits: str = Form('false'),
    settings: str = Form('{}'),
):
    _require(request, 'manage_runtime_profiles')
    p = db.query(RuntimeProfileRecord).filter(RuntimeProfileRecord.id == profile_id).first()
    if not p:
        raise HTTPException(status_code=404, detail='Runtime profile not found')
    try:
        parsed_settings = _json_form_value(settings)
    except (ValueError, json.JSONDecodeError) as exc:
        return _render(request, 'profile_form.html', {
            'active': 'settings',
            'profile': {'id': str(p.id), 'name': name, 'allow_exploits': allow_exploits.lower() == 'true', 'settings': settings},
            'action': f'/runtime-profiles/{p.id}/edit',
            'error': f'Invalid settings JSON: {exc}',
        })
    p.name = name.strip()
    p.allow_exploits = allow_exploits.lower() == 'true'
    p.settings = parsed_settings
    audit(db, action='runtime_profile_update', resource_type='runtime_profile', actor_user_id=UUID(request.state.current_user['id']), resource_id=str(p.id))
    db.commit()
    return RedirectResponse(url=f'/runtime-profiles/{p.id}', status_code=303)


@router.post('/runtime-profiles/{profile_id}/delete')
def profile_delete(profile_id: UUID, request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_runtime_profiles')
    p = db.query(RuntimeProfileRecord).filter(RuntimeProfileRecord.id == profile_id).first()
    if not p:
        raise HTTPException(status_code=404, detail='Runtime profile not found')
    db.delete(p)
    audit(db, action='runtime_profile_delete', resource_type='runtime_profile', actor_user_id=UUID(request.state.current_user['id']), resource_id=str(p.id))
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        return profile_detail_page(profile_id, request, db, error='Runtime profile cannot be deleted while it is referenced by runs.')
    return RedirectResponse(url='/settings?tab=profiles', status_code=303)


# ── Artifacts ──────────────────────────────────────────────────────────

@router.get('/artifacts', response_class=HTMLResponse)
def artifacts_page(request: Request, db: Session = Depends(get_db)):
    _require(request, 'view')
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
    _require(request, 'view')
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
    _require(request, 'manage_users')
    users = []
    for u in db.query(User).order_by(User.created_at.desc()).all():
        origin = describe_user_origin(u)
        users.append({
            'id': str(u.id),
            'email': u.email,
            'display_name': u.display_name,
            'role': u.role,
            'source_label': origin.source_label,
            'role_source_label': origin.role_source_label,
            'status': u.status,
            'is_service_account': u.is_service_account,
            'created_at': u.created_at.isoformat() if u.created_at else None,
        })
    return _render(request, 'users.html', {'active': 'management', 'users': users,
    })


@router.get('/users/new', response_class=HTMLResponse)
def user_form(request: Request):
    _require(request, 'manage_users')
    return _render(request, 'user_form.html', {'active': 'management',
    })


@router.post('/users/new')
def user_create(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    display_name: str = Form(...),
    password: str = Form(''),
    role: str = Form('viewer'),
    status: str = Form('active'),
    is_service_account: str = Form('false'),
):
    _require(request, 'manage_users')
    service_account = is_service_account.lower() == 'true'
    user = User(
        email=email.strip().lower(),
        display_name=display_name,
        password_hash=hash_password(password) if password.strip() and not service_account else None,
        role='service_account' if service_account else role,
        status=status,
        is_service_account=service_account,
    )
    db.add(user)
    db.flush()
    audit(db, action='user_create', resource_type='user', actor_user_id=UUID(request.state.current_user['id']), resource_id=email.strip().lower(), metadata={'role': user.role, 'service_account': service_account})
    db.commit()
    return RedirectResponse(url=f'/users/{user.id}', status_code=303)


@router.get('/users/{user_id}', response_class=HTMLResponse)
def user_detail_page(user_id: UUID, request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_users')
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    origin = describe_user_origin(u)
    user = {
        'id': str(u.id),
        'email': u.email,
        'display_name': u.display_name,
        'role': u.role,
        'source_label': origin.source_label,
        'role_source_label': origin.role_source_label,
        'origin_details': origin.details,
        'status': u.status,
        'is_service_account': u.is_service_account,
        'created_at': u.created_at.isoformat() if u.created_at else None,
    }
    tokens = [
        {
            'id': str(t.id),
            'display_name': t.display_name,
            'status': t.status,
            'last_used_at': t.last_used_at.isoformat() if t.last_used_at else None,
            'created_at': t.created_at.isoformat() if t.created_at else None,
        }
        for t in db.query(ServiceAccountToken).filter(ServiceAccountToken.user_id == u.id).order_by(ServiceAccountToken.created_at.desc()).all()
    ]
    return _render(request, 'user_detail.html', {'active': 'management', 'user': user, 'tokens': tokens,
    })


@router.post('/users/{user_id}/service-tokens')
def service_token_create(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    display_name: str = Form(...),
):
    _require(request, 'manage_service_accounts')
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_service_account:
        raise HTTPException(status_code=404, detail='Service account not found')
    plain_token = create_service_token()
    token = ServiceAccountToken(
        user_id=user.id,
        display_name=display_name,
        token_hash=hash_service_token(plain_token),
    )
    db.add(token)
    db.flush()
    audit(db, action='service_token_create', resource_type='service_account_token', actor_user_id=UUID(request.state.current_user['id']), resource_id=str(user.id))
    db.commit()
    return _render(request, 'service_token_created.html', {
        'active': 'management',
        'user': {'id': str(user.id), 'display_name': user.display_name},
        'token': plain_token,
    })


@router.get('/management')
def management_redirect(request: Request):
    _require(request, 'manage_users')
    return RedirectResponse(url='/management/iam', status_code=303)


@router.get('/management/iam', response_class=HTMLResponse)
def management_iam_page(request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_users')
    users = [
        {
            'id': str(u.id),
            'email': u.email,
            'display_name': u.display_name,
            'role': u.role,
            'status': u.status,
            'is_service_account': u.is_service_account,
            'created_at': u.created_at.isoformat() if u.created_at else None,
        }
        for u in db.query(User).order_by(User.created_at.desc()).all()
    ]
    recent_audit_events = [
        {
            'action': a.action,
            'resource_type': a.resource_type,
            'resource_id': a.resource_id,
            'outcome': a.outcome,
            'created_at': a.created_at.isoformat() if a.created_at else None,
        }
        for a in db.query(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(8).all()
    ]
    return _render(request, 'management_iam.html', {
        'active': 'management',
        'summary': build_management_summary(db),
        'users': users,
        'recent_audit_events': recent_audit_events,
    })


# ── Settings (combined) ────────────────────────────────────────────────

@router.get('/management/privileged-access', response_class=HTMLResponse)
def management_privileged_access_page(request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_users')
    grants_query = (
        db.query(PrivilegedAccessGrant, User)
        .outerjoin(User, User.id == PrivilegedAccessGrant.user_id)
        .order_by(PrivilegedAccessGrant.created_at.desc())
    )
    grants = [
        {
            'id': str(grant.id),
            'user_id': str(grant.user_id),
            'user_email': user.email if user else '',
            'permission': grant.permission,
            'status': grant.status,
            'reason': grant.reason,
            'expires_at': grant.expires_at.isoformat() if grant.expires_at else None,
            'created_at': grant.created_at.isoformat() if grant.created_at else None,
        }
        for grant, user in grants_query.limit(20).all()
    ]
    summary = {
        'active': db.query(func.count(PrivilegedAccessGrant.id)).filter(PrivilegedAccessGrant.status == 'active').scalar() or 0,
        'pending': db.query(func.count(PrivilegedAccessGrant.id)).filter(PrivilegedAccessGrant.status == 'pending').scalar() or 0,
        'expired': db.query(func.count(PrivilegedAccessGrant.id)).filter(PrivilegedAccessGrant.status == 'expired').scalar() or 0,
    }
    return _render(request, 'management_privileged_access.html', {
        'active': 'management',
        'grants': grants,
        'summary': summary,
    })


@router.get('/management/directory', response_class=HTMLResponse)
def directory_config_page(request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_users')
    ldap = db.get(LdapConfig, 1)
    flash_message = request.query_params.get('msg')
    flash_type = request.query_params.get('type', 'info')
    return _render(request, 'management_directory.html', {
        'active': 'management',
        'ldap': ldap,
        'flash_message': flash_message,
        'flash_type': flash_type,
        'provider_types': [
            {'value': key, 'label': preset['label']}
            for key, preset in LDAP_PROVIDER_PRESETS.items()
        ],
        'provider_presets': LDAP_PROVIDER_PRESETS,
        'roles': ASSIGNABLE_ROLES,
        'preview': getattr(request.state, 'ldap_preview', None),
        'diagnostics': getattr(request.state, 'ldap_diagnostics', None),
    })


@router.post('/management/directory/config')
def directory_config_save(
    request: Request,
    db: Session = Depends(get_db),
    provider_type: str = Form('generic'),
    host: str = Form(...),
    port: int = Form(636),
    use_ssl: str = Form(''),
    start_tls: str = Form(''),
    bind_dn: str = Form(''),
    bind_password: str = Form(''),
    base_dn: str = Form(...),
    user_filter: str = Form('(objectClass=person)'),
    email_attribute: str = Form('mail'),
    display_name_attribute: str = Form('displayName'),
    username_attribute: str = Form('uid'),
    member_of_attribute: str = Form('memberOf'),
    admin_group_dns: str = Form(''),
    security_engineer_group_dns: str = Form(''),
    operator_group_dns: str = Form(''),
    analyst_group_dns: str = Form(''),
    viewer_group_dns: str = Form(''),
    default_role: str = Form('viewer'),
    auto_disable_missing: str = Form(''),
    enabled: str = Form(''),
):
    _require(request, 'manage_users')
    if provider_type not in {'generic', 'ad', 'authentik'}:
        raise HTTPException(status_code=400, detail='Invalid directory provider type')
    if default_role not in {r['value'] for r in ASSIGNABLE_ROLES}:
        raise HTTPException(status_code=400, detail='Invalid default role')
    ldap = db.get(LdapConfig, 1)
    if ldap is None:
        ldap = LdapConfig(id=1, host=host.strip(), base_dn=base_dn.strip())
        db.add(ldap)
    ldap.provider_type = provider_type
    ldap.host = host.strip()
    ldap.port = port
    ldap.use_ssl = use_ssl == 'true'
    ldap.start_tls = start_tls == 'true'
    ldap.bind_dn = bind_dn.strip() or None
    if bind_password:
        ldap.encrypted_bind_password = encrypt_api_key(bind_password)
    ldap.base_dn = base_dn.strip()
    ldap.user_filter = user_filter.strip() or '(objectClass=person)'
    ldap.email_attribute = email_attribute.strip() or 'mail'
    ldap.display_name_attribute = display_name_attribute.strip() or 'displayName'
    ldap.username_attribute = username_attribute.strip() or 'uid'
    ldap.member_of_attribute = member_of_attribute.strip() or 'memberOf'
    ldap.admin_group_dns = admin_group_dns.strip()
    ldap.security_engineer_group_dns = security_engineer_group_dns.strip()
    ldap.operator_group_dns = operator_group_dns.strip()
    ldap.analyst_group_dns = analyst_group_dns.strip()
    ldap.viewer_group_dns = viewer_group_dns.strip()
    ldap.default_role = default_role
    ldap.auto_disable_missing = auto_disable_missing == 'true'
    ldap.enabled = enabled == 'true'
    current_user = getattr(request.state, 'current_user', None)
    audit(
        db,
        action='ldap_config_update',
        resource_type='ldap_config',
        actor_user_id=UUID(current_user['id']) if current_user else None,
        resource_id='ldap_config',
        metadata={'provider_type': provider_type, 'host': ldap.host, 'enabled': ldap.enabled},
    )
    db.commit()
    return RedirectResponse(url='/management/directory?msg=Directory+sync+settings+saved&type=completed', status_code=303)


@router.post('/management/directory/test')
def directory_test(request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_users')
    ldap = db.get(LdapConfig, 1)
    if not ldap:
        return RedirectResponse(url='/management/directory?msg=Directory+sync+is+not+configured&type=failed', status_code=303)
    try:
        entries = fetch_ldap_entries(ldap)
        diagnostics = build_ldap_diagnostics(entries, config_data_from_model(ldap))
        sample = diagnostics.get('sample_user')
        sample_text = f"; sample {sample['email']} -> {sample['role']}" if sample else "; no usable sample user"
        message = (
            f"Bind/search succeeded; matched {diagnostics['matched_users']} users; "
            f"missing email {diagnostics['missing_email']}; missing groups {diagnostics['missing_groups']}"
            f"{sample_text}"
        )
        msg_type = 'completed'
    except LdapSyncError as exc:
        message = str(exc)
        msg_type = 'failed'
    import urllib.parse
    return RedirectResponse(url=f'/management/directory?msg={urllib.parse.quote(message)}&type={msg_type}', status_code=303)


@router.post('/management/directory/preview')
def directory_preview(request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_users')
    ldap = db.get(LdapConfig, 1)
    if not ldap:
        return RedirectResponse(url='/management/directory?msg=Directory+sync+is+not+configured&type=failed', status_code=303)
    try:
        entries = fetch_ldap_entries(ldap)
        preview = preview_ldap_sync(db, entries, config_data_from_model(ldap))
        db.rollback()
    except LdapSyncError as exc:
        import urllib.parse
        return RedirectResponse(url=f'/management/directory?msg={urllib.parse.quote(str(exc))}&type=failed', status_code=303)
    flash_message = (
        f"Preview: {preview['summary'].created} create, {preview['summary'].updated} update, "
        f"{preview['summary'].disabled} disable, {preview['summary'].conflicts} conflicts"
    )
    return _render(request, 'management_directory.html', {
        'active': 'management',
        'ldap': ldap,
        'flash_message': flash_message,
        'flash_type': 'completed',
        'provider_types': [
            {'value': key, 'label': preset['label']}
            for key, preset in LDAP_PROVIDER_PRESETS.items()
        ],
        'provider_presets': LDAP_PROVIDER_PRESETS,
        'roles': ASSIGNABLE_ROLES,
        'preview': preview,
        'diagnostics': None,
    })


@router.post('/management/directory/sync')
def directory_sync(request: Request, db: Session = Depends(get_db), dry_run: str = Form('')):
    _require(request, 'manage_users')
    ldap = db.get(LdapConfig, 1)
    if not ldap:
        return RedirectResponse(url='/management/directory?msg=Directory+sync+is+not+configured&type=failed', status_code=303)
    current_user = getattr(request.state, 'current_user', None)
    try:
        if dry_run == 'true':
            entries = fetch_ldap_entries(ldap)
            preview = preview_ldap_sync(db, entries, config_data_from_model(ldap))
            summary = preview['summary']
            db.rollback()
            prefix = 'Dry run'
        else:
            summary = run_ldap_sync(db, ldap, actor_user_id=UUID(current_user['id']) if current_user else None, dry_run=False)
            audit(
                db,
                action='ldap_sync_run',
                resource_type='ldap_config',
                actor_user_id=UUID(current_user['id']) if current_user else None,
                resource_id='ldap_config',
                metadata=summary.__dict__,
            )
            db.commit()
            prefix = 'LDAP sync'
        message = f'{prefix}: {summary.created} created, {summary.updated} updated, {summary.disabled} disabled, {summary.conflicts} conflicts, {summary.skipped} skipped'
        msg_type = 'completed'
    except LdapSyncError as exc:
        db.rollback()
        message = str(exc)
        msg_type = 'failed'
    import urllib.parse
    return RedirectResponse(url=f'/management/directory?msg={urllib.parse.quote(message)}&type={msg_type}', status_code=303)


# -- Email Notification Configuration ------------------------------------

@router.get('/management/notifications', response_class=HTMLResponse)
def notifications_config_page(request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_users')
    smtp = db.get(SmtpConfig, 1)
    preferences = db.query(NotificationPreference).order_by(NotificationPreference.event_type).all()
    existing = {p.event_type for p in preferences}
    for event_type in ALL_EVENT_TYPES:
        if event_type not in existing:
            db.add(NotificationPreference(event_type=event_type, enabled=False, recipient_emails=[]))
    if len(existing) < len(ALL_EVENT_TYPES):
        db.commit()
        preferences = db.query(NotificationPreference).order_by(NotificationPreference.event_type).all()
    order = {event_type: index for index, event_type in enumerate(ALL_EVENT_TYPES)}
    preferences = sorted(preferences, key=lambda pref: order.get(pref.event_type, 99))
    flash_message = request.query_params.get('msg')
    flash_type = request.query_params.get('type', 'info')
    return _render(request, 'management_notifications.html', {
        'active': 'management',
        'smtp': smtp,
        'preferences': preferences,
        'event_labels': EVENT_LABELS,
        'flash_message': flash_message,
        'flash_type': flash_type,
    })


@router.post('/management/notifications/smtp')
def notifications_smtp_save(
    request: Request,
    db: Session = Depends(get_db),
    host: str = Form(...),
    port: int = Form(587),
    username: str = Form(''),
    password: str = Form(''),
    use_tls: str = Form(''),
    from_address: str = Form(...),
    from_name: str = Form('Moonwing'),
    enabled: str = Form(''),
):
    _require(request, 'manage_users')
    smtp = db.get(SmtpConfig, 1)
    if smtp:
        smtp.host = host
        smtp.port = port
        smtp.username = username or None
        if password:
            smtp.encrypted_password = encrypt_api_key(password)
        smtp.use_tls = use_tls == 'true'
        smtp.from_address = from_address
        smtp.from_name = from_name
        smtp.enabled = enabled == 'true'
    else:
        smtp = SmtpConfig(
            id=1,
            host=host,
            port=port,
            username=username or None,
            encrypted_password=encrypt_api_key(password) if password else None,
            use_tls=use_tls == 'true',
            from_address=from_address,
            from_name=from_name,
            enabled=enabled == 'true',
        )
        db.add(smtp)
    current_user = getattr(request.state, 'current_user', None)
    audit(
        db,
        action='smtp_config_update',
        resource_type='smtp',
        actor_user_id=UUID(current_user['id']) if current_user else None,
        resource_id='smtp_config',
        metadata={'host': host, 'port': port, 'enabled': enabled == 'true'},
    )
    db.commit()
    return RedirectResponse(url='/management/notifications?msg=SMTP+settings+saved&type=completed', status_code=303)


@router.post('/management/notifications/smtp/test')
def notifications_smtp_test(
    request: Request,
    db: Session = Depends(get_db),
    test_recipient: str = Form(...),
):
    _require(request, 'manage_users')
    from moonwing.services.notifications import send_test_email
    ok, message = send_test_email(db, test_recipient)
    msg_type = 'completed' if ok else 'failed'
    import urllib.parse
    return RedirectResponse(url=f'/management/notifications?msg={urllib.parse.quote(message)}&type={msg_type}', status_code=303)


@router.post('/management/notifications/preferences')
async def notifications_preferences_save(request: Request, db: Session = Depends(get_db)):
    _require(request, 'manage_users')
    body = await request.form()
    for event_type in ALL_EVENT_TYPES:
        pref = db.query(NotificationPreference).filter(NotificationPreference.event_type == event_type).first()
        if not pref:
            pref = NotificationPreference(event_type=event_type)
            db.add(pref)
        pref.enabled = body.get(f'enabled_{event_type}') == 'true'
        raw_recipients = body.get(f'recipients_{event_type}', '')
        pref.recipient_emails = [email.strip() for email in raw_recipients.split(',') if email.strip()]
    current_user = getattr(request.state, 'current_user', None)
    audit(
        db,
        action='notification_prefs_update',
        resource_type='notification_preferences',
        actor_user_id=UUID(current_user['id']) if current_user else None,
        resource_id='all',
    )
    db.commit()
    return RedirectResponse(url='/management/notifications?msg=Notification+preferences+saved&type=completed', status_code=303)


@router.get('/settings', response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    _require(request, 'view')
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
    users = [
        {
            'id': str(u.id),
            'email': u.email,
            'display_name': u.display_name,
            'created_at': u.created_at.isoformat() if u.created_at else None,
        }
        for u in db.query(User).order_by(User.created_at.desc()).all()
    ]
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
    return _render(request, 'settings.html', {
        'active': 'settings',
        'targets': targets,
        'credentials': credentials,
        'users': users,
        'profiles': profiles,
    })


# ── State Machine ──────────────────────────────────────────────────────

@router.get('/state-machine', response_class=HTMLResponse)
def state_machine_page(request: Request):
    _require(request, 'view')
    return _render(request, 'state_machine.html', {'active': 'state-machine',
        'state_transitions': _STATE_TRANSITIONS,
    })
