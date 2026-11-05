"""Engine and session management.

Transaction boundaries are opened in the application layer and nowhere else, per the
architecture. This module provides the primitives; it does not use them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from nickland.platform.config import Settings
from nickland.platform.logging import get_logger

logger = get_logger(__name__)


def _configure_sqlite(engine: Engine) -> None:
    """Apply SQLite pragmas required for integrity and concurrency.

    Foreign keys are off by default in SQLite, which would silently defeat every
    relationship constraint. Write-ahead logging permits concurrent readers alongside
    one writer, which is the standalone deployment's requirement.
    """

    @event.listens_for(engine, "connect")
    def _on_connect(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()


def create_database_engine(settings: Settings, *, echo: bool = False) -> Engine:
    """Build the engine for this deployment."""
    url = settings.resolved_database_url()

    if url.startswith("sqlite"):
        in_memory = ":memory:" in url
        engine = create_engine(
            url,
            echo=echo,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if in_memory else None,
        )
        _configure_sqlite(engine)
        return engine

    return create_engine(
        url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        isolation_level="REPEATABLE READ",
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build the session factory.

    Autoflush is on. It was briefly off, on the reasoning that explicit flush points
    read more clearly, but that made a pending write invisible to a query issued later
    in the same transaction, which produced a succession of defects: a password reuse
    check that saw no history, a category path that read as empty, a search index that
    had not reached the column being searched. Flushing before a query is what
    SQLAlchemy does by default, and the explicitness was not worth the class of bug it
    invited. Transaction boundaries remain explicit; only the flush is automatic.
    """
    return sessionmaker(
        bind=engine,
        autoflush=True,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


@contextmanager
def unit_of_work(factory: sessionmaker[Session]) -> Iterator[Session]:
    """A single transaction.

    Commits on success, rolls back on any exception. Multi-record financial events are
    atomic because they occur inside one of these, per PRD 9.5.
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def database_is_reachable(engine: Engine) -> tuple[bool, str | None]:
    """Probe the database. Used by the health endpoint, per PRD 9.4."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, None
    except Exception as error:
        return False, str(error)
