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
from moonwing.services.web_terminal_status import terminal_supported


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


def test_terminal_rest_status_forbidden_viewer(upd_client):
    app, ids = upd_client
    client = _session_client(app, ids['viewer'], role='viewer')
    res = client.get('/api/system/terminal/status')
    assert res.status_code == 403


def test_terminal_rest_status_ok_admin(upd_client):
    app, ids = upd_client
    client = _session_client(app, ids['admin'])
    res = client.get('/api/system/terminal/status')
    assert res.status_code == 200
    payload = res.json()
    assert 'supported' in payload
    assert 'enabled_effective' in payload
    assert 'ready' in payload
    assert 'blockers' in payload
    assert isinstance(payload['blockers'], list)


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


def test_host_terminal_page_forbidden_viewer(upd_client):
    app, ids = upd_client
    client = _session_client(app, ids['viewer'], role='viewer')
    res = client.get('/system/host-terminal')
    assert res.status_code == 403


def test_host_terminal_page_ok_admin(upd_client):
    app, ids = upd_client
    client = _session_client(app, ids['admin'])
    res = client.get('/system/host-terminal')
    assert res.status_code == 200
    assert b'Host terminal' in res.content or b'host terminal' in res.content.lower()
    assert b'Local AI CLI detection' in res.content
    assert b'In-browser shell' in res.content
    if terminal_supported():
        assert b'Enable live shell' in res.content


@pytest.mark.skipif(not terminal_supported(), reason='Web shell DB toggle is only exposed when PTY is supported')
def test_host_terminal_web_shell_enable_disable(upd_client):
    from moonwing.api import deps as deps_module
    from moonwing.db.models import SystemSetting
    from moonwing.services.system_setting import KEY_WEB_TERMINAL_ENABLED

    app, ids = upd_client
    client = _session_client(app, ids['admin'])

    res = client.post('/system/host-terminal/web-shell', data={'action': 'enable'}, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers.get('location', '').endswith('/system/host-terminal?shell=enabled')

    db = deps_module._get_session_factory()()
    try:
        row = db.get(SystemSetting, KEY_WEB_TERMINAL_ENABLED)
        assert row is not None
        assert row.value == 'true'
    finally:
        db.close()

    res2 = client.get('/system/host-terminal?shell=enabled')
    assert res2.status_code == 200
    assert b'Live shell enabled' in res2.content
    assert b'Disable live shell' in res2.content

    res3 = client.post('/system/host-terminal/web-shell', data={'action': 'disable'}, follow_redirects=False)
    assert res3.status_code == 303

    db = deps_module._get_session_factory()()
    try:
        row = db.get(SystemSetting, KEY_WEB_TERMINAL_ENABLED)
        assert row is not None
        assert row.value == 'false'
    finally:
        db.close()

    res4 = client.post('/system/host-terminal/web-shell', data={'action': 'env_default'}, follow_redirects=False)
    assert res4.status_code == 303

    db = deps_module._get_session_factory()()
    try:
        assert db.get(SystemSetting, KEY_WEB_TERMINAL_ENABLED) is None
    finally:
        db.close()
