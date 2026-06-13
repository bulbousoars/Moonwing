"""Anthropic Messages API adapter."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .base import LLMAdapter, LLMError, LLMResponse, LLMToolCall, LLMUsage

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
_ANTHROPIC_VERSION = "2023-06-01"


def _split_system(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Anthropic puts the system prompt in a top-level ``system`` field."""
    system_text: list[str] = []
    conversation: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            if isinstance(content, str) and content:
                system_text.append(content)
            continue
        conversation.append(m)
    system = "\n\n".join(system_text) if system_text else None
    return system, conversation


def _to_anthropic_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Convert OpenAI-shaped tool list to Anthropic shape if needed."""
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for t in tools:
        if "function" in t and "type" in t:
            fn = t["function"]
            converted.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        else:
            converted.append(t)
    return converted


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert generic chat messages to Anthropic content-block format."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m["tool_call_id"],
                            "content": _stringify(m.get("content", "")),
                        }
                    ],
                }
            )
        elif role == "assistant" and m.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if isinstance(m.get("content"), str) and m["content"]:
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc.get("arguments") or {},
                    }
                )
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": role, "content": m.get("content", "")})
    return out


def _stringify(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content)
    except (TypeError, ValueError):
        return str(content)


class AnthropicAdapter:
    provider = "anthropic"

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client  # tests inject a mocked client

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
        system, conv = _split_system(messages)
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": _to_anthropic_messages(conv),
        }
        if system is not None:
            body["system"] = system
        if tools:
            body["tools"] = _to_anthropic_tools(tools)

        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

        client = self._client or httpx.Client(timeout=timeout)
        owns = self._client is None
        try:
            resp = client.post(_ANTHROPIC_URL, json=body, headers=headers)
        finally:
            if owns:
                client.close()

        if resp.status_code != 200:
            raise LLMError(
                f"Anthropic API returned {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                provider=self.provider,
                raw=resp.text,
            )

        data = resp.json()
        text_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        for block in data.get("content", []):
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    LLMToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=block.get("input") or {},
                    )
                )

        usage_raw = data.get("usage", {}) or {}
        usage = LLMUsage(
            input_tokens=usage_raw.get("input_tokens"),
            output_tokens=usage_raw.get("output_tokens"),
            total_tokens=(usage_raw.get("input_tokens") or 0) + (usage_raw.get("output_tokens") or 0)
            if usage_raw.get("input_tokens") is not None and usage_raw.get("output_tokens") is not None
            else None,
            raw=usage_raw,
        )

        return LLMResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=data.get("stop_reason"),
            usage=usage,
            raw=data,
        )

    def list_models(self, *, api_key: str, timeout: int = 30) -> list[str]:
        headers = {"x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION}
        client = self._client or httpx.Client(timeout=timeout)
        owns = self._client is None
        try:
            resp = client.get(_ANTHROPIC_MODELS_URL, headers=headers)
        finally:
            if owns:
                client.close()
        if resp.status_code != 200:
            raise LLMError(
                f"Anthropic models endpoint returned {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
                provider=self.provider,
            )
        data = resp.json()
        return [m["id"] for m in data.get("data", []) if "id" in m]
