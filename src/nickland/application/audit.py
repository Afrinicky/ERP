"""Audit recording.

Entries are written inside the same transaction as the change they describe, so an
audited change cannot commit without its audit record, per PRD 10. The store is
append-only: there is no update or delete path.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from nickland.application.context import AnyActor
from nickland.persistence.models import AuditEvent
from nickland.platform.clock import Clock, SystemClock
from nickland.platform.logging import redact


class AuditAction(StrEnum):
    """Audited actions. Extended by each phase; never renamed once recorded."""

    # Identity
    SIGN_IN = "sign_in"
    SIGN_IN_FAILED = "sign_in_failed"
    SIGN_OUT = "sign_out"
    ACCOUNT_LOCKED = "account_locked"
    PASSWORD_CHANGED = "password_changed"
    USER_CREATED = "user_created"
    USER_AMENDED = "user_amended"
    USER_DEACTIVATED = "user_deactivated"
    USER_REACTIVATED = "user_reactivated"
    ROLE_ASSIGNED = "role_assigned"
    ROLE_WITHDRAWN = "role_withdrawn"
    ROLE_AMENDED = "role_amended"
    PERMISSION_AMENDED = "permission_amended"
    # Configuration
    BUSINESS_CREATED = "business_created"
    BUSINESS_AMENDED = "business_amended"
    BRANCH_CREATED = "branch_created"
    BRANCH_AMENDED = "branch_amended"
    WAREHOUSE_CREATED = "warehouse_created"
    WAREHOUSE_AMENDED = "warehouse_amended"
    SETTING_AMENDED = "setting_amended"
    NUMBERING_AMENDED = "numbering_amended"
    NUMBER_ALLOCATED = "number_allocated"
    CUSTOM_FIELD_CREATED = "custom_field_created"
    CUSTOM_FIELD_AMENDED = "custom_field_amended"
    CUSTOM_FIELD_WITHDRAWN = "custom_field_withdrawn"
    THEME_AMENDED = "theme_amended"
    THEME_RESET = "theme_reset"
    # Platform
    BACKUP_RUN = "backup_run"
    BACKUP_VERIFIED = "backup_verified"
    BACKUP_FAILED = "backup_failed"
    RESTORE_PERFORMED = "restore_performed"
    ATTACHMENT_UPLOADED = "attachment_uploaded"
    SETUP_COMPLETED = "setup_completed"


class AuditRecorder:
    """Writes audit entries. Constructed per unit of work."""

    __slots__ = ("_clock", "_session")

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def record(
        self,
        actor: AnyActor,
        action: AuditAction | str,
        *,
        entity_type: str,
        entity_id: uuid.UUID | None = None,
        entity_label: str | None = None,
        reason: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Append one entry. The caller does not commit; the unit of work does."""
        event = AuditEvent(
            business_id=actor.business_id,
            branch_id=actor.branch_id,
            occurred_at=self._clock.now(),
            actor_id=actor.user_id,
            actor_username=actor.username,
            session_id=actor.session_id,
            device=actor.device,
            action=str(action),
            entity_type=entity_type,
            entity_id=entity_id,
            entity_label=entity_label,
            reason=reason,
            before_values=_serialise(before),
            after_values=_serialise(after),
            context=_serialise(context),
        )
        self._session.add(event)
        return event

    def record_change(
        self,
        actor: AnyActor,
        action: AuditAction | str,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        entity_label: str | None,
        before: dict[str, Any],
        after: dict[str, Any],
        reason: str | None = None,
    ) -> AuditEvent | None:
        """Append an entry recording only the fields that actually changed.

        Returns ``None`` when nothing changed, so an idle save leaves no audit noise.
        """
        changed = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
        if not changed:
            return None
        return self.record(
            actor,
            action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_label=entity_label,
            reason=reason,
            before={key: before.get(key) for key in sorted(changed)},
            after={key: after.get(key) for key in sorted(changed)},
        )


def _serialise(values: dict[str, Any] | None) -> dict[str, Any] | None:
    """Make a value dictionary JSON-safe, with sensitive keys redacted."""
    if values is None:
        return None
    result: dict[str, Any] = {}
    for key, value in redact(values).items():
        if value is None or isinstance(value, (str, int, bool, float)):
            result[key] = value
        else:
            result[key] = str(value)
    return result
