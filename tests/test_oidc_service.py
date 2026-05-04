import base64
import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from moonwing.db.base import Base
from moonwing.db.models import User
from moonwing.services.oidc import (
    build_authorization_url,
    create_oidc_state_token,
    parse_oidc_state_token,
    upsert_oidc_user,
    validate_oidc_claims,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _settings():
    return SimpleNamespace(
        oidc_authorization_endpoint="https://auth.example.test/application/o/authorize/",
        oidc_client_id="moonwing",
        oidc_redirect_uri="https://moonwing.example.test/auth/oidc/callback",
        oidc_scope="openid email profile",
        session_secret="state-secret",
    )


def test_oidc_state_token_is_signed_and_detects_tampering():
    token = create_oidc_state_token(next_url="/runs", nonce="nonce-123", secret="state-secret")

    assert parse_oidc_state_token(token, secret="state-secret") == {
        "next": "/runs",
        "nonce": "nonce-123",
    }
    assert parse_oidc_state_token(token + "x", secret="state-secret") is None


def test_authorization_url_contains_standard_oidc_parameters():
    url = build_authorization_url(_settings(), state="signed-state", nonce="nonce-123")

    assert url.startswith("https://auth.example.test/application/o/authorize/?")
    assert "response_type=code" in url
    assert "client_id=moonwing" in url
    assert "redirect_uri=https%3A%2F%2Fmoonwing.example.test%2Fauth%2Foidc%2Fcallback" in url
    assert "scope=openid+email+profile" in url
    assert "state=signed-state" in url
    assert "nonce=nonce-123" in url


def test_upsert_oidc_user_creates_and_reuses_subject_link():
    session = _session()

    user = upsert_oidc_user(
        session,
        issuer="https://auth.example.test/application/o/moonwing/",
        subject="user-123",
        email="owner@example.test",
        display_name="Owner",
        default_role="viewer",
    )
    session.commit()

    same_user = upsert_oidc_user(
        session,
        issuer="https://auth.example.test/application/o/moonwing/",
        subject="user-123",
        email="owner-renamed@example.test",
        display_name="Owner Renamed",
        default_role="admin",
    )

    assert same_user.id == user.id
    assert same_user.email == "owner-renamed@example.test"
    assert same_user.display_name == "Owner Renamed"
    assert same_user.role == "viewer"
    assert session.query(User).count() == 1


def test_upsert_oidc_user_links_existing_local_user_by_email():
    session = _session()
    session.add(User(email="analyst@example.test", display_name="Analyst", role="analyst", status="active"))
    session.commit()

    user = upsert_oidc_user(
        session,
        issuer="https://auth.example.test/application/o/moonwing/",
        subject="subject-456",
        email="analyst@example.test",
        display_name="Analyst From OIDC",
        default_role="viewer",
    )

    assert user.role == "analyst"
    assert user.oidc_issuer == "https://auth.example.test/application/o/moonwing/"
    assert user.oidc_subject == "subject-456"
    assert user.auth_provider == "oidc"


def test_validate_oidc_claims_rejects_nonce_mismatch_from_id_token():
    id_token = _unsigned_jwt({
        "iss": "https://auth.example.test/application/o/moonwing/",
        "sub": "subject-789",
        "nonce": "wrong-nonce",
    })

    try:
        validate_oidc_claims(
            {"sub": "subject-789", "email": "owner@example.test", "name": "Owner"},
            id_token=id_token,
            expected_issuer="https://auth.example.test/application/o/moonwing/",
            expected_nonce="expected-nonce",
        )
    except ValueError as exc:
        assert "nonce" in str(exc).lower()
    else:
        raise AssertionError("expected nonce mismatch to be rejected")


def _unsigned_jwt(payload: dict) -> str:
    header = {"alg": "none", "typ": "JWT"}
    return ".".join([
        _b64(header),
        _b64(payload),
        "",
    ])


def _b64(value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
