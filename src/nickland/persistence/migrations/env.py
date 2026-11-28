"""Alembic environment.

Migrations are versioned with a tested downgrade path, and the database is backed up
before any upgrade, per PRD 9.5. The backup is taken by the application's upgrade
procedure, not here.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import pool

from nickland.persistence.base import Base
from nickland.persistence.engine import create_database_engine
from nickland.platform.config import get_settings

import nickland.persistence.catalogue_models
import nickland.persistence.models  # noqa: F401  — registers the mapped classes

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    override = config.get_main_option("sqlalchemy.url", None)
    if override:
        return override
    return get_settings().resolved_database_url()


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    settings = get_settings()
    override = config.get_main_option("sqlalchemy.url", None)
    if override:
        from sqlalchemy import create_engine

        connectable = create_engine(override, poolclass=pool.NullPool, future=True)
    else:
        connectable = create_database_engine(settings)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # Batch mode lets SQLite alter tables, which it cannot do natively.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
