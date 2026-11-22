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

SQLite serialises every write transaction already, and has no advisory locks; there the
lock is a no-op.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

Part = str | uuid.UUID | int | None


def lock_key(*parts: Part) -> int:
    """A signed 64-bit key for a name made of parts."""
    name = "\x1f".join("" if part is None else str(part) for part in parts)
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big", signed=True)


def supports_advisory_locks(session: Session) -> bool:
    bind = session.get_bind()
    return bind.dialect.name == "postgresql"


def advisory_lock(session: Session, *parts: Part, shared: bool = False) -> None:
    """Wait for, and hold until the transaction ends, the lock named by ``parts``.

    A shared lock admits other shared holders and excludes an exclusive one: postings take
    their period's lock shared, a close takes it exclusive.
    """
    if not supports_advisory_locks(session):
        return
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    session.execute(text(f"SELECT {function}(:key)"), {"key": lock_key(*parts)})


def try_advisory_lock(session: Session, *parts: Part) -> bool:
    """Take the lock if it is free, without waiting. Always true on SQLite."""
    if not supports_advisory_locks(session):
        return True
    return bool(
        session.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key(*parts)}
        ).scalar()
    )


__all__ = ["advisory_lock", "lock_key", "supports_advisory_locks", "try_advisory_lock"]
