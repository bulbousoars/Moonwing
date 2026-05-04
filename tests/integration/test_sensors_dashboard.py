from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from moonwing.db.base import Base
from moonwing.db.models import SensorEndpoint, SensorEvent, SensorTask, User


@pytest.fixture()
def _dash(monkeypatch):
    monkeypatch.setenv("MOONWING_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("MOONWING_SENSOR_ENROLLMENT_TOKEN", "enroll-secret-token-12345")

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
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

    # Bust cached settings so the new env vars take effect.
    deps_module._get_settings.cache_clear()  # type: ignore[attr-defined]

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(deps_module, "_get_session_factory", lambda: SessionLocal)
    monkeypatch.setattr(main_module, "_get_session_factory", lambda: SessionLocal)

    session = SessionLocal()
    user = User(
        id=uuid4(),
        email="admin@moonwing.dev",
        display_name="Admin",
        role="admin",
        status="active",
    )
    sensor = SensorEndpoint(
        id=uuid4(),
        hostname="lab-01",
        platform="linux",
        os_name="Ubuntu 24.04",
        sensor_version="0.1.0",
        token_hash="x" * 64,
        labels=["docker", "core"],
        last_seen_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    stale = SensorEndpoint(
        id=uuid4(),
        hostname="lab-02",
        platform="windows",
        os_name="Windows Server 2022",
        sensor_version="0.1.0",
        token_hash="y" * 64,
        labels=[],
        last_seen_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    session.add_all([user, sensor, stale])
    session.flush()
    session.add(SensorEvent(sensor_id=sensor.id, event_type="fim.change", severity="medium", payload={"path": "/etc/hosts"}))
    session.add(SensorTask(sensor_id=sensor.id, task_type="inventory", status="queued", payload={}))
    session.commit()
    ids = {
        "user_id": str(user.id),
        "sensor_id": str(sensor.id),
        "stale_id": str(stale.id),
    }
    session.close()

    yield ids, app

    app.dependency_overrides.clear()
    deps_module._get_settings.cache_clear()  # type: ignore[attr-defined]


def _client(app, user_id: str, role: str = "admin") -> TestClient:
    from moonwing.services.auth import create_session_token
    token = create_session_token(user_id, role, secret="test-session-secret")
    client = TestClient(app)
    client.cookies.set("moonwing_session", token)
    return client


def test_sensors_index_lists_endpoints_and_shows_install_button(_dash):
    ids, app = _dash
    client = _client(app, ids["user_id"])
    resp = client.get("/sensors")
    assert resp.status_code == 200
    body = resp.text
    assert "lab-01" in body
    assert "lab-02" in body
    assert "Download sensor installer" in body
    assert "/sensors/install" in body
    # Stale sensor should be flagged
    assert "stale" in body.lower()


def test_sensor_install_page_renders_for_admin(_dash):
    ids, app = _dash
    client = _client(app, ids["user_id"])
    resp = client.get("/sensors/install")
    assert resp.status_code == 200
    body = resp.text
    assert "Install a sensor" in body
    assert "Linux endpoint" in body
    assert "Windows endpoint" in body
    assert "/sensors/install/linux.sh" in body
    assert "/sensors/install/windows.ps1" in body
    # Token is masked by default but present in data attribute
    assert 'data-token="enroll-secret-token-12345"' in body


def test_sensor_install_page_blocked_for_viewer(_dash):
    ids, app = _dash
    # Seed a viewer user too
    from moonwing.db.models import User as UserModel
    from moonwing.api import deps as deps_module
    sf = deps_module._get_session_factory()
    s = sf()
    viewer = UserModel(
        id=uuid4(),
        email="viewer@moonwing.dev",
        display_name="Viewer",
        role="viewer",
        status="active",
    )
    s.add(viewer)
    s.commit()
    viewer_id = str(viewer.id)
    s.close()

    client = _client(app, viewer_id, role="viewer")
    resp = client.get("/sensors/install")
    assert resp.status_code == 403


def test_linux_installer_download(_dash):
    ids, app = _dash
    client = _client(app, ids["user_id"])
    resp = client.get("/sensors/install/linux.sh")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/x-shellscript")
    assert "attachment" in resp.headers["content-disposition"]
    body = resp.text
    assert body.startswith("#!/usr/bin/env bash")
    assert "ENROLLMENT_TOKEN='enroll-secret-token-12345'" in body
    assert "/api/sensors/enroll" in body


def test_windows_installer_download(_dash):
    ids, app = _dash
    client = _client(app, ids["user_id"])
    resp = client.get("/sensors/install/windows.ps1")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/x-powershell")
    body = resp.text
    assert "#requires -RunAsAdministrator" in body
    assert "$EnrollmentToken  = 'enroll-secret-token-12345'" in body


def test_sensor_detail_page_shows_inventory_events_and_tasks(_dash):
    ids, app = _dash
    client = _client(app, ids["user_id"])
    resp = client.get(f"/sensors/{ids['sensor_id']}")
    assert resp.status_code == 200
    body = resp.text
    assert "lab-01" in body
    assert "Effective policy" in body
    assert "Recent events" in body
    assert "fim.change" in body
    assert "Tasks (" in body
    assert "inventory" in body  # task_type cell


def test_sensor_detail_404_for_unknown(_dash):
    ids, app = _dash
    client = _client(app, ids["user_id"])
    bogus = str(uuid4())
    resp = client.get(f"/sensors/{bogus}")
    assert resp.status_code == 404


def test_sensor_delete_redirects_to_index(_dash):
    ids, app = _dash
    client = _client(app, ids["user_id"])
    resp = client.post(f"/sensors/{ids['stale_id']}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/sensors"
    # Subsequent fetch should 404
    follow = client.get(f"/sensors/{ids['stale_id']}")
    assert follow.status_code == 404
