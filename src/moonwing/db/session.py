from collections.abc import Callable

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


SessionFactory = Callable[[], Session]


def build_session_factory(database_url: str) -> SessionFactory:
    engine = create_engine(database_url, future=True)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
