"""Adapter selection by provider name."""

from __future__ import annotations

from typing import Any

import httpx

from .anthropic_adapter import AnthropicAdapter
from .base import LLMAdapter
from .google_adapter import GoogleAdapter
from .openai_compatible_adapter import OpenAICompatibleAdapter


SUPPORTED_PROVIDERS: tuple[str, ...] = (
    "anthropic",
    "openai",
    "openrouter",
    "ollama",
    "google",
)


def get_adapter(provider: str, *, client: httpx.Client | None = None) -> LLMAdapter:
    """Return a configured adapter for ``provider``.

    Passing ``client`` allows tests to inject a transport (``MockTransport``
    is the idiomatic httpx mock). Production code should leave it ``None``
    so the adapter owns its own short-lived client per call.
    """
    if provider == "anthropic":
        return AnthropicAdapter(client=client)
    if provider in ("openai", "openrouter", "ollama"):
        return OpenAICompatibleAdapter(provider, client=client)
    if provider == "google":
        return GoogleAdapter(client=client)
    raise ValueError(f"unsupported LLM provider: {provider!r}")
