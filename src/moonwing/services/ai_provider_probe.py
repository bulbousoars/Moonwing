from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any

import httpx

from moonwing.config import Settings


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

CLI_PROBE_CONTAINER_NOTE = (
    "This probe runs inside the Moonwing API container/process. In Docker Compose it is not your "
    "Docker host — it is the image filesystem and PATH inside that container."
)

CLI_PROBE_WORKER_NOTE = (
    "CLI-mode scans execute inside moonwing-worker. Compare API vs worker tables when they run in "
    "separate containers; only the worker snapshot predicts scan success."
)

# Vendor install paths change — keep URLs current; commands are typical npm/brew flows.
CLI_INSTALL_META: dict[str, dict[str, Any]] = {
    "claude": {
        "docs_url": "https://docs.anthropic.com/en/docs/claude-code/setup",
        "commands": [
            ("npm (global)", "npm install -g @anthropic-ai/claude-code"),
            ("npx (no global install)", "npx @anthropic-ai/claude-code --version"),
        ],
    },
    "codex": {
        "docs_url": "https://developers.openai.com/codex/quickstart",
        "commands": [
            ("npm (global)", "npm install -g @openai/codex"),
            ("Homebrew (macOS)", "brew install --cask codex"),
        ],
    },
    "gemini": {
        "docs_url": "https://google-gemini.github.io/gemini-cli/docs/get-started/",
        "commands": [
            ("npm (global)", "npm install -g @google/gemini-cli"),
            ("Homebrew", "brew install gemini-cli"),
        ],
    },
}


def cli_binary_executable(path: str) -> bool:
    """True if ``path`` is an executable we can invoke (PATH lookup or absolute path)."""
    if os.sep in path or (os.altsep and os.altsep in path):
        return bool(os.path.isfile(path) and os.access(path, os.X_OK))
    return shutil.which(path) is not None


def resolve_cli_binary(configured: str) -> str | None:
    """Return an absolute path when the CLI is available, else None."""
    if os.sep in configured or (os.altsep and os.altsep in configured):
        if os.path.isfile(configured) and os.access(configured, os.X_OK):
            return os.path.abspath(configured)
        return None
    found = shutil.which(configured)
    return os.path.abspath(found) if found else None


