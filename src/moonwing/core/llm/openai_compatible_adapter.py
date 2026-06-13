"""OpenAI-compatible chat completions adapter.

Covers vanilla OpenAI, OpenRouter, and local Ollama instances. The wire
format is identical; per-provider differences are limited to base URL,
auth header, and a couple of bespoke ``HTTP-Referer`` / ``X-Title``
headers required by OpenRouter.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .base import LLMAdapter, LLMError, LLMResponse, LLMToolCall, LLMUsage


_PROVIDER_URLS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "ollama": "http://localhost:11434/v1/chat/completions",
}

_PROVIDER_MODELS_URLS = {
    "openai": "https://api.openai.com/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
    "ollama": "http://localhost:11434/v1/models",
}


def _openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI accepts our generic shape directly; this function exists so
    we have one place to add normalization later (e.g. content blocks)."""
    return [dict(m) for m in messages]


class OpenAICompatibleAdapter:
    """One adapter parameterized by provider name."""

    def __init__(self, provider: str, *, client: httpx.Client | None = None) -> None:
        if provider not in _PROVIDER_URLS:
            raise ValueError(f"unsupported openai-compatible provider: {provider!r}")
        self.provider = provider
        self._client = client

    def _extra_headers(self) -> dict[str, str]:
        if self.provider == "openrouter":
            return {
                "HTTP-Referer": os.environ.get(
                    "MOONWING_OPENROUTER_HTTP_REFERER", "https://moonwing.example.org"
                ),
                "X-Title": "Moonwing Security Scanner",
            }
        return {}

    def _auth_header(self, api_key: str) -> dict[str, str]:
        # Ollama doesn't enforce auth locally but accepts the header without complaint.
        return {"Authorization": f"Bearer {api_key or 'unused'}"}

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
        headers = {"Content-Type": "application/json"}
        headers.update(self._auth_header(api_key))
        headers.update(self._extra_headers())

        body: dict[str, Any] = {
            "model": model,
            "messages": _openai_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
        if response_format == "json_object":
            body["response_format"] = {"type": "json_object"}

        url = _PROVIDER_URLS[self.provider]
        client = self._client or httpx.Client(timeout=timeout)
        owns = self._client is None
        try:
            resp = client.post(url, json=body, headers=headers)
        finally:
            if owns:
                client.close()

        if resp.status_code != 200:
            raise LLMError(
                f"{self.provider} API returned {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                provider=self.provider,
                raw=resp.text,
            )

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {}) or {}
        text = message.get("content") or ""

        tool_calls: list[LLMToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {}) or {}
            raw_args = fn.get("arguments")
            args: dict[str, Any]
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}
            tool_calls.append(
                LLMToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args)
            )

        usage_raw = data.get("usage") or {}
        usage = LLMUsage(
            input_tokens=usage_raw.get("prompt_tokens"),
            output_tokens=usage_raw.get("completion_tokens"),
            total_tokens=usage_raw.get("total_tokens"),
            raw=usage_raw,
        )

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=choice.get("finish_reason"),
            usage=usage,
            raw=data,
        )

    def list_models(self, *, api_key: str, timeout: int = 30) -> list[str]:
        headers = {"Content-Type": "application/json"}
        headers.update(self._auth_header(api_key))
        headers.update(self._extra_headers())

        url = _PROVIDER_MODELS_URLS[self.provider]
        client = self._client or httpx.Client(timeout=timeout)
        owns = self._client is None
        try:
            resp = client.get(url, headers=headers)
        finally:
            if owns:
                client.close()
        if resp.status_code != 200:
            raise LLMError(
                f"{self.provider} models endpoint returned {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
                provider=self.provider,
            )
        data = resp.json()
        models = data.get("data") or []
        return [m["id"] for m in models if isinstance(m, dict) and "id" in m]
