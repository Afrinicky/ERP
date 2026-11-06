"""Append-only enforcement for the financial record.

PRD §10 and §29.2 require that a posted journal entry is never amended and that the
audit trail is never altered. A service can honour that by having no amendment path,
but a service is one call site among many: a later feature, a data fix, a test helper
or a migration script could reach the same rows through the session and quietly
rewrite history.

So the rule is enforced where every write must pass. A ``before_flush`` listener
inspects what the unit of work is about to change and refuses the flush where the
change would alter a record that is closed to alteration. The guard is deliberately
narrow: a draft entry remains fully editable, and a posted entry still accepts the
one transition the accounting model allows — being marked as reversed by the entry
that reverses it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session, sessionmaker

from nickland.domain.errors import ConflictError
from nickland.domain.ledger import JournalState

SETTLED_STATES = frozenset({JournalState.POSTED.value, JournalState.REVERSED.value})
"""The states from which an entry's figures may no longer move."""

ENTRY_FIELDS_AFTER_POSTING = frozenset(
    {
        "state",
        "reversed_by_fk",
        "reversal_reason",
        "number",
        "attachment_ids",
    }
)
"""What may still change on a posted entry.

``state`` and the two reversal columns record that the entry was undone. ``number``
is assigned as part of posting itself. ``attachment_ids`` admits supporting evidence
being filed later, which adds to the record without restating it.
"""

LINE_FIELDS_AFTER_POSTING = frozenset({"state"})
"""A posted line's only permitted change is the state it carries from its entry."""


def _original_state(obj: Any) -> str | None:
    """The state the row held when it was last read or written.

    Taken from the attribute's history rather than the instance, because the instance
    already carries whatever the caller just assigned to it.
    """
    history = inspect(obj).attrs.state.history
    if history.deleted:
        return str(history.deleted[0])
    if history.unchanged:
        return str(history.unchanged[0])
    return None


def _changed_fields(obj: Any) -> set[str]:
    """The persisted columns this instance has pending changes on."""
    inspection = inspect(obj)
    return {
        attribute.key
        for attribute in inspection.mapper.column_attrs
        if inspection.attrs[attribute.key].history.has_changes()
    }


def _refuse_entry_change(obj: Any, changed: set[str]) -> None:
    offending = sorted(changed - ENTRY_FIELDS_AFTER_POSTING)
    raise ConflictError(
        f"Entry {obj.number or obj.id} is posted, so it cannot be amended "
        f"({', '.join(offending)}). Post a reversing entry instead: the original "
        "stays on the record and the correction is visible beside it.",
        entry_id=str(obj.id),
        fields=offending,
    )


def _refuse_line_change(obj: Any, changed: set[str]) -> None:
    offending = sorted(changed - LINE_FIELDS_AFTER_POSTING)
    raise ConflictError(
        "A posted journal line cannot be amended "
        f"({', '.join(offending)}). Reverse the entry and post it afresh.",
        line_id=str(obj.id),
        fields=offending,
    )


def _guard(session: Session, _context: Any, _instances: Any) -> None:
    """Refuse the flush where it would rewrite a settled record."""
    from nickland.persistence.ledger_models import JournalEntry, JournalLine
    from nickland.persistence.models import AuditEvent

    for obj in session.dirty:
        if not session.is_modified(obj, include_collections=False):
            continue
        if isinstance(obj, AuditEvent):
            raise ConflictError(
                "An audit entry cannot be altered. The trail is append-only, so a "
                "correction is recorded as a further entry.",
                event_id=str(obj.id),
            )
        if isinstance(obj, JournalEntry) and _original_state(obj) in SETTLED_STATES:
            changed = _changed_fields(obj)
            if changed - ENTRY_FIELDS_AFTER_POSTING:
                _refuse_entry_change(obj, changed)
        elif isinstance(obj, JournalLine) and _original_state(obj) in SETTLED_STATES:
            changed = _changed_fields(obj)
            if changed - LINE_FIELDS_AFTER_POSTING:
                _refuse_line_change(obj, changed)

    for obj in session.deleted:
        if isinstance(obj, AuditEvent):
            raise ConflictError(
                "An audit entry cannot be deleted. The trail is append-only.",
                event_id=str(obj.id),
            )
        if isinstance(obj, JournalEntry) and _original_state(obj) in SETTLED_STATES:
            raise ConflictError(
                f"Entry {obj.number or obj.id} is posted, so it cannot be deleted. "
                "Reverse it: an entry that was made remains on the record.",
                entry_id=str(obj.id),
            )
        if isinstance(obj, JournalLine) and _original_state(obj) in SETTLED_STATES:
            raise ConflictError(
                "A posted journal line cannot be deleted. Reverse its entry.",
                line_id=str(obj.id),
            )


def register_append_only_guards(factory: sessionmaker[Session] | None = None) -> None:
    """Attach the guard to every session in the process.

    The guard is registered on ``Session`` itself rather than on a session factory.
    Registering per factory looked tidier, and was wrong: ``event.contains`` on a
    factory consults the shared ``Session`` registry, so the second factory in a
    process saw the first factory's listener, concluded one was already present and
    attached nothing — and its sessions then flushed unguarded. The failure was
    silent, and it only appeared once a third test in a row built its own factory,
    which is exactly the kind of hole an append-only rule cannot afford.

    One registration on the class covers every session, whoever creates it. The
    ``factory`` argument is accepted so existing call sites read unchanged.
    """
    del factory
    global _REGISTERED
    if _REGISTERED:
        return
    event.listen(Session, "before_flush", _guard)
    _REGISTERED = True


_REGISTERED = False
