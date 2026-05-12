"""Direct API executor — calls provider APIs via httpx instead of CLI tools.

Supports OpenAI, Anthropic, Google Gemini, and OpenRouter APIs.  The
executor sends a structured security-scanning prompt and parses the
JSON response.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import httpx

from moonwing.worker.clearwing_runner import _get_prompt, append_operator_ai_instruction

logger = logging.getLogger("moonwing.worker.api_executor")

DEFAULT_TIMEOUT_SECONDS = 300  # 5 minutes for API calls

# Provider → (base_url, model_header_key)
_PROVIDER_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}


class APIExecutionError(RuntimeError):
    """Raised when an API call fails."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class APIExecutionResult:
    """Result of a direct API execution."""

    raw_payload: dict
    provider: str
    model: str
    usage: dict | None = None


def _call_openai_compatible(
    *,
    url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: int,
    extra_headers: dict | None = None,
) -> dict:
    """Call an OpenAI-compatible chat completions endpoint."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a security scanner. Respond only with valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body, headers=headers)

    if resp.status_code != 200:
        raise APIExecutionError(
            f"API returned {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
        )

    data = resp.json()
    content = data["choices"][0]["message"]["content"]

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise APIExecutionError(
            f"API response is not valid JSON: {exc}. First 200 chars: {content[:200]!r}"
        ) from exc

    return {
        "payload": payload,
        "usage": data.get("usage"),
    }


def _call_anthropic(
    *,
    api_key: str,
    model: str,
    prompt: str,
    timeout: int,
) -> dict:
    """Call the Anthropic Messages API."""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    body = {
        "model": model,
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
        "system": "You are a security scanner. Respond only with valid JSON matching the requested schema.",
    }

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers=headers,
        )

    if resp.status_code != 200:
        raise APIExecutionError(
            f"Anthropic API returned {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
        )

    data = resp.json()
    # Extract text from content blocks
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    content = "\n".join(text_blocks)

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise APIExecutionError(
            f"Anthropic response is not valid JSON: {exc}. First 200 chars: {content[:200]!r}"
        ) from exc

    return {
        "payload": payload,
        "usage": data.get("usage"),
    }


def _call_google(
    *,
    api_key: str,
    model: str,
    prompt: str,
    timeout: int,
) -> dict:
    """Call the Google Gemini generateContent API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {
            "parts": [{"text": "You are a security scanner. Respond only with valid JSON."}],
        },
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body, headers=headers)

    if resp.status_code != 200:
        raise APIExecutionError(
            f"Google API returned {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
        )

    data = resp.json()
    # Extract text from candidates[0].content.parts
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    content = "".join(p.get("text", "") for p in parts)

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise APIExecutionError(
            f"Google response is not valid JSON: {exc}. First 200 chars: {content[:200]!r}"
        ) from exc

    usage = data.get("usageMetadata")

    return {
        "payload": payload,
        "usage": usage,
    }


def execute_via_api(
    *,
    provider: str,
    model: str,
    api_key: str,
    job_family: str,
    source_ref: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ai_instruction: str | None = None,
) -> APIExecutionResult:
    """Execute a security scan via direct API call.

    Returns an APIExecutionResult with the parsed findings payload.
    """
    prompt = append_operator_ai_instruction(
        _get_prompt(job_family, source_ref),
        ai_instruction,
    )

    logger.info("API execution: provider=%s model=%s source=%s", provider, model, source_ref)

    if provider == "anthropic":
        result = _call_anthropic(
            api_key=api_key,
            model=model,
            prompt=prompt,
            timeout=timeout,
        )
    elif provider in ("openai", "openrouter"):
        url = _PROVIDER_ENDPOINTS[provider]
        extra_headers = {}
        if provider == "openrouter":
            extra_headers["HTTP-Referer"] = os.environ.get(
                "MOONWING_OPENROUTER_HTTP_REFERER", "https://moonwing.example.org"
            )
            extra_headers["X-Title"] = "Moonwing Security Scanner"
        result = _call_openai_compatible(
            url=url,
            api_key=api_key,
            model=model,
            prompt=prompt,
            timeout=timeout,
            extra_headers=extra_headers or None,
        )
    elif provider == "google":
        result = _call_google(
            api_key=api_key,
            model=model,
            prompt=prompt,
            timeout=timeout,
        )
    elif provider == "ollama":
        # Ollama uses OpenAI-compatible API locally
        result = _call_openai_compatible(
            url="http://localhost:11434/v1/chat/completions",
            api_key="ollama",  # ollama doesn't need a real key
            model=model,
            prompt=prompt,
            timeout=timeout,
        )
    else:
        raise APIExecutionError(f"unsupported provider for API execution: {provider!r}")

    payload = result["payload"]
    if not isinstance(payload, dict):
        raise APIExecutionError(
            f"API response parsed to {type(payload).__name__}, expected dict"
        )

    # Ensure findings key exists
    if "findings" not in payload:
        payload["findings"] = []

    return APIExecutionResult(
        raw_payload=payload,
        provider=provider,
        model=model,
        usage=result.get("usage"),
    )
