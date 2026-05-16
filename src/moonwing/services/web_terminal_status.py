"""Diagnostics for the in-browser host PTY (WebSocket + forkpty)."""

from __future__ import annotations

import logging
import os

try:
    import fcntl  # noqa: F401
    import termios  # noqa: F401
except ImportError:
    fcntl = None  # type: ignore[assignment, misc]
    termios = None  # type: ignore[assignment, misc]

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from moonwing.config import Settings
from moonwing.services.system_setting import (
    web_terminal_db_override_active,
    web_terminal_enabled_effective,
)

logger = logging.getLogger(__name__)


def terminal_supported() -> bool:
    """PTY shell bridge is POSIX-only (typical Linux manager host)."""
    if os.name != "posix" or not hasattr(os, "forkpty"):
        return False
    if fcntl is None or termios is None:
        return False
    return True


def system_setting_table_available(session: Session) -> bool:
    try:
        session.execute(text("SELECT 1 FROM system_setting LIMIT 1"))
        return True
    except SQLAlchemyError as exc:
        logger.warning("system_setting table unavailable: %s", exc)
        session.rollback()
        return False


def resolve_web_terminal_shell(settings: Settings) -> str | None:
    """First executable shell among configured path, bash, then sh."""
    configured = (settings.web_terminal_shell or "").strip()
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    for fallback in ("/bin/bash", "/bin/sh"):
        if fallback not in candidates:
            candidates.append(fallback)
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def build_web_terminal_status(session: Session, settings: Settings) -> dict[str, object]:
    table_ok = system_setting_table_available(session)
    env_default = settings.web_terminal_enabled
    if table_ok:
        enabled_effective = web_terminal_enabled_effective(session, env_default=env_default)
        db_override = web_terminal_db_override_active(session)
    else:
        enabled_effective = env_default
        db_override = False

    shell_configured = (settings.web_terminal_shell or "").strip() or "/bin/bash"
    shell_resolved = resolve_web_terminal_shell(settings)
    supported = terminal_supported()

    blockers: list[str] = []
    if not supported:
        blockers.append("Host OS does not support forkpty (need Linux/POSIX).")
    if not table_ok:
        blockers.append(
            "Database migration missing: run `alembic upgrade head` (needs system_setting table)."
        )
    if supported and not enabled_effective:
        blockers.append("Live shell is disabled — click Enable live shell or set MOONWING_WEB_TERMINAL_ENABLED=true.")
    if supported and enabled_effective and shell_resolved is None:
        blockers.append(
            f"No executable shell found (tried {shell_configured!r}, /bin/bash, /bin/sh)."
        )

    return {
        "supported": supported,
        "enabled_effective": enabled_effective,
        "env_default": env_default,
        "db_override": db_override,
        "db_settings_available": table_ok,
        "shell_configured": shell_configured,
        "shell_resolved": shell_resolved,
        "ready": supported and enabled_effective and shell_resolved is not None and table_ok,
        "blockers": blockers,
    }
