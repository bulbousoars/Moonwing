"""Google Gemini ``generateContent`` adapter."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .base import LLMError, LLMResponse, LLMToolCall, LLMUsage


_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _to_gemini_contents(messages: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Split system instruction out and convert chat → Gemini ``contents``."""
    system_text: list[str] = []
    contents: list[dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            content = m.get("content", "")
            if isinstance(content, str) and content:
                system_text.append(content)
            continue
        gemini_role = "model" if role == "assistant" else "user"
        # Gemini doesn't have a "tool" role; pack tool results as user text.
        if role == "tool":
            text = f"<tool_result id={m.get('tool_call_id')}>{m.get('content', '')}</tool_result>"
            contents.append({"role": "user", "parts": [{"text": text}]})
            continue
        text = m.get("content", "")
        if isinstance(text, str):
            contents.append({"role": gemini_role, "parts": [{"text": text}]})
    system = {"parts": [{"text": "\n\n".join(system_text)}]} if system_text else None
    return system, contents


def _to_gemini_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    fns: list[dict[str, Any]] = []
    for t in tools:
        if "function" in t and "type" in t:
            fn = t["function"]
            fns.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object"}),
                }
            )
        elif "input_schema" in t:
            fns.append(
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t["input_schema"],
                }
            )
    return [{"function_declarations": fns}] if fns else None


class GoogleAdapter:
    provider = "google"

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client

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
        system, contents = _to_gemini_contents(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system is not None:
            body["systemInstruction"] = system
        gtools = _to_gemini_tools(tools)
        if gtools is not None:
            body["tools"] = gtools
        if response_format == "json_object":
            body["generationConfig"]["responseMimeType"] = "application/json"

        url = _GENERATE_URL.format(model=model)
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}

        client = self._client or httpx.Client(timeout=timeout)
        owns = self._client is None
        try:
            resp = client.post(url, json=body, headers=headers)
        finally:
            if owns:
                client.close()

        if resp.status_code != 200:
            raise LLMError(
                f"Google API returned {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                provider=self.provider,
                raw=resp.text,
            )

        data = resp.json()
        candidate = (data.get("candidates") or [{}])[0]
        parts = ((candidate.get("content") or {}).get("parts") or [])

        text_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        for p in parts:
            if "text" in p:
                text_parts.append(p["text"])
            elif "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append(
                    LLMToolCall(
                        id=fc.get("name", ""),  # Gemini reuses name as id
                        name=fc.get("name", ""),
                        arguments=fc.get("args") or {},
                    )
                )

        usage_raw = data.get("usageMetadata") or {}
        usage = LLMUsage(
            input_tokens=usage_raw.get("promptTokenCount"),
            output_tokens=usage_raw.get("candidatesTokenCount"),
            total_tokens=usage_raw.get("totalTokenCount"),
            raw=usage_raw,
        )

        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=candidate.get("finishReason"),
            usage=usage,
            raw=data,
        )

    def list_models(self, *, api_key: str, timeout: int = 30) -> list[str]:
        headers = {"x-goog-api-key": api_key}
        client = self._client or httpx.Client(timeout=timeout)
        owns = self._client is None
        try:
            resp = client.get(_MODELS_URL, headers=headers)
        finally:
            if owns:
                client.close()
        if resp.status_code != 200:
            raise LLMError(
                f"Google models endpoint returned {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
                provider=self.provider,
            )
        data = resp.json()
        out: list[str] = []
        for m in data.get("models") or []:
            name = m.get("name") or ""
            # API returns "models/gemini-2.5-pro" — strip the prefix.
            out.append(name.split("/", 1)[-1] if "/" in name else name)
        return out
