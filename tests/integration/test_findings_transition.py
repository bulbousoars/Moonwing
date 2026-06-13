from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from moonwing.core.evidence import EvidenceLevel
from moonwing.db.base import Base
from moonwing.db.models import Credential, Finding, Run, RuntimeProfileRecord, Target, User


@pytest.fixture()
def _api_db(monkeypatch):
    monkeypatch.setenv("MOONWING_SESSION_SECRET", "test-session-secret")

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    from moonwing.api import deps as deps_module
    from moonwing.api import main as main_module
    from moonwing.api.deps import get_db
    from moonwing.api.main import app

    # Other test modules cache get_settings() with a different env; bust both
    # caches so MOONWING_SESSION_SECRET is re-read for this test run.
    deps_module._get_settings.cache_clear()
    deps_module._get_session_factory.cache_clear()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(deps_module, "_get_session_factory", lambda: SessionLocal)
    monkeypatch.setattr(main_module, "_get_session_factory", lambda: SessionLocal)

    session = SessionLocal()
    admin = User(
        id=uuid4(),
        email="admin@moonwing.dev",
        display_name="Admin",
        role="admin",
        status="active",
    )
    viewer = User(
        id=uuid4(),
        email="viewer@moonwing.dev",
        display_name="Viewer",
        role="viewer",
        status="active",
    )
    cred = Credential(id=uuid4(), scope="user", provider="openai", display_name="k", secret_ref="vault://k")
    profile = RuntimeProfileRecord(id=uuid4(), name="p", allow_exploits=False, settings={})
    target = Target(id=uuid4(), target_type="network_host", display_name="t", source_metadata={"address": "1.2.3.4"})
    run = Run(
        id=uuid4(),
        job_family="network_scan",
        target_id=target.id,
        runtime_profile_id=profile.id,
        credential_id=cred.id,
        user_id=admin.id,
        status="queued",
        provider="openai",
        model="gpt-5.2",
        execution_snapshot={},
    )
    finding = Finding(
        id=uuid4(),
        run_id=run.id,
        title="Open Redis on 1.2.3.4:6379",
        severity="high",
        status="open",
        evidence_refs=[],
        details={},
        evidence_level=EvidenceLevel.SUSPICION.value,
        evidence_history=[],
    )
    session.add_all([admin, viewer, cred, profile, target, run, finding])
    session.commit()
    ids = {
        "admin_id": str(admin.id),
        "viewer_id": str(viewer.id),
        "finding_id": str(finding.id),
    }
    session.close()

    yield ids

    app.dependency_overrides.clear()


def _client_with_session(user_id: str, role: str = "admin"):
    from fastapi.testclient import TestClient
    from moonwing.api.main import app
    from moonwing.services.auth import create_session_token

    token = create_session_token(user_id, role, secret="test-session-secret")
    client = TestClient(app)
    client.cookies.set("moonwing_session", token)
    return client


def test_transition_succeeds_for_admin(_api_db):
    client = _client_with_session(_api_db["admin_id"], role="admin")
    resp = client.post(
        f"/api/findings/{_api_db['finding_id']}/transition",
        json={"new_level": "static_corroborated", "reason": "semgrep agrees"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["evidence_level"] == "static_corroborated"
    assert len(body["evidence_history"]) == 1
    assert body["last_transition_by"] == _api_db["admin_id"]


def test_transition_forbidden_for_viewer(_api_db):
    client = _client_with_session(_api_db["viewer_id"], role="viewer")
    resp = client.post(
        f"/api/findings/{_api_db['finding_id']}/transition",
        json={"new_level": "static_corroborated"},
    )
    # Middleware-level permission check returns 403 before the handler runs.
    assert resp.status_code == 403


def test_transition_rejects_invalid_move(_api_db):
    client = _client_with_session(_api_db["admin_id"], role="admin")
    # Move forward, then try to walk backwards
    client.post(
        f"/api/findings/{_api_db['finding_id']}/transition",
        json={"new_level": "reproduced"},
    )
    resp = client.post(
        f"/api/findings/{_api_db['finding_id']}/transition",
        json={"new_level": "suspicion"},
    )
    assert resp.status_code == 409


def test_transition_404_for_missing_finding(_api_db):
    client = _client_with_session(_api_db["admin_id"], role="admin")
    resp = client.post(
        f"/api/findings/{uuid4()}/transition",
        json={"new_level": "static_corroborated"},
    )
    assert resp.status_code == 404


def test_get_finding_includes_new_fields(_api_db):
    client = _client_with_session(_api_db["admin_id"], role="admin")
    resp = client.get(f"/api/findings/{_api_db['finding_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidence_level"] == "suspicion"
    assert body["evidence_history"] == []
    assert body["last_transition_at"] is None
