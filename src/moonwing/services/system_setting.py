"""Persisted manager settings (DB) that override env defaults where supported."""

from __future__ import annotations

from sqlalchemy.orm import Session

from moonwing.db.models import SystemSetting

KEY_WEB_TERMINAL_ENABLED = "web_terminal_enabled"


def _parse_boolish(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


def web_terminal_enabled_effective(session: Session, *, env_default: bool) -> bool:
    row = session.get(SystemSetting, KEY_WEB_TERMINAL_ENABLED)
    if row is None:
        return env_default
    return _parse_boolish(row.value)


def web_terminal_db_override_active(session: Session) -> bool:
    return session.get(SystemSetting, KEY_WEB_TERMINAL_ENABLED) is not None


def set_web_terminal_enabled(session: Session, value: bool | None) -> None:
    """Persist override. ``None`` removes the row so the process env default applies again."""
    if value is None:
        row = session.get(SystemSetting, KEY_WEB_TERMINAL_ENABLED)
        if row is not None:
            session.delete(row)
        return
    sval = "true" if value else "false"
    row = session.get(SystemSetting, KEY_WEB_TERMINAL_ENABLED)
    if row is None:
        session.add(SystemSetting(key=KEY_WEB_TERMINAL_ENABLED, value=sval))
    else:
        row.value = sval
