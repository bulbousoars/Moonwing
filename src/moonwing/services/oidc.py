from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from moonwing.db.models import User


def oidc_enabled(settings) -> bool:
    required = [
        settings.oidc_authorization_endpoint,
        settings.oidc_token_endpoint,
        settings.oidc_userinfo_endpoint,
        settings.oidc_client_id,
        settings.oidc_client_secret,
        settings.oidc_redirect_uri,
    ]
    return all(str(value or "").strip() for value in required)


def build_authorization_url(settings, *, state: str, nonce: str) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": settings.oidc_scope,
        "state": state,
        "nonce": nonce,
    }
    return f"{settings.oidc_authorization_endpoint}?{urlencode(params)}"


def create_oidc_state_token(*, next_url: str, nonce: str, secret: str) -> str:
    payload = {"next": _safe_next_url(next_url), "nonce": nonce}
    payload_raw = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _sign(payload_raw, secret)
    return f"{payload_raw}.{signature}"


def parse_oidc_state_token(token: str | None, *, secret: str) -> dict | None:
    if not token or "." not in token:
        return None
    payload_raw, signature = token.split(".", 1)
    if not hmac.compare_digest(_sign(payload_raw, secret), signature):
        return None
    try:
        payload = json.loads(_unb64(payload_raw))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("nonce"):
        return None
    return {"next": _safe_next_url(str(payload.get("next") or "/")), "nonce": str(payload["nonce"])}


def upsert_oidc_user(
    session: Session,
    *,
    issuer: str,
    subject: str,
    email: str,
    display_name: str,
    default_role: str,
) -> User:
    clean_issuer = issuer.strip()
    clean_subject = subject.strip()
    clean_email = email.strip().lower()
    clean_name = display_name.strip() or clean_email
    if not clean_issuer:
        raise ValueError("OIDC issuer is required")
    if not clean_subject:
        raise ValueError("OIDC subject is required")
    if not clean_email:
        raise ValueError("OIDC email is required")

    external_id = _external_id(clean_issuer, clean_subject)
    if hasattr(User, "oidc_issuer") and hasattr(User, "oidc_subject"):
        user = (
            session.query(User)
            .filter(User.oidc_issuer == clean_issuer, User.oidc_subject == clean_subject)
            .first()
        )
    elif hasattr(User, "external_id"):
        user = (
            session.query(User)
            .filter(User.external_id == external_id, User.auth_source == "oidc")
            .first()
        )
    else:
        user = None
    if user is None:
        user = session.query(User).filter(User.email == clean_email, User.is_service_account.is_(False)).first()

    if user is None:
        user = User(
            email=clean_email,
            display_name=clean_name,
            password_hash=None,
            role=default_role,
            status="active",
            is_service_account=False,
            is_bootstrap=False,
            must_change_password=False,
        )
        session.add(user)

    user.email = clean_email
    user.display_name = clean_name
    if hasattr(user, "auth_provider"):
        user.auth_provider = "oidc"
    if hasattr(user, "auth_source"):
        user.auth_source = "oidc"
    if hasattr(user, "oidc_issuer"):
        user.oidc_issuer = clean_issuer
    if hasattr(user, "oidc_subject"):
        user.oidc_subject = clean_subject
    if hasattr(user, "external_id"):
        user.external_id = external_id
    user.status = "active"
    user.is_service_account = False
    return user


def extract_userinfo_claims(userinfo: dict, *, fallback_issuer: str = "") -> dict[str, str]:
    subject = str(userinfo.get("sub") or "").strip()
    email = str(userinfo.get("email") or userinfo.get("preferred_username") or "").strip().lower()
    display_name = str(userinfo.get("name") or userinfo.get("preferred_username") or email).strip()
    issuer = str(userinfo.get("iss") or fallback_issuer).strip()
    return {"issuer": issuer, "subject": subject, "email": email, "display_name": display_name}


def validate_oidc_claims(
    userinfo: dict,
    *,
    id_token: str | None,
    expected_issuer: str,
    expected_nonce: str,
) -> dict[str, str]:
    id_claims = _decode_jwt_payload(id_token) if id_token else {}
    if id_claims:
        token_nonce = str(id_claims.get("nonce") or "")
        if token_nonce != expected_nonce:
            raise ValueError("OIDC nonce validation failed")

        token_issuer = str(id_claims.get("iss") or "").strip()
        if expected_issuer and token_issuer and token_issuer != expected_issuer:
            raise ValueError("OIDC issuer validation failed")

        userinfo_subject = str(userinfo.get("sub") or "").strip()
        token_subject = str(id_claims.get("sub") or "").strip()
        if token_subject and userinfo_subject and token_subject != userinfo_subject:
            raise ValueError("OIDC subject validation failed")

    return extract_userinfo_claims(userinfo, fallback_issuer=expected_issuer)


def _safe_next_url(next_url: str) -> str:
    value = (next_url or "/").strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _external_id(issuer: str, subject: str) -> str:
    return f"oidc:{issuer}#{subject}"


def _sign(payload_raw: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), payload_raw.encode(), hashlib.sha256).digest())


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _decode_jwt_payload(token: str | None) -> dict:
    parts = (token or "").split(".")
    if len(parts) < 2:
        raise ValueError("OIDC ID token was malformed")
    try:
        payload = json.loads(_unb64(parts[1]))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("OIDC ID token payload was invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("OIDC ID token payload was invalid")
    return payload
