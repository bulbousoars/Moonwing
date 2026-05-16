"""Diagnostics for in-browser host PTY."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from moonwing.config import Settings
from moonwing.db.base import Base
from moonwing.services.system_setting import set_web_terminal_enabled
from moonwing.services.web_terminal_status import (
    build_web_terminal_status,
    resolve_web_terminal_shell,
    terminal_supported,
)


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_resolve_web_terminal_shell_finds_bash_or_sh(monkeypatch):
    settings = Settings(web_terminal_shell="/bin/bash")
    if Path("/bin/bash").is_file():
        assert resolve_web_terminal_shell(settings) == "/bin/bash"
    elif Path("/bin/sh").is_file():
        assert resolve_web_terminal_shell(settings) == "/bin/sh"
    else:
        monkeypatch.setattr(
            "moonwing.services.web_terminal_status.os.path.isfile",
            lambda p: p in ("/bin/bash", "/bin/sh"),
        )
        monkeypatch.setattr(
            "moonwing.services.web_terminal_status.os.access",
            lambda _p, _mode: True,
        )
        assert resolve_web_terminal_shell(settings) == "/bin/bash"


def test_build_web_terminal_status_reports_db_toggle(monkeypatch):
    if not terminal_supported():
        pytest.skip("PTY not supported on this platform")

    db = _session()
    settings = Settings(web_terminal_enabled=False, web_terminal_shell="/bin/bash")
    try:
        status = build_web_terminal_status(db, settings)
        assert status["db_settings_available"] is True
        assert status["env_default"] is False
        assert status["enabled_effective"] is False
        assert "disabled" in " ".join(status["blockers"]).lower()

        set_web_terminal_enabled(db, True)
        db.commit()
        status2 = build_web_terminal_status(db, settings)
        assert status2["enabled_effective"] is True
        assert status2["db_override"] is True
    finally:
        db.close()


def test_build_web_terminal_status_unsupported_platform(monkeypatch):
    monkeypatch.setattr(
        "moonwing.services.web_terminal_status.terminal_supported",
        lambda: False,
    )
    db = _session()
    settings = Settings(web_terminal_enabled=True)
    try:
        status = build_web_terminal_status(db, settings)
        assert status["supported"] is False
        assert status["ready"] is False
        assert any("forkpty" in b.lower() for b in status["blockers"])
    finally:
        db.close()
