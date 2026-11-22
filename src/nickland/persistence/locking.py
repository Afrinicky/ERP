"""Transaction-scoped advisory locks. PRD §9.5.

Several tills, desks and a back office write to one PostgreSQL database in the LAN edition.
Where two of them could read the same thing and act on it before either has written (the
last units of a product, the tender a duplicated callback reports, the period being closed
while an entry is posted into it), the operation first takes an advisory lock on what it is
about to change. The lock is released when the transaction ends, so it can never be left
held by a client that disconnected.

A row lock does not serve where the row may not exist yet (the first stock position for a
product in a warehouse, a journal entry with an idempotency key not yet used), which is
why the lock is on a name rather than a row.

SQLite has no advisory locks, and its single writer does not of itself protect a check
made before the write: the driver begins a transaction only just before the first
statement that writes, so the check runs outside any transaction and two threads can both
pass it. There the lock takes the database's write lock instead (``BEGIN IMMEDIATE``),
which every other writer waits for, so the check and the write that follows it are as
serialised as under PostgreSQL's lock. A transaction that has already written holds that
lock already. It is coarser than a named lock, and on one machine's database that is no
cost worth avoiding.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

Part = str | uuid.UUID | int | None

EXCLUSIVE = text("SELECT pg_advisory_xact_lock(:key)")
SHARED = text("SELECT pg_advisory_xact_lock_shared(:key)")


def lock_key(*parts: Part) -> int:
    """A signed 64-bit key for a name made of parts."""
    name = "\x1f".join("" if part is None else str(part) for part in parts)
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big", signed=True)


def supports_advisory_locks(session: Session) -> bool:
    bind = session.get_bind()
    return bind.dialect.name == "postgresql"


def _hold_sqlite_write_lock(session: Session) -> None:
    """Begin the transaction holding SQLite's write lock, unless it already holds it.

    The driver has begun a transaction only if something was written, and writing took the
    lock; otherwise the transaction is begun now, immediately rather than deferred, waiting
    (up to the busy timeout) for any other writer to finish.
    """
    connection = session.connection()
    dbapi = connection.connection.dbapi_connection
    if dbapi is not None and not getattr(dbapi, "in_transaction", True):
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def advisory_lock(session: Session, *parts: Part, shared: bool = False) -> None:
    """Wait for, and hold until the transaction ends, the lock named by ``parts``.

    A shared lock admits other shared holders and excludes an exclusive one: postings take
    their period's lock shared, a close takes it exclusive. On SQLite both take the
    database's write lock; see the module's note.
    """
    if not supports_advisory_locks(session):
        _hold_sqlite_write_lock(session)
        return
    statement = SHARED if shared else EXCLUSIVE
    session.execute(statement, {"key": lock_key(*parts)})


def try_advisory_lock(session: Session, *parts: Part) -> bool:
    """Take the lock if it is free, without waiting.

    On SQLite this waits for the write lock like :func:`advisory_lock`, and is then true:
    there is no lock to find taken, only a writer to wait for.
    """
    if not supports_advisory_locks(session):
        _hold_sqlite_write_lock(session)
        return True
    return bool(
        session.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key(*parts)}
        ).scalar()
    )


__all__ = ["advisory_lock", "lock_key", "supports_advisory_locks", "try_advisory_lock"]
