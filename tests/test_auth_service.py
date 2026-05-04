from moonwing.services.auth import (
    create_session_token,
    create_service_token,
    hash_password,
    hash_service_token,
    parse_session_token,
    verify_password,
    verify_service_token,
)


def test_password_hash_round_trips_without_storing_plaintext():
    hashed = hash_password("correct horse battery staple")

    assert "correct horse" not in hashed
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_session_token_is_signed_and_detects_tampering():
    token = create_session_token("user-123", "admin", secret="test-secret")

    assert parse_session_token(token, secret="test-secret") == {
        "sub": "user-123",
        "role": "admin",
    }
    assert parse_session_token(token + "x", secret="test-secret") is None


def test_service_tokens_are_only_verifiable_from_hash():
    token = create_service_token()
    hashed = hash_service_token(token)

    assert token not in hashed
    assert verify_service_token(token, hashed)
    assert not verify_service_token("mwsvc_wrong", hashed)
