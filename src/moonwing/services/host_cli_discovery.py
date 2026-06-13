"""Read-only detection of AI CLIs on the Docker host via bind-mounted bin directories.

Moonwing cannot inspect the host filesystem from inside a container without mounts.
Compose ships optional read-only volumes under ``/host-probe/…``; set
``MOONWING_HOST_CLI_SCAN_DIRS`` to add more paths (e.g. a user's ``~/.local/bin``).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from moonwing.config import Settings
from moonwing.services.runtime_context import running_in_container

# Default mount targets when using docker-compose.yml host-probe volumes.
COMPOSE_DEFAULT_HOST_DIRS: tuple[str, ...] = (
    "/host-probe/usr/local/bin",
    "/host-probe/usr/bin",
    "/host-probe/bin",
)

HOST_AI_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "claude",
        "display_name": "Anthropic Claude",
        "binaries": ("claude",),
    },
    {
        "id": "codex",
        "display_name": "OpenAI Codex",
        "binaries": ("codex",),
    },
    {
        "id": "gemini",
        "display_name": "Google Gemini",
        "binaries": ("gemini",),
    },
    {
        "id": "cursor",
        "display_name": "Cursor",
        "binaries": ("cursor", "cursor-agent"),
    },
    {
        "id": "kimi",
        "display_name": "Kimi (Moonshot)",
        "binaries": ("kimi", "kimi-cli"),
    },
)


def parse_host_cli_scan_dirs(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def resolve_host_cli_scan_dirs(settings: Settings | None = None) -> list[str]:
    """Configured dirs, else compose defaults that exist on disk."""
    settings = settings or Settings()
    configured = parse_host_cli_scan_dirs(settings.host_cli_scan_dirs)
    if configured:
        return configured
    return [d for d in COMPOSE_DEFAULT_HOST_DIRS if os.path.isdir(d)]


def _executable_at(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _find_tool_in_dir(directory: str, binary_names: tuple[str, ...]) -> tuple[str | None, str | None]:
    for name in binary_names:
        candidate = os.path.join(directory, name)
        if _executable_at(candidate):
            return os.path.abspath(candidate), name
    return None, None


def scan_host_ai_tools(settings: Settings | None = None) -> dict[str, Any]:
    """Stat-only scan of mounted host bin directories (no subprocess on host binaries)."""
    settings = settings or Settings()
    configured = parse_host_cli_scan_dirs(settings.host_cli_scan_dirs)
    effective_dirs = resolve_host_cli_scan_dirs(settings)
    tools: list[dict[str, Any]] = []

    for spec in HOST_AI_TOOL_SPECS:
        resolved: str | None = None
        matched: str | None = None
        for directory in effective_dirs:
            found_path, found_name = _find_tool_in_dir(directory, tuple(spec["binaries"]))
            if found_path:
                resolved = found_path
                matched = found_name
                break
        tools.append(
            {
                "id": spec["id"],
                "display_name": spec["display_name"],
                "binaries": list(spec["binaries"]),
                "installed": resolved is not None,
                "resolved_path": resolved,
                "matched_binary": matched,
            }
        )

    installed_count = sum(1 for t in tools if t["installed"])
    return {
        "configured_dirs": configured,
        "effective_dirs": effective_dirs,
        "using_compose_defaults": not configured and bool(effective_dirs),
        "in_container": running_in_container(),
        "tools": tools,
        "installed_count": installed_count,
        "total_tools": len(tools),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "setup_required": not effective_dirs,
    }
