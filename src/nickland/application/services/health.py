"""Health reporting.

Reports the local service, the database connection, the network interface, storage
capacity, the backup position and the worker queue, per PRD 9.4.
"""

from __future__ import annotations

import shutil
import socket
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session as DbSession

from nickland.application.context import AnyActor
from nickland.application.services.backup import backup_is_current
from nickland.persistence.engine import database_is_reachable
from nickland.platform.clock import Clock, SystemClock
from nickland.platform.config import DeploymentMode, Settings


class HealthState(StrEnum):
    """A check's outcome.

    ``UNAVAILABLE`` means a capability requiring connectivity cannot be reached. It is
    never reported as a failure, because offline operation is the expected standalone
    condition, per PRD 9.4.
    """

    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Check:
    """One health check."""

    name: str
    state: HealthState
    detail: str
    measurements: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HealthReport:
    """The whole report, as the status strip consumes it."""

    state: HealthState
    deployment_mode: str
    checks: list[Check]

    @property
    def is_operational(self) -> bool:
        return self.state in (HealthState.OK, HealthState.DEGRADED)


class HealthService:
    """Produces the health report."""

    def __init__(
        self,
        session: DbSession,
        engine: Engine,
        settings: Settings,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._db = session
        self._engine = engine
        self._settings = settings
        self._clock = clock or SystemClock()

    def report(self, actor: AnyActor | None = None) -> HealthReport:
        """Run every check."""
        checks = [
            self._service(),
            self._database(),
            self._migrations(),
            self._storage(),
            self._network(),
        ]
        if actor is not None:
            checks.append(self._backup(actor))
        checks.append(self._workers())

        worst = HealthState.OK
        order = {
            HealthState.OK: 0,
            HealthState.UNAVAILABLE: 0,
            HealthState.DEGRADED: 1,
            HealthState.FAILED: 2,
        }
        for check in checks:
            if order[check.state] > order[worst]:
                worst = check.state

        return HealthReport(
            state=worst,
            deployment_mode=self._settings.deployment_mode.value,
            checks=checks,
        )

    def _service(self) -> Check:
        return Check(
            name="Application service",
            state=HealthState.OK,
            detail="Running.",
            measurements={
                "environment": self._settings.environment.value,
                "deployment_mode": self._settings.deployment_mode.value,
            },
        )

    def _database(self) -> Check:
        reachable, error = database_is_reachable(self._engine)
        if not reachable:
            return Check(
                name="Database",
                state=HealthState.FAILED,
                detail=f"Not reachable. {error}",
            )
        return Check(
            name="Database",
            state=HealthState.OK,
            detail="Connected.",
            measurements={"engine": self._engine.dialect.name},
        )

    def _migrations(self) -> Check:
        try:
            with self._engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
        except Exception as error:
            return Check(
                name="Schema",
                state=HealthState.FAILED,
                detail=f"Schema version could not be read. {error}",
            )
        if revision is None:
            return Check(
                name="Schema",
                state=HealthState.FAILED,
                detail="No schema revision recorded. Run the migrations.",
            )
        return Check(
            name="Schema",
            state=HealthState.OK,
            detail=f"At revision {revision}.",
            measurements={"revision": revision},
        )

    def _storage(self) -> Check:
        try:
            usage = shutil.disk_usage(self._settings.data_directory)
        except OSError as error:
            return Check(
                name="Storage",
                state=HealthState.FAILED,
                detail=f"Data directory is not accessible. {error}",
            )

        free_mb = usage.free // (1024 * 1024)
        measurements = {
            "free_mb": free_mb,
            "total_mb": usage.total // (1024 * 1024),
            "path": str(self._settings.data_directory),
        }
        if free_mb < 256:
            return Check(
                name="Storage",
                state=HealthState.FAILED,
                detail=f"Only {free_mb} MB free. Free space before transacting further.",
                measurements=measurements,
            )
        if free_mb < 2048:
            return Check(
                name="Storage",
                state=HealthState.DEGRADED,
                detail=f"{free_mb} MB free. Consider freeing space.",
                measurements=measurements,
            )
        return Check(
            name="Storage",
            state=HealthState.OK,
            detail=f"{free_mb} MB free.",
            measurements=measurements,
        )

    def _network(self) -> Check:
        """Probe the network.

        Absence of a network is not a failure in standalone mode; it is the expected
        condition, per PRD 9.4.
        """
        if self._settings.deployment_mode is DeploymentMode.STANDALONE:
            reachable = _can_resolve()
            return Check(
                name="Network",
                state=HealthState.OK if reachable else HealthState.UNAVAILABLE,
                detail=(
                    "Connected. Online features are available."
                    if reachable
                    else "Offline. Core workflows are unaffected; "
                    "online features are unavailable."
                ),
            )
        reachable = _can_resolve()
        return Check(
            name="Network",
            state=HealthState.OK if reachable else HealthState.FAILED,
            detail=(
                "Connected."
                if reachable
                else "Not reachable. LAN clients cannot contact the host."
            ),
        )

    def _backup(self, actor: AnyActor) -> Check:
        from nickland.application.services.backup import BackupService

        service = BackupService(self._db, self._engine, self._settings, clock=self._clock)
        latest = service.latest(actor)
        now = self._clock.now()

        if not self._settings.backup_enabled:
            return Check(
                name="Backup",
                state=HealthState.DEGRADED,
                detail="Backup is disabled. Enable it before transacting in earnest.",
            )
        if latest is None:
            return Check(
                name="Backup",
                state=HealthState.DEGRADED,
                detail="No verified backup has been taken.",
            )
        current = backup_is_current(latest.started_at, now)
        return Check(
            name="Backup",
            state=HealthState.OK if current else HealthState.DEGRADED,
            detail=(
                f"Last verified backup {latest.started_at:%Y-%m-%d %H:%M} UTC."
                if current
                else f"Last verified backup {latest.started_at:%Y-%m-%d %H:%M} UTC, "
                "which is more than 48 hours ago."
            ),
            measurements={
                "taken_at": latest.started_at.isoformat(),
                "size_bytes": latest.size_bytes,
            },
        )

    def _workers(self) -> Check:
        """Background worker queue.

        Phase 1 runs scheduled work in-process; the queue report becomes meaningful when
        the worker pool arrives.
        """
        return Check(
            name="Background work",
            state=HealthState.OK,
            detail="In-process scheduler running.",
            measurements={"queued": 0},
        )


def _can_resolve(host: str = "one.one.one.one", timeout: float = 1.5) -> bool:
    """Whether outbound name resolution succeeds. Never raises."""
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return True
    except (OSError, socket.gaierror):
        return False
    finally:
        socket.setdefaulttimeout(None)
