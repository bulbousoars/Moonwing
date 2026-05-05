from __future__ import annotations

import httpx


# Which local CLI binary handles each provider in CLI execution mode.
# Authoritative source of truth used by both the worker (clearwing_runner.py)
# and the Launch Scan form for the binary-required hint.
PROVIDER_CLI_BINARIES: dict[str, str] = {
    "anthropic": "claude",
    "openai": "codex",
    "openrouter": "codex",
    "google": "gemini",
    "ollama": "codex",  # codex with --local-provider ollama
}


PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-5.5",
    "anthropic": "claude-sonnet-4-6",
    "google": "gemini-2.5-pro",
    "openrouter": "openai/gpt-5.5",
    "ollama": "llama3.1",
}

# Selectable models per provider for the Launch Scan form.
# These are starter lists — verify against each provider's official docs and
# adjust as model lineups change. The first entry is treated as the suggested
# default when a provider is selected.
PROVIDER_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-5",
        "claude-opus-4-1",
        "claude-3-7-sonnet-latest",
        "claude-3-5-haiku-latest",
    ],
    "openai": [
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-4.1",
        "gpt-4o",
        "o3",
        "o1",
    ],
    "google": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
    ],
    "openrouter": [
        "openai/gpt-5",
        "anthropic/claude-opus-4-7",
        "anthropic/claude-sonnet-4-6",
        "google/gemini-2.5-pro",
        "meta-llama/llama-3.3-70b-instruct",
    ],
    "ollama": [
        "llama3.3",
        "llama3.1",
        "qwen2.5",
        "mistral",
        "codellama",
    ],
}

OPENAI_COMPATIBLE_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "ollama": "http://host.docker.internal:11434/v1/chat/completions",
}


class ProviderProbeError(ValueError):
    pass


def require_api_key(provider: str) -> bool:
    return provider in {"openai", "anthropic", "google", "openrouter"}


def build_probe_request(*, provider: str, api_key: str, model: str | None = None) -> dict:
    provider = provider.strip().lower()
    model = (model or PROVIDER_DEFAULT_MODELS.get(provider) or "").strip()

    if provider not in PROVIDER_DEFAULT_MODELS:
        raise ProviderProbeError(f"Unsupported provider: {provider}")
    if require_api_key(provider) and not api_key.strip():
        raise ProviderProbeError("API key is required for this provider")
    if not model:
        raise ProviderProbeError("Model is required")

    if provider in OPENAI_COMPATIBLE_ENDPOINTS:
        headers = {
            "Authorization": f"Bearer {api_key or 'ollama'}",
            "Content-Type": "application/json",
        }
        if provider == "openrouter":
            headers.update({
                "HTTP-Referer": "https://github.com/moonwing-security/moonwing",
                "X-Title": "Moonwing Security Scanner",
            })
        return {
            "url": OPENAI_COMPATIBLE_ENDPOINTS[provider],
            "headers": headers,
            "json": {
                "model": model,
                "messages": [
                    {"role": "system", "content": "Respond only with valid JSON."},
                    {"role": "user", "content": 'Return exactly {"ok": true}.'},
                ],
                "temperature": 0,
                "max_tokens": 32,
                "response_format": {"type": "json_object"},
            },
        }

    if provider == "anthropic":
        return {
            "url": "https://api.anthropic.com/v1/messages",
            "headers": {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            "json": {
                "model": model,
                "max_tokens": 32,
                "system": "Respond only with valid JSON.",
                "messages": [{"role": "user", "content": 'Return exactly {"ok": true}.'}],
            },
        }

    return {
        "url": f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "headers": {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        "json": {
            "contents": [{"parts": [{"text": 'Return exactly {"ok": true}.'}]}],
            "systemInstruction": {"parts": [{"text": "Respond only with valid JSON."}]},
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 32,
                "responseMimeType": "application/json",
            },
        },
    }


def probe_provider(*, provider: str, api_key: str = "", model: str | None = None, timeout: int = 20) -> dict:
    request = build_probe_request(provider=provider, api_key=api_key, model=model)

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(request["url"], headers=request["headers"], json=request["json"])
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "provider": provider,
            "model": model or PROVIDER_DEFAULT_MODELS.get(provider, ""),
            "message": f"Connection failed: {exc}",
        }

    selected_model = model or PROVIDER_DEFAULT_MODELS.get(provider, "")
    if 200 <= response.status_code < 300:
        return {
            "ok": True,
            "provider": provider,
            "model": selected_model,
            "message": f"{provider} accepted the test request.",
        }

    return {
        "ok": False,
        "provider": provider,
        "model": selected_model,
        "message": f"{provider} returned HTTP {response.status_code}: {response.text[:240]}",
    }
