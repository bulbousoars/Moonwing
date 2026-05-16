"""Persisted manager settings (web terminal toggle)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from moonwing.db.base import Base
from moonwing.db.models import SystemSetting
from moonwing.services.system_setting import (
    KEY_WEB_TERMINAL_ENABLED,
    set_web_terminal_enabled,
    web_terminal_db_override_active,
    web_terminal_enabled_effective,
)


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_web_terminal_effective_uses_env_when_no_row():
    db = _session()
    try:
        assert web_terminal_enabled_effective(db, env_default=False) is False
        assert web_terminal_enabled_effective(db, env_default=True) is True
        assert web_terminal_db_override_active(db) is False
    finally:
        db.close()


def test_web_terminal_persist_and_clear_override():
    db = _session()
    try:
        set_web_terminal_enabled(db, True)
        db.commit()
        assert web_terminal_enabled_effective(db, env_default=False) is True
        assert web_terminal_db_override_active(db) is True

        set_web_terminal_enabled(db, False)
        db.commit()
        row = db.get(SystemSetting, KEY_WEB_TERMINAL_ENABLED)
        assert row is not None
        assert row.value == "false"
        assert web_terminal_enabled_effective(db, env_default=True) is False

        set_web_terminal_enabled(db, None)
        db.commit()
        assert db.get(SystemSetting, KEY_WEB_TERMINAL_ENABLED) is None
        assert web_terminal_enabled_effective(db, env_default=False) is False
    finally:
        db.close()
