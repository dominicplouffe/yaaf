"""Engine and session factory.

Sync SQLAlchemy 2.0 on psycopg3. The API runs sync endpoints in FastAPI's
threadpool; the dispatcher, janitor and runner are plain loops. One fewer
concurrency model to get wrong.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from yaaf.core.config import settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(settings().database_url, pool_pre_ping=True, pool_size=10)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker[Session]:
    engine()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def db_session() -> Iterator[Session]:
    """Transaction-per-block helper: commits on success, rolls back on error."""
    with session_factory()() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def notify(session: Session, channel: str, payload: str = "") -> None:
    """Postgres NOTIFY as a *latency hint*. Listeners must still poll — NOTIFY
    drops messages across reconnects and when no listener is attached."""
    if session.get_bind().dialect.name != "postgresql":
        return  # unit tests on SQLite; polling is the source of truth anyway
    session.execute(text("SELECT pg_notify(:ch, :pl)"), {"ch": channel, "pl": payload})


def reset_for_tests(url: str) -> None:
    """Point the module at a different database (test harness only)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(url, pool_pre_ping=True)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)


def swap_engine(url: str) -> Callable[[], None]:
    """Point the module at `url` and hand back the undo.

    The engine is a process-global, so anything that borrows it — the fire
    drill borrows it for a throwaway SQLite file — has to give it back or
    every later caller in that process is silently talking to the wrong
    database, or to one that has since been deleted.

    Restoring by URL is not good enough: `str(engine().url)` masks the
    password, so a Postgres deployment would come back with a literal `***`
    where its credentials were. The engine OBJECT is what gets put back, which
    is also why the borrowed one is disposed here rather than left to a
    finalizer.
    """
    global _engine, _session_factory
    previous_engine, previous_factory = _engine, _session_factory
    _engine = create_engine(url, pool_pre_ping=True)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    borrowed = _engine

    def restore() -> None:
        global _engine, _session_factory
        borrowed.dispose()
        _engine, _session_factory = previous_engine, previous_factory

    return restore


# Ensure UTC on every connection so timestamp comparisons are unambiguous.
@event.listens_for(Engine, "connect")
def _set_utc(dbapi_connection, connection_record):  # noqa: ANN001
    try:
        with dbapi_connection.cursor() as cur:
            cur.execute("SET TIME ZONE 'UTC'")
    except Exception:
        # Non-Postgres (e.g. SQLite in unit tests) — timezone handled in app code.
        pass
