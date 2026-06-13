from __future__ import annotations

import shutil
import subprocess
import json

import httpx


PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-5.5",
    "anthropic": "claude-sonnet-4-6",
    "google": "gemini-2.5-pro",
    "openrouter": "openai/gpt-5.5",
    "ollama": "llama3.1",
}

OPENAI_COMPATIBLE_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "ollama": "http://host.docker.internal:11434/v1/chat/completions",
}

MODEL_LIST_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "google": "https://generativelanguage.googleapis.com/v1beta/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
    "ollama": "http://host.docker.internal:11434/v1/models",
}

CLI_MODEL_CANDIDATES = {
    "anthropic": ["sonnet", "opus", "haiku"],
    "openai": [
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-5.1-codex",
        "gpt-5.1-codex-max",
        "gpt-5.1-codex-mini",
        "gpt-5-codex",
        "codex-mini-latest",
    ],
    "google": [
        "gemini-3.1-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ],
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


def build_model_list_request(*, provider: str, api_key: str = "") -> dict:
    provider = provider.strip().lower()
    if provider not in MODEL_LIST_ENDPOINTS:
        raise ProviderProbeError(f"Unsupported provider: {provider}")
    if require_api_key(provider) and not api_key.strip():
        raise ProviderProbeError("API key is required for this provider")

    headers = {"Content-Type": "application/json"}
    params: dict[str, str] = {}
    if provider in {"openai", "openrouter", "ollama"}:
        headers["Authorization"] = f"Bearer {api_key or 'ollama'}"
        if provider == "openrouter":
            headers.update({
                "HTTP-Referer": "https://github.com/moonwing-security/moonwing",
                "X-Title": "Moonwing Security Scanner",
            })
    elif provider == "anthropic":
        headers.update({
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        })
    elif provider == "google":
        headers["x-goog-api-key"] = api_key

    return {"url": MODEL_LIST_ENDPOINTS[provider], "headers": headers, "params": params}


def _looks_like_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    blocked = (
        "embedding",
        "moderation",
        "transcribe",
        "tts",
        "whisper",
        "dall-e",
        "image",
        "audio",
        "realtime",
        "sora",
    )
    return bool(model_id) and not any(token in lowered for token in blocked)


def parse_model_list_response(provider: str, payload: dict) -> list[str]:
    provider = provider.strip().lower()
    model_ids: list[str] = []

    if provider in {"openai", "openrouter", "anthropic", "ollama"}:
        for item in payload.get("data", []):
            model_id = str(item.get("id") or "").strip()
            if _looks_like_chat_model(model_id):
                model_ids.append(model_id)
    elif provider == "google":
        for item in payload.get("models", []):
            methods = item.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            name = str(item.get("name") or "").strip()
            model_id = name.removeprefix("models/")
            if _looks_like_chat_model(model_id):
                model_ids.append(model_id)
    else:
        raise ProviderProbeError(f"Unsupported provider: {provider}")

    return sorted(set(model_ids))


def parse_ollama_cli_list(output: str) -> list[str]:
    models: list[str] = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if parts:
            models.append(parts[0])
    return sorted(set(models))


def parse_codex_cli_model_catalog(output: str) -> list[str]:
    try:
        payload = json.loads(output)
    except ValueError:
        return []
    models = []
    for item in payload.get("models", []):
        if not isinstance(item, dict):
            continue
        if item.get("visibility") not in (None, "list"):
            continue
        model_id = str(item.get("slug") or "").strip()
        if model_id and _looks_like_chat_model(model_id):
            models.append(model_id)
    return sorted(set(models))


def _build_cli_probe_command(provider: str, binary: str, model: str) -> list[str]:
    prompt = "Return exactly OK."
    if provider == "anthropic":
        return [
            binary,
            "--print",
            "--output-format",
            "json",
            "--model",
            model,
            "--max-turns",
            "1",
            "--disable-slash-commands",
            "-p",
            prompt,
        ]
    if provider == "openai":
        return [
            binary,
            "exec",
            "--json",
            "-m",
            model,
            "--full-auto",
            "--skip-git-repo-check",
            "--ephemeral",
            prompt,
        ]
    if provider == "google":
        return [binary, "-p", prompt, "-o", "json", "-m", model, "--yolo", "--skip-trust"]
    raise ProviderProbeError(f"Unsupported CLI provider: {provider}")


def _cli_probe_succeeded(provider: str, proc: subprocess.CompletedProcess) -> bool:
    if proc.returncode == 0:
        return True
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return False
    if provider == "anthropic":
        return bool(payload.get("modelUsage"))
    return False


def _resolved_cli_model_ids(provider: str, candidate: str, proc: subprocess.CompletedProcess) -> list[str]:
    if not _cli_probe_succeeded(provider, proc):
        return []
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return [candidate]
    if provider == "anthropic":
        model_usage = payload.get("modelUsage")
        if isinstance(model_usage, dict) and model_usage:
            model_ids = sorted(str(model_id) for model_id in model_usage.keys() if str(model_id).strip())
            matching_ids = [model_id for model_id in model_ids if candidate.lower() in model_id.lower()]
            return matching_ids or model_ids[:1]
    return [candidate]


def discover_cli_models(*, provider: str, timeout: int = 8, use_sudo: bool = False) -> dict:
    provider = provider.strip().lower()
    binaries = {
        "anthropic": "claude",
        "openai": "codex",
        "google": "gemini",
        "ollama": "ollama",
    }
    binary = binaries.get(provider)
    if not binary:
        return {"ok": False, "provider": provider, "models": [], "message": "Unsupported CLI provider."}
    if not shutil.which(binary):
        return {"ok": False, "provider": provider, "models": [], "message": f"{binary} CLI is not installed."}
    if provider == "ollama":
        try:
            proc = subprocess.run([binary, "list"], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "provider": provider, "models": [], "message": f"CLI discovery failed: {exc}"}
        if proc.returncode != 0:
            return {"ok": False, "provider": provider, "models": [], "message": proc.stderr.strip() or "CLI discovery failed."}
        models = parse_ollama_cli_list(proc.stdout)
        return {
            "ok": bool(models),
            "provider": provider,
            "models": models,
            "message": "Discovered available CLI models." if models else "No CLI models discovered.",
        }

    if provider == "openai":
        try:
            command = [binary, "debug", "models"]
            if use_sudo:
                command = ["sudo", "-n", *command]
            proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, input="", cwd="/tmp")
        except (OSError, subprocess.SubprocessError):
            proc = None
        if proc is not None and proc.returncode == 0:
            models = parse_codex_cli_model_catalog(proc.stdout)
            if models:
                return {
                    "ok": True,
                    "provider": provider,
                    "models": models,
                    "message": "Discovered available CLI models.",
                }

    models = []
    for model in CLI_MODEL_CANDIDATES.get(provider, []):
        try:
            command = _build_cli_probe_command(provider, binary, model)
            if use_sudo:
                command = ["sudo", "-n", *command]
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                input="",
                cwd="/tmp",
            )
        except (OSError, subprocess.SubprocessError):
            continue
        models.extend(_resolved_cli_model_ids(provider, model, proc))
    return {
        "ok": bool(models),
        "provider": provider,
        "models": sorted(set(models)),
        "message": "Discovered available CLI models." if models else "No candidate CLI models validated.",
    }


def discover_api_models(*, provider: str, api_key: str = "", timeout: int = 8) -> dict:
    try:
        request = build_model_list_request(provider=provider, api_key=api_key)
    except ProviderProbeError as exc:
        return {"ok": False, "provider": provider, "models": [], "message": str(exc)}

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(request["url"], headers=request["headers"], params=request["params"])
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "provider": provider,
            "models": [],
            "message": f"Model discovery failed: {exc}",
        }

    if not 200 <= response.status_code < 300:
        return {
            "ok": False,
            "provider": provider,
            "models": [],
            "message": f"Model discovery returned HTTP {response.status_code}: {response.text[:240]}",
        }

    try:
        models = parse_model_list_response(provider, response.json())
    except (ValueError, ProviderProbeError) as exc:
        return {"ok": False, "provider": provider, "models": [], "message": str(exc)}

    return {
        "ok": bool(models),
        "provider": provider,
        "models": models,
        "message": "Discovered available models." if models else "No chat-capable models discovered.",
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
