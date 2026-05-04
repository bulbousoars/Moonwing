from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from moonwing.db.base import Base
from moonwing.db.models import Credential, RuntimeProfileRecord, Target, User


@pytest.fixture()
def _api_db(monkeypatch):
    """Wire the API's DB dependency to an in-memory SQLite database."""
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
    app.dependency_overrides[get_db] = override_get_db
    # Auth middleware calls _get_session_factory() directly (not through
    # FastAPI dependency injection). main.py imported it by name, so we
    # have to patch it on both modules.
    monkeypatch.setattr(deps_module, "_get_session_factory", lambda: SessionLocal)
    monkeypatch.setattr(main_module, "_get_session_factory", lambda: SessionLocal)

    # Seed required entities (admin role + active so auth + permissions pass)
    session = SessionLocal()
    user = User(
        id=uuid4(),
        email="test@moonwing.dev",
        display_name="Test User",
        role="admin",
        status="active",
    )
    cred = Credential(id=uuid4(), scope="user", provider="openai", display_name="Test Key", secret_ref="vault://test")
    profile = RuntimeProfileRecord(id=uuid4(), name="default", allow_exploits=False, settings={})
    target = Target(id=uuid4(), target_type="network_host", display_name="192.168.1.100", source_metadata={"address": "192.168.1.100"})
    session.add_all([user, cred, profile, target])
    session.commit()
    ids = {
        "user_id": str(user.id),
        "credential_id": str(cred.id),
        "profile_id": str(profile.id),
        "target_id": str(target.id),
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


def test_post_run_creates_queued_run(_api_db):
    client = _client_with_session(_api_db['user_id'])
    payload = {
        'job_family': 'network_scan',
        'target_id': _api_db['target_id'],
        'runtime_profile_id': _api_db['profile_id'],
        'credential_id': _api_db['credential_id'],
        'user_id': _api_db['user_id'],
        'provider': 'openai',
        'model': 'gpt-5.2',
    }

    response = client.post('/api/runs', json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body['status'] == 'queued'
    assert body['job_family'] == 'network_scan'


def test_post_run_rejects_missing_user(_api_db):
    client = _client_with_session(_api_db['user_id'])
    payload = {
        'job_family': 'network_scan',
        'runtime_profile_id': _api_db['profile_id'],
        'credential_id': _api_db['credential_id'],
        'user_id': str(uuid4()),  # non-existent
        'provider': 'openai',
        'model': 'gpt-5.2',
    }

    response = client.post('/api/runs', json=payload)

    assert response.status_code == 422
