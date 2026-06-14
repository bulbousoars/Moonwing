from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moonwing.config import Settings


@dataclass(frozen=True)
class CliToolDefinition:
    name: str
    label: str
    binary: str
    npm_package: str | None = None
    update_kind: str = "unsupported"


CLI_TOOLS: dict[str, CliToolDefinition] = {
    "claude": CliToolDefinition(name="claude", label="Claude Code", binary="claude", update_kind="self"),
    "codex": CliToolDefinition(name="codex", label="OpenAI Codex", binary="codex", npm_package="@openai/codex", update_kind="npm_detected"),
    "gemini": CliToolDefinition(name="gemini", label="Gemini CLI", binary="gemini", npm_package="@google/gemini-cli", update_kind="npm"),
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _status_path(settings: Settings | None = None) -> Path:
    settings = settings or Settings()
    return Path(settings.workspace_dir) / "_cli-tool-status.json"


def _load_state(settings: Settings | None = None) -> dict[str, Any]:
    path = _status_path(settings)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {"tools": {}}


def _save_state(state: dict[str, Any], settings: Settings | None = None) -> None:
    path = _status_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _run_text(command: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, input="")


def _command_output(proc: subprocess.CompletedProcess, *, limit: int = 4000) -> str:
    text = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip())
    return text[-limit:] if len(text) > limit else text


def _version_for(binary: str) -> str:
    try:
        proc = _run_text([binary, "--version"], timeout=8)
    except (OSError, subprocess.SubprocessError):
        return ""
    return _command_output(proc, limit=500).splitlines()[0] if _command_output(proc, limit=500) else ""


def _global_npm_packages() -> set[str]:
    if not shutil.which("npm"):
        return set()
    try:
        proc = _run_text(["npm", "list", "-g", "--depth=0", "--json"], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return set()
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        return set()
    dependencies = payload.get("dependencies") if isinstance(payload, dict) else {}
    if not isinstance(dependencies, dict):
        return set()
    return set(dependencies.keys())


def _update_command(definition: CliToolDefinition, npm_packages: set[str]) -> tuple[list[str], str, str]:
    if definition.update_kind == "self":
        return ["sudo", "-n", definition.binary, "update"], f"{definition.binary} update", "self-updater"
    if definition.update_kind == "npm" and definition.npm_package:
        return ["sudo", "-n", "npm", "install", "-g", f"{definition.npm_package}@latest"], f"npm install -g {definition.npm_package}@latest", "npm"
    if definition.update_kind == "npm_detected" and definition.npm_package in npm_packages:
        return ["sudo", "-n", "npm", "install", "-g", f"{definition.npm_package}@latest"], f"npm install -g {definition.npm_package}@latest", "npm"
    return [], "Update method unknown", "unknown"


def list_cli_tools(*, settings: Settings | None = None) -> list[dict[str, Any]]:
    state = _load_state(settings)
    npm_packages = _global_npm_packages()
    tools_state = state.setdefault("tools", {})
    checked_at = _now()
    rows = []

    for name, definition in CLI_TOOLS.items():
        path = shutil.which(definition.binary) or ""
        command, command_label, install_source = _update_command(definition, npm_packages)
        tool_state = dict(tools_state.get(name) or {})
        tool_state["last_checked_at"] = checked_at
        tools_state[name] = tool_state
        rows.append({
            "name": name,
            "label": definition.label,
            "binary": definition.binary,
            "path": path,
            "installed": bool(path),
            "version": _version_for(definition.binary) if path else "",
            "install_source": install_source,
            "update_supported": bool(path and command),
            "update_command": command_label,
            "last_checked_at": checked_at,
            "last_update": tool_state.get("last_update") or {},
        })

    _save_state(state, settings)
    return rows


def update_cli_tool(tool_name: str, *, settings: Settings | None = None, timeout: int = 300) -> dict[str, Any]:
    if tool_name not in CLI_TOOLS:
        raise ValueError("Unsupported CLI tool.")

    state = _load_state(settings)
    tools_state = state.setdefault("tools", {})
    npm_packages = _global_npm_packages()
    definition = CLI_TOOLS[tool_name]
    command, command_label, install_source = _update_command(definition, npm_packages)
    if not shutil.which(definition.binary):
        result = {"status": "failed", "message": f"{definition.binary} is not installed.", "command": command_label}
    elif not command:
        result = {"status": "unsupported", "message": "Update method unknown.", "command": command_label}
    else:
        started_at = _now()
        tools_state[tool_name] = {
            **dict(tools_state.get(tool_name) or {}),
            "last_update": {
                "status": "running",
                "install_source": install_source,
                "command": command_label,
                "started_at": started_at,
            },
        }
        _save_state(state, settings)
        try:
            proc = _run_text(command, timeout=timeout)
            result = {
                "status": "completed" if proc.returncode == 0 else "failed",
                "install_source": install_source,
                "command": command_label,
                "started_at": started_at,
                "finished_at": _now(),
                "returncode": proc.returncode,
                "output": _command_output(proc),
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "status": "failed",
                "install_source": install_source,
                "command": command_label,
                "started_at": started_at,
                "finished_at": _now(),
                "message": f"Update timed out after {timeout}s.",
                "output": _command_output(exc),
            }
        except OSError as exc:
            result = {
                "status": "failed",
                "install_source": install_source,
                "command": command_label,
                "started_at": started_at,
                "finished_at": _now(),
                "message": str(exc),
            }

    tools_state[tool_name] = {
        **dict(tools_state.get(tool_name) or {}),
        "last_update": result,
    }
    _save_state(state, settings)
    return result
