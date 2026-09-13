"""Engine and session management."""

from __future__ import annotations

import functools
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings


def _prepare_sqlite_path(url: str) -> None:
    """Ensure the directory for a file-backed SQLite database exists."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return
    target = url[len(prefix) :]
    if target and target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)


@functools.lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    url = settings.database_url
    _prepare_sqlite_path(url)

    kwargs: dict[str, object] = {"future": True, "echo": settings.debug}
    if url.startswith("sqlite"):
        # The worker runs in a background thread within the same process.
        kwargs["connect_args"] = {"check_same_thread": False}

    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            # Off by default in SQLite; the job queue and event log depend on it.
            cursor.execute("PRAGMA foreign_keys=ON")
            # Lets the worker read while an API request writes.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


@functools.lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
