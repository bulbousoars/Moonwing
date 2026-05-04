"""Symmetric encryption for API keys stored in the database.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the ``cryptography``
library.  The encryption key is read from the ``MOONWING_ENCRYPTION_KEY``
environment variable, which must be a valid 32-byte URL-safe base64
Fernet key.  Generate one with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

ENV_VAR = "MOONWING_ENCRYPTION_KEY"

# Provider → environment variable name passed to Clearwing subprocess
PROVIDER_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "google": "GOOGLE_API_KEY",
    "ollama": "",  # local, no key needed
}


class CryptoError(RuntimeError):
    """Raised when encryption/decryption fails."""


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    key = os.environ.get(ENV_VAR)
    if not key:
        raise CryptoError(
            f"{ENV_VAR} environment variable is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt an API key and return the ciphertext as a UTF-8 string."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt a stored API key ciphertext back to plaintext."""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CryptoError("Failed to decrypt API key — wrong encryption key?") from exc


def mask_api_key(plaintext: str) -> str:
    """Return a masked version of an API key for display, e.g. ``sk-...a1b2``."""
    if len(plaintext) <= 8:
        return "****"
    prefix = plaintext[:3] if plaintext.startswith(("sk-", "sk_")) else ""
    suffix = plaintext[-4:]
    return f"{prefix}...{suffix}" if prefix else f"...{suffix}"


def provider_env_var(provider: str) -> str | None:
    """Return the env var name for a provider, or None if no key is needed."""
    name = PROVIDER_ENV_VARS.get(provider, "")
    return name or None
