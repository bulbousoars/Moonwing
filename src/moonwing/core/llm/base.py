"""Core types for the LLM adapter layer.

Kept provider-neutral. Anything tied to a specific vendor's wire format
lives in its adapter module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(RuntimeError):
    """Raised by adapters on transport/auth/parse failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider: str | None = None,
        raw: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider
        self.raw = raw


@dataclass
class LLMUsage:
    """Best-effort token accounting, normalized across providers."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMToolCall:
    """One tool-call request emitted by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Final result of a ``chat_with_tools`` call.

    ``text`` is the assistant's textual content (may be empty when the
    model emitted only tool calls). ``tool_calls`` is the list of tools
    the model wants run before continuing.
    """

    text: str
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    usage: LLMUsage | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class LLMAdapter(Protocol):
    """Provider-side interface used by the ReAct loop and one-shot calls.

    ``messages`` follows the de-facto chat schema:
      ``[{"role": "system"|"user"|"assistant"|"tool", "content": str, ...}]``

    ``tools`` (optional) is a list of ``ToolSpec.to_openai_schema()``-shaped
    dicts. Adapters convert to provider-native shape internally.

    ``model``, ``temperature``, ``max_tokens`` are passed through.
    ``timeout`` is in seconds.
    """

    provider: str

    def chat_with_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str,
        api_key: str,
        temperature: float = 0.1,
        max_tokens: int = 8192,
        timeout: int = 300,
        response_format: str | None = None,
    ) -> LLMResponse:
        ...

    def list_models(self, *, api_key: str, timeout: int = 30) -> list[str]:
        """Return the catalog of model ids the provider exposes for this key.

        Used by the doctor and the Launch Scan UI to verify a key works
        and to populate the model picker dynamically.
        """
        ...
