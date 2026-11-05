"""Shared fixtures.

Integration tests run against SQLite by default and against PostgreSQL when
NICKLAND_TEST_POSTGRES_URL is set, per PRD 9.8.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from nickland.application.context import Actor
from nickland.application.services.identity import IdentityService
from nickland.application.services.setup import SetupRequest, SetupService
from nickland.domain.permissions import ALL_CODES
from nickland.persistence.base import Base
from nickland.persistence.engine import create_database_engine, create_session_factory
from nickland.platform.clock import FixedClock
from nickland.platform.config import Environment, Settings

ADMIN_PASSWORD = "Provision9Admin"


@pytest.fixture
def clock() -> FixedClock:
    """A frozen clock, so time-dependent assertions are exact."""
    return FixedClock(datetime(2026, 11, 5, 9, 30, tzinfo=UTC))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment=Environment.TEST,
        data_directory=tmp_path / "data",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        backup_retain_count=3,
    )


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    settings.ensure_directories()
    created = create_database_engine(settings)
    Base.metadata.create_all(created)
    # Health and backup read the schema revision, so record one.
    from sqlalchemy import text

    with created.begin() as connection:
        connection.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)"))
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('test')"))
    yield created
    created.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def provisioned(db: Session, clock: FixedClock) -> dict[str, uuid.UUID]:
    """A provisioned business with an administrator."""
    service = SetupService(db, clock=clock)
    result = service.provision(
        SetupRequest(
            business_name="Nicholas Nickland Enterprise",
            administrator_username="proprietor",
            administrator_full_name="N. Nickland",
            administrator_password=ADMIN_PASSWORD,
            theme_preset="charcoal",
            tax_identifier="C0001234567",
            email="accounts@example.test",
        )
    )
    db.flush()
    return {
        "business_id": result.business_id,
        "branch_id": result.branch_id,
        "warehouse_id": result.warehouse_id,
        "administrator_id": result.administrator_id,
    }


@pytest.fixture
def admin(db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]) -> Actor:
    """The administrator, authenticated."""
    service = IdentityService(db, clock=clock)
    result = service.sign_in(
        "proprietor",
        ADMIN_PASSWORD,
        business_id=provisioned["business_id"],
        device="test-suite",
    )
    return result.actor


def make_actor(
    business_id: uuid.UUID,
    *,
    permissions: set[str] | None = None,
    branch_id: uuid.UUID | None = None,
) -> Actor:
    """An actor with an explicit permission set, for permission-denial tests."""
    return Actor(
        user_id=uuid.uuid4(),
        username="tester",
        full_name="Test Actor",
        business_id=business_id,
        branch_id=branch_id,
        permissions=frozenset(permissions if permissions is not None else ALL_CODES),
    )


def postgres_url() -> str | None:
    return os.environ.get("NICKLAND_TEST_POSTGRES_URL")
