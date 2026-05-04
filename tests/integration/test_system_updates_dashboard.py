"""Dashboard + REST surface for Moonwing manager self-updates."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from moonwing.db.base import Base
from moonwing.db.models import User


@pytest.fixture(name='upd_client')
def _upd_client(monkeypatch):
    monkeypatch.setenv('MOONWING_SESSION_SECRET', 'upd-secret')
    monkeypatch.setenv('MOONWING_UPDATE_REPO_PATH', '')

    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    from moonwing.api import deps as deps_module
    from moonwing.api import main as main_module
    from moonwing.api.deps import get_db
    from moonwing.api.main import app

    deps_module._get_settings.cache_clear()  # type: ignore[attr-defined]
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(deps_module, '_get_session_factory', lambda: SessionLocal)
    monkeypatch.setattr(main_module, '_get_session_factory', lambda: SessionLocal)

    sess = SessionLocal()
    admin = User(
        id=uuid4(),
        email='a@moonwing.dev',
        display_name='Admin',
        role='admin',
        status='active',
    )
    viewer = User(
        id=uuid4(),
        email='v@moonwing.dev',
        display_name='Viewer',
        role='viewer',
        status='active',
    )
    sess.add_all([admin, viewer])
    sess.commit()

    ids = {'admin': str(admin.id), 'viewer': str(viewer.id)}
    sess.close()

    yield app, ids

    app.dependency_overrides.clear()
    deps_module._get_settings.cache_clear()  # type: ignore[attr-defined]


def _session_client(app, user_id: str, *, role: str = 'admin') -> TestClient:
    from moonwing.services.auth import create_session_token

    token = create_session_token(user_id, role, secret='upd-secret')
    client = TestClient(app)
    client.cookies.set('moonwing_session', token)
    return client


def test_system_updates_rest_status_forbidden_viewer(upd_client):
    app, ids = upd_client
    client = _session_client(app, ids['viewer'], role='viewer')
    res = client.get('/api/system/updates/status')
    assert res.status_code == 403


def test_system_updates_rest_status_ok_admin(upd_client):
    app, ids = upd_client
    client = _session_client(app, ids['admin'])
    res = client.get('/api/system/updates/status')
    assert res.status_code == 200
    payload = res.json()
    assert 'package_version' in payload
    assert payload['commits_behind'] is None


def test_system_updates_page_forbidden_viewer(upd_client):
    app, ids = upd_client
    client = _session_client(app, ids['viewer'], role='viewer')
    res = client.get('/system/updates')
    assert res.status_code == 403


def test_system_updates_page_ok_admin(upd_client):
    app, ids = upd_client
    client = _session_client(app, ids['admin'])
    res = client.get('/system/updates')
    assert res.status_code == 200
    assert b'Moonwing updates' in res.content


def test_system_updates_apply_requires_confirm(monkeypatch, tmp_path, upd_client):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('MOONWING_UPDATE_REPO_PATH', '')
    deps = __import__('moonwing.api.deps', fromlist=['_get_settings'])
    deps._get_settings.cache_clear()  # type: ignore[attr-defined]

    app, ids = upd_client
    client = _session_client(app, ids['admin'])
    res = client.post('/system/updates/apply', data={'confirm': 'NOPE'})
    assert res.status_code == 200
    assert b'Type APPLY' in res.content


def test_system_updates_apply_success_redirect(monkeypatch, upd_client):
    import moonwing.api.routes.dashboard as dashboard_mod
    from moonwing.services.system_updates import ApplyOutcome

    monkeypatch.setattr(
        dashboard_mod,
        'apply_system_update',
        lambda _settings: ApplyOutcome(success=True, message='would run', steps=[]),
    )

    app, ids = upd_client
    client = _session_client(app, ids['admin'])
    res = client.post('/system/updates/apply', data={'confirm': 'APPLY'}, follow_redirects=False)
    assert res.status_code == 303
    assert 'applied=1' in res.headers.get('location', '')
