"""FastAPI dependency injection — DB session and settings."""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy.orm import Session

from moonwing.config import Settings
from moonwing.db.session import build_session_factory


@lru_cache(maxsize=1)
def _get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def _get_session_factory():
    return build_session_factory(_get_settings().database_url)


def get_db() -> Generator[Session, None, None]:
    session = _get_session_factory()()
    try:
        yield session
    finally:
        session.close()
