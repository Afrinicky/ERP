"""Declarative base and common mixins."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from nickland.persistence.types import UtcDateTime, Uuid

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""Deterministic constraint names, so Alembic can alter them by name."""


class Base(DeclarativeBase):
    """Declarative base for every mapped class."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} {identifier}>"


class IdentifiedMixin:
    """A UUIDv7 primary key."""

    @staticmethod
    def _new_id() -> uuid.UUID:
        from nickland.platform.ids import new_id

        return new_id()

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=lambda: IdentifiedMixin._new_id()
    )


class TimestampedMixin:
    """Creation and amendment instants, maintained by the database."""

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BusinessScopedMixin:
    """Business scoping, present from the first migration, per ADR-0003.

    Every business-owned table carries this. Application services derive the value
    from the actor's scoping context and apply it to every query.
    """

    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)


class BranchScopedMixin:
    """Branch scoping. Nullable where a record is business-wide."""

    branch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)


def as_dict(instance: Any) -> dict[str, Any]:
    """Shallow column dictionary. Used when recording audit before and after values."""
    mapper = instance.__class__.__mapper__
    return {column.key: getattr(instance, column.key) for column in mapper.column_attrs}
