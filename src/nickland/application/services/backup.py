"""Backup, verification and restore.

The database and the attachment store are backed up together as one consistent set,
per PRD 9.5. Each backup is verified on completion; a restore is an audited,
permission-controlled action.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditAction, AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.persistence.models import BackupRecord
from nickland.platform.clock import Clock, SystemClock
from nickland.platform.config import Settings
from nickland.platform.logging import get_logger

logger = get_logger(__name__)

_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class BackupOutcome:
    """The result of a backup run."""

    record_id: object
    path: Path
    size_bytes: int
    content_hash: str
    verified: bool
    note: str


class BackupService:
    """Backup and restore of the database and attachment store."""

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
        self._audit = AuditRecorder(session, self._clock)

    def run(
        self, actor: AnyActor, *, trigger: str = "manual", include_attachments: bool = True
    ) -> BackupOutcome:
        """Take a backup and verify it.

        A SQLite database is copied through the online backup API rather than by file
        copy, so a backup taken while the application is running is consistent.
        """
        actor.require("backup.manage")
        started = self._clock.now()
        self._settings.backup_directory.mkdir(parents=True, exist_ok=True)

        record = BackupRecord(
            business_id=actor.business_id,
            started_at=started,
            status="running",
            trigger=trigger,
            includes_attachments=include_attachments,
            schema_revision=self._schema_revision(),
        )
        self._db.add(record)
        self._db.flush()

        stamp = started.strftime("%Y%m%d-%H%M%S")
        archive = self._settings.backup_directory / f"nickland-{stamp}.zip"

        try:
            with tempfile.TemporaryDirectory() as staging_name:
                staging = Path(staging_name)
                database_copy = staging / "nickland.db"
                self._copy_database(database_copy)

                with zipfile.ZipFile(
                    archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
                ) as bundle:
                    bundle.write(database_copy, "database/nickland.db")
                    bundle.writestr(
                        "manifest.txt",
                        "\n".join(
                            [
                                "Nickland ERP backup",
                                f"taken_at={started.isoformat()}",
                                f"schema_revision={record.schema_revision}",
                                f"includes_attachments={include_attachments}",
                                f"engine={self._engine.dialect.name}",
                            ]
                        ),
                    )
                    if include_attachments and self._settings.attachment_directory.exists():
                        root = self._settings.attachment_directory
                        for item in sorted(root.rglob("*")):
                            if item.is_file():
                                bundle.write(item, f"attachments/{item.relative_to(root)}")

            size = archive.stat().st_size
            digest = _hash_file(archive)
            verified, note = self._verify(archive)

            record.completed_at = self._clock.now()
            record.status = "verified" if verified else "unverified"
            record.storage_key = archive.name
            record.size_bytes = size
            record.content_hash = digest
            record.verified_at = self._clock.now() if verified else None
            record.verification_note = note

            self._audit.record(
                actor,
                AuditAction.BACKUP_RUN if verified else AuditAction.BACKUP_FAILED,
                entity_type="backup",
                entity_id=record.id,
                entity_label=archive.name,
                context={
                    "trigger": trigger,
                    "size_bytes": size,
                    "verified": verified,
                    "note": note,
                },
            )
            self._prune(actor)
            logger.info("Backup %s completed: %s", archive.name, note)

            return BackupOutcome(
                record_id=record.id,
                path=archive,
                size_bytes=size,
                content_hash=digest,
                verified=verified,
                note=note,
            )

        except Exception as error:
            record.completed_at = self._clock.now()
            record.status = "failed"
            record.failure_reason = str(error)
            archive.unlink(missing_ok=True)
            self._audit.record(
                actor,
                AuditAction.BACKUP_FAILED,
                entity_type="backup",
                entity_id=record.id,
                entity_label=archive.name,
                context={"trigger": trigger, "error": str(error)},
            )
            logger.exception("Backup failed")
            raise

    def history(self, actor: AnyActor, limit: int = 30) -> list[BackupRecord]:
        actor.require("health.view")
        return list(
            self._db.scalars(
                select(BackupRecord)
                .where(BackupRecord.business_id == actor.business_id)
                .order_by(BackupRecord.started_at.desc())
                .limit(limit)
            )
        )

    def latest(self, actor: AnyActor) -> BackupRecord | None:
        return self._db.scalar(
            select(BackupRecord)
            .where(
                BackupRecord.business_id == actor.business_id,
                BackupRecord.status == "verified",
            )
            .order_by(BackupRecord.started_at.desc())
            .limit(1)
        )

    def restore(
        self, actor: AnyActor, archive_name: str, *, reason: str, confirm: bool = False
    ) -> Path:
        """Stage a restore.

        The archive is verified and extracted to a staging directory. The swap itself is
        performed by the application at the next start, while nothing holds the
        database open — a live swap risks a partially written file.
        """
        actor.require("backup.manage")
        if not reason.strip():
            raise ValidationError("A reason is required to restore.", field="reason")
        if not confirm:
            raise ConflictError(
                "A restore replaces the current database. Confirm the action to proceed."
            )

        archive = self._settings.backup_directory / Path(archive_name).name
        if not archive.exists():
            raise NotFoundError(f"Backup not found: {archive_name}")

        verified, note = self._verify(archive)
        if not verified:
            raise ConflictError(f"This backup did not verify and will not be restored. {note}")

        staging = self._settings.data_directory / "restore-pending"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                # Reject any path that would escape the staging directory.
                resolved = (staging / member).resolve()
                if not str(resolved).startswith(str(staging.resolve())):
                    raise ConflictError(f"Backup contains an unsafe path: {member}")
            bundle.extractall(staging)

        self._audit.record(
            actor,
            AuditAction.RESTORE_PERFORMED,
            entity_type="backup",
            entity_label=archive.name,
            reason=reason,
            context={"staged_at": str(staging), "content_hash": _hash_file(archive)},
        )
        logger.warning("Restore staged from %s; applied at next start", archive.name)
        return staging

    def _database_path(self) -> Path:
        """The SQLite file this engine actually uses.

        Read from the engine rather than from the configured default, so a deployment
        pointed at another path is backed up correctly.
        """
        database = self._engine.url.database
        if not database:
            raise ConflictError("The database path could not be determined.")
        return Path(database)

    def _copy_database(self, destination: Path) -> None:
        """Copy the database consistently."""
        if self._engine.dialect.name == "sqlite":
            source_path = self._database_path()
            if not source_path.exists():
                raise ConflictError(
                    f"The database file is not present at {source_path}."
                )
            source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
            try:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
            return
        raise ConflictError(
            "Backup of a PostgreSQL deployment is performed by the host service's "
            "scheduled dump, not from a client. See the LAN deployment procedure."
        )

    def _verify(self, archive: Path) -> tuple[bool, str]:
        """Verify the archive and run an integrity check on the contained database."""
        try:
            with zipfile.ZipFile(archive) as bundle:
                damaged = bundle.testzip()
                if damaged is not None:
                    return False, f"Archive member is corrupt: {damaged}"
                if "database/nickland.db" not in bundle.namelist():
                    return False, "Archive does not contain a database."
                with tempfile.TemporaryDirectory() as staging:
                    extracted = Path(staging) / "nickland.db"
                    with bundle.open("database/nickland.db") as member, extracted.open("wb") as out:
                        shutil.copyfileobj(member, out)
                    connection = sqlite3.connect(f"file:{extracted}?mode=ro", uri=True)
                    try:
                        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
                        if result != "ok":
                            return False, f"Integrity check reported: {result}"
                        tables = connection.execute(
                            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                        ).fetchone()[0]
                    finally:
                        connection.close()
            return True, f"Integrity check passed. {tables} tables present."
        except Exception as error:
            return False, f"Verification failed: {error}"

    def _prune(self, actor: AnyActor) -> None:
        """Remove archives beyond the retention count."""
        keep = self._settings.backup_retain_count
        if keep <= 0:
            return
        # Autoflush is off, so the run just completed must be flushed to be counted.
        self._db.flush()
        records = self._db.scalars(
            select(BackupRecord)
            .where(
                BackupRecord.business_id == actor.business_id,
                BackupRecord.storage_key.is_not(None),
            )
            .order_by(BackupRecord.started_at.desc())
        ).all()
        for record in records[keep:]:
            if record.storage_key:
                (self._settings.backup_directory / record.storage_key).unlink(missing_ok=True)
                record.storage_key = None
                record.verification_note = "Archive removed under the retention policy."

    def _schema_revision(self) -> str | None:
        try:
            with self._engine.connect() as connection:
                return connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
        except Exception:
            return None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def backup_is_current(latest: datetime | None, now: datetime, *, hours: int = 48) -> bool:
    """Whether the most recent verified backup is recent enough."""
    if latest is None:
        return False
    return (now - latest).total_seconds() <= hours * 3600
