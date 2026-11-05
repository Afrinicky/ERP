"""Backup behaviour that differs between engines. PRD 9.5, PRD 9.8."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.backup import BackupService, backup_is_current
from nickland.domain.errors import ConflictError, PermissionDeniedError
from nickland.platform.clock import FixedClock
from nickland.platform.config import Settings
from tests.conftest import make_actor


def _service(
    db: Session, engine: Engine, settings: Settings, clock: FixedClock
) -> BackupService:
    return BackupService(db, engine, settings, clock=clock)


def test_a_postgresql_deployment_refuses_a_client_side_backup(
    db: Session,
    engine: Engine,
    settings: Settings,
    clock: FixedClock,
    admin: Actor,
    engine_name: str,
) -> None:
    """A client must not attempt to dump the host's database.

    The standalone path uses SQLite's online backup API. A PostgreSQL deployment is
    backed up by the host service, which Phase 15 delivers; until then the attempt is
    refused with that explanation rather than producing an unusable archive.
    """
    if engine_name != "postgresql":
        pytest.skip("asserts the PostgreSQL path")
    with pytest.raises(ConflictError, match="host service"):
        _service(db, engine, settings, clock).run(admin, trigger="manual")


def test_backup_requires_the_permission(
    db: Session, engine: Engine, settings: Settings, clock: FixedClock, admin: Actor
) -> None:
    weak = make_actor(admin.business_id, permissions={"health.view"})
    with pytest.raises(PermissionDeniedError, match="backup.manage"):
        _service(db, engine, settings, clock).run(weak)


def test_a_failed_backup_is_recorded_rather_than_silently_lost(
    db: Session,
    engine: Engine,
    settings: Settings,
    clock: FixedClock,
    admin: Actor,
    engine_name: str,
) -> None:
    if engine_name != "postgresql":
        pytest.skip("uses the PostgreSQL refusal to force a failure")
    service = _service(db, engine, settings, clock)
    with pytest.raises(ConflictError):
        service.run(admin, trigger="manual")
    db.commit()
    history = service.history(admin)
    assert len(history) == 1
    assert history[0].status == "failed"
    assert history[0].failure_reason


class TestCurrency:
    def test_no_backup_is_never_current(self, clock: FixedClock) -> None:
        assert not backup_is_current(None, clock.now())

    def test_a_recent_backup_is_current(self, clock: FixedClock) -> None:
        from datetime import timedelta

        assert backup_is_current(clock.now() - timedelta(hours=12), clock.now())

    def test_an_old_backup_is_not_current(self, clock: FixedClock) -> None:
        from datetime import timedelta

        assert not backup_is_current(clock.now() - timedelta(hours=49), clock.now())