def parse_host_cli_probe_paths(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def probe_host_mount_cli_paths(paths: list[str]) -> list[dict[str, Any]]:
    """Read-only check of bind-mounted paths (typically host CLIs mounted into a container)."""
    rows: list[dict[str, Any]] = []
    for path in paths:
        exists = os.path.isfile(path)
        executable = exists and os.access(path, os.X_OK)
        rows.append(
            {
                "path": path,
                "basename": os.path.basename(path) or path,
                "available": executable,
                "resolved_path": os.path.abspath(path) if executable else None,
            }
        )
    return rows


def discover_ai_cli_tools(
    settings: Settings | None = None,
    *,
    process_label: str = "moonwing",
) -> dict[str, Any]:
    """Probe configured AI CLIs the same way staging checks before a CLI-mode run."""
    settings = settings or Settings()
    slots: list[dict[str, Any]] = [
        {
            "id": "claude",
            "display_name": "Anthropic Claude CLI",
            "env_var": "MOONWING_CLAUDE_CLI_BINARY",
            "configured_value": settings.claude_cli_binary,
            "providers": ["anthropic"],
        },
        {
            "id": "codex",
            "display_name": "OpenAI Codex CLI (also OpenRouter + Ollama routing)",
            "env_var": "MOONWING_CODEX_CLI_BINARY",
            "configured_value": settings.codex_cli_binary,
            "providers": ["openai", "openrouter", "ollama"],
        },
        {
            "id": "gemini",
            "display_name": "Google Gemini CLI",
            "env_var": "MOONWING_GEMINI_CLI_BINARY",
            "configured_value": settings.gemini_cli_binary,
            "providers": ["google"],
        },
    ]
    tools: list[dict[str, Any]] = []
    for slot in slots:
        cfg = str(slot["configured_value"] or "").strip() or slot["id"]
        tid = str(slot["id"])
        meta = CLI_INSTALL_META.get(tid, {})
        docs = str(meta.get("docs_url") or "").strip()
        cmds_raw = meta.get("commands") or []
        install_commands: list[dict[str, str]] = []
        for row in cmds_raw:
            if isinstance(row, (list, tuple)) and len(row) == 2:
                label, command = str(row[0]), str(row[1])
                if label and command:
                    install_commands.append({"label": label, "command": command})
        tools.append(
            {
                **slot,
                "configured_value": cfg,
                "resolved_path": resolve_cli_binary(cfg),
                "available": cli_binary_executable(cfg),
                "install_docs_url": docs,
                "install_commands": install_commands,
            }
        )
    return {
        "process_label": process_label,
        "note": CLI_PROBE_CONTAINER_NOTE if process_label == "moonwing-api" else CLI_PROBE_WORKER_NOTE,
        "tools": tools,
    }


def _smoke_cli_once(invoke: str, timeout: float) -> dict[str, Any]:
    """Run a trivial non-interactive argv; stdin closed so shells never wait for TTY."""
    arg_chains = (("--version",), ("-V",), ("--help",))
    last: dict[str, Any] = {}
    for extra in arg_chains:
        try:
            proc = subprocess.run(
                [invoke, *extra],
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return {"smoke_ok": False, "smoke_tried": extra, "smoke_error": "not_found"}
        except subprocess.TimeoutExpired:
            return {"smoke_ok": False, "smoke_tried": extra, "smoke_error": "timeout"}
        except OSError as exc:
            return {"smoke_ok": False, "smoke_tried": extra, "smoke_error": str(exc)[:200]}

        out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        head = out.splitlines()[0][:160] if out else ""
        last = {
            "smoke_ok": proc.returncode == 0,
            "smoke_tried": extra,
            "smoke_returncode": proc.returncode,
            "smoke_line": head,
        }
        if proc.returncode == 0:
            return last
    return last or {"smoke_ok": False, "smoke_tried": None, "smoke_error": "nonzero_exit"}


def enrich_tools_with_boot_smoke(
    tools: list[dict[str, Any]],
    *,
    timeout: float,
) -> None:
    """Mutate tool dicts with ``smoke_*`` keys where a binary exists on disk / PATH."""
    for t in tools:
        if not t.get("available"):
            t["smoke_skipped"] = True
            t["smoke_ok"] = None
            continue
        invoke = t.get("resolved_path") or t.get("configured_value") or t["id"]
        result = _smoke_cli_once(str(invoke), timeout=timeout)
        t["smoke_skipped"] = False
        t["smoke_ok"] = result.get("smoke_ok")
        tried = result.get("smoke_tried")
        t["smoke_tried"] = list(tried) if isinstance(tried, tuple) else tried
        if "smoke_returncode" in result:
            t["smoke_returncode"] = result["smoke_returncode"]
        if result.get("smoke_line"):
            t["smoke_line"] = result["smoke_line"]
        if result.get("smoke_error"):
            t["smoke_error"] = result["smoke_error"]


def _tool_boot_status_token(tool: dict[str, Any]) -> str:
    tid = str(tool.get("id", "?"))
    if not tool.get("available"):
        return f"{tid}:missing"
    if tool.get("smoke_skipped"):
        return f"{tid}:path"
    if tool.get("smoke_ok") is True:
        return f"{tid}:exec_ok"
    err = str(tool.get("smoke_error") or tool.get("smoke_line") or "smoke_failed")[:48]
    return f"{tid}:exec_fail({err})"


def build_cli_agent_snapshot(settings: Settings | None = None, *, process_label: str) -> dict[str, Any]:
    """Full PATH + optional smoke probe for dashboards and DB persistence (JSON-safe)."""
    from datetime import datetime, timezone

    settings = settings or Settings()
    probe = discover_ai_cli_tools(settings, process_label=process_label)
    if settings.cli_boot_smoke:
        enrich_tools_with_boot_smoke(
            probe["tools"],
            timeout=float(settings.cli_boot_smoke_timeout_seconds),
        )
    else:
        for t in probe["tools"]:
            t["smoke_skipped"] = True
            t["smoke_ok"] = None
    for t in probe["tools"]:
        st = t.get("smoke_tried")
        if isinstance(st, tuple):
            t["smoke_tried"] = list(st)
    if settings.cli_boot_smoke:
        ready = sum(1 for t in probe["tools"] if t.get("available") and t.get("smoke_ok") is True)
    else:
        ready = sum(1 for t in probe["tools"] if t.get("available"))
    probe["ready_for_cli_scan_count"] = ready
    probe["path_only_count"] = sum(1 for t in probe["tools"] if t.get("available"))
    probe["total_cli_slots"] = len(probe["tools"])
    probe["captured_at"] = datetime.now(timezone.utc).isoformat()
    return probe


def log_ai_cli_boot_diagnostics(
    logger: logging.Logger,
    settings: Settings,
    *,
    process_label: str,
) -> dict[str, Any]:
    """PATH/executable check plus optional subprocess smoke; one INFO line per boot."""
    snap = build_cli_agent_snapshot(settings, process_label=process_label)
    parts = [_tool_boot_status_token(t) for t in snap["tools"]]
    if settings.cli_boot_smoke:
        logger.info(
            "AI CLI boot diagnostics (%s, smoke=%.1fs): %s",
            process_label,
            float(settings.cli_boot_smoke_timeout_seconds),
            ", ".join(parts),
        )
    else:
        logger.info("AI CLI boot diagnostics (%s, smoke=off): %s", process_label, ", ".join(parts))
    return snap


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
