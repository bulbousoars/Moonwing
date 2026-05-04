from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets


PASSWORD_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        _b64(salt),
        _b64(digest),
    )


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    try:
        scheme, iterations_raw, salt_raw, digest_raw = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = _unb64(salt_raw)
        expected = _unb64(digest_raw)
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(actual, expected)


def create_session_token(user_id: str, role: str, *, secret: str) -> str:
    payload = {"sub": user_id, "role": role}
    payload_raw = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _sign(payload_raw, secret)
    return f"{payload_raw}.{signature}"


def parse_session_token(token: str | None, *, secret: str) -> dict | None:
    if not token or "." not in token:
        return None
    payload_raw, signature = token.split(".", 1)
    if not hmac.compare_digest(_sign(payload_raw, secret), signature):
        return None
    try:
        payload = json.loads(_unb64(payload_raw))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("sub") or not payload.get("role"):
        return None
    return {"sub": str(payload["sub"]), "role": str(payload["role"])}


def create_service_token() -> str:
    return "mwsvc_" + secrets.token_urlsafe(32)


def hash_service_token(token: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", token.encode(), salt, PASSWORD_ITERATIONS)
    return "service_pbkdf2_sha256${}${}${}".format(PASSWORD_ITERATIONS, _b64(salt), _b64(digest))


def verify_service_token(token: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    try:
        scheme, iterations_raw, salt_raw, digest_raw = stored_hash.split("$", 3)
        if scheme != "service_pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = _unb64(salt_raw)
        expected = _unb64(digest_raw)
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac("sha256", token.encode(), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _sign(payload_raw: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), payload_raw.encode(), hashlib.sha256).digest())


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
