"""Backup, verification and restore.

The database and the attachment store are backed up together as one consistent set,
per PRD 9.5. Each backup is verified on completion; a restore is an audited,
permission-controlled action.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, make_url, select, text
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditAction, AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.persistence import pgtools
from nickland.persistence.models import BackupRecord
from nickland.persistence.transfer import digests, manifest
from nickland.platform.clock import Clock, SystemClock
from nickland.platform.config import Settings
from nickland.platform.logging import get_logger

logger = get_logger(__name__)

_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class DrillReport:
    """What a restore drill found."""

    archive: str
    taken_at: str | None
    engine: str
    tables: int
    rows: int
    table_mismatches: list[str]
    files: int
    file_mismatches: list[str]
    seconds: float

    @property
    def reproduced(self) -> bool:
        return not self.table_mismatches and not self.file_mismatches


@dataclass(frozen=True, slots=True)
class _Scratch:
    engine: Engine
    discard: Callable[[], None]


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
                database_copy, tables = self._copy_database(staging)
                files = manifest(self._settings.attachment_directory) if include_attachments else {}

                with zipfile.ZipFile(
                    archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
                ) as bundle:
                    bundle.write(database_copy, f"database/{database_copy.name}")
                    bundle.writestr(
                        "manifest.json",
                        json.dumps(
                            {
                                "taken_at": started.isoformat(),
                                "engine": self._engine.dialect.name,
                                "schema_revision": record.schema_revision,
                                "tables": tables,
                                "attachments": files,
                            },
                            indent=1,
                            sort_keys=True,
                        ),
                    )
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

    def _copy_database(self, staging: Path) -> tuple[Path, dict[str, list[Any]]]:
        """Copy the database consistently, with every table's digest as of the copy.

        SQLite is copied through the online backup API and digested from the copy.
        PostgreSQL is dumped by ``pg_dump`` from an exported snapshot, and digested in the
        transaction that holds that snapshot, so the digests describe exactly the rows the
        dump contains however busy the host is.
        """
        if self._engine.dialect.name == "sqlite":
            destination = staging / "nickland.db"
            source_path = self._database_path()
            if not source_path.exists():
                raise ConflictError(f"The database file is not present at {source_path}.")
            source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
            try:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
            copy = create_engine(f"sqlite+pysqlite:///{destination}")
            try:
                return destination, digests(copy)
            finally:
                copy.dispose()
        destination = staging / "nickland.dump"
        with self._engine.connect() as connection:
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
            with connection.begin():
                snapshot = connection.execute(text("SELECT pg_export_snapshot()")).scalar()
                tables = digests(connection)
                pgtools.dump(
                    self._engine.url,
                    destination,
                    snapshot=str(snapshot),
                    directory=self._settings.postgres_bin,
                    schema=pgtools.schema_of(self._engine.url),
                )
        return destination, tables

    def _verify(self, archive: Path) -> tuple[bool, str]:
        """Verify the archive: its members, the database within it, and every attachment
        against the digest recorded when it was taken."""
        try:
            with zipfile.ZipFile(archive) as bundle:
                damaged = bundle.testzip()
                if damaged is not None:
                    return False, f"Archive member is corrupt: {damaged}"
                names = set(bundle.namelist())
                recorded = (
                    json.loads(bundle.read("manifest.json")) if "manifest.json" in names else {}
                )
                for relative, expected in (recorded.get("attachments") or {}).items():
                    stored = f"attachments/{relative}"
                    if stored not in names:
                        return False, f"Attachment missing from the archive: {relative}"
                    if hashlib.sha256(bundle.read(stored)).hexdigest() != expected:
                        return False, f"Attachment altered in the archive: {relative}"
                with tempfile.TemporaryDirectory() as staging:
                    if "database/nickland.dump" in names:
                        extracted = Path(staging) / "nickland.dump"
                        with (
                            bundle.open("database/nickland.dump") as member,
                            extracted.open("wb") as out,
                        ):
                            shutil.copyfileobj(member, out)
                        objects = pgtools.list_contents(
                            extracted, directory=self._settings.postgres_bin
                        )
                        return True, f"Dump readable: {objects} objects listed."
                    if "database/nickland.db" not in names:
                        return False, "Archive does not contain a database."
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

    # -- the restore drill ----------------------------------------------------------------

    def drill(self, actor: AnyActor, archive_name: str | None = None) -> DrillReport:
        """Restore a backup somewhere harmless and prove it reproduces what was taken.

        The database is restored into a scratch database (PostgreSQL) or file (SQLite),
        every table is digested and compared with the digests recorded at backup time,
        and every attachment is extracted and compared with its recorded digest. The
        scratch copy is then removed. Nothing the business is using is touched.
        """
        actor.require("backup.manage")
        if archive_name is None:
            latest = self.latest(actor)
            if latest is None or not latest.storage_key:
                raise NotFoundError("No verified backup is available to drill.")
            archive_name = latest.storage_key
        archive = self._settings.backup_directory / Path(archive_name).name
        if not archive.exists():
            raise NotFoundError(f"Backup not found: {archive_name}")
        started = self._clock.now()
        with zipfile.ZipFile(archive) as bundle, tempfile.TemporaryDirectory() as staging_name:
            staging = Path(staging_name)
            names = set(bundle.namelist())
            if "manifest.json" not in names:
                raise ConflictError("This backup predates recorded digests and cannot be drilled.")
            recorded = json.loads(bundle.read("manifest.json"))
            for member in names:
                resolved = (staging / member).resolve()
                if not str(resolved).startswith(str(staging.resolve())):
                    raise ConflictError(f"Backup contains an unsafe path: {member}")
            bundle.extractall(staging)
            restored = self._restore_scratch(staging)
            try:
                tables = digests(restored.engine)
            finally:
                restored.discard()
            files = manifest(staging / "attachments")
        expected_tables: dict[str, list[Any]] = recorded.get("tables", {})
        expected_files: dict[str, str] = recorded.get("attachments", {})
        table_mismatches = sorted(
            name for name, value in expected_tables.items() if tables.get(name) != value
        )
        file_mismatches = sorted(
            name for name, value in expected_files.items() if files.get(name) != value
        )
        report = DrillReport(
            archive=archive.name,
            taken_at=recorded.get("taken_at"),
            engine=str(recorded.get("engine")),
            tables=len(expected_tables),
            rows=sum(int(value[0]) for value in expected_tables.values()),
            table_mismatches=table_mismatches,
            files=len(expected_files),
            file_mismatches=file_mismatches,
            seconds=(self._clock.now() - started).total_seconds(),
        )
        self._audit.record(
            actor,
            "restore_drill",
            entity_type="backup",
            entity_label=archive.name,
            context={
                "reproduced": report.reproduced,
                "tables": report.tables,
                "rows": report.rows,
                "files": report.files,
                "table_mismatches": table_mismatches[:20],
                "file_mismatches": file_mismatches[:20],
            },
        )
        return report

    def _restore_scratch(self, staging: Path) -> _Scratch:
        dump = staging / "database" / "nickland.dump"
        if dump.exists():
            admin_url = make_url(
                self._settings.migration_database_url
                or self._engine.url.render_as_string(hide_password=False)
            )
            name = f"{admin_url.database}_drill_{secrets.token_hex(4)}"
            admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
            _database_statement(admin, "CREATE DATABASE {}", name)
            pgtools.restore(admin_url, dump, name, directory=self._settings.postgres_bin)
            engine = create_engine(admin_url.set(database=name))

            def discard() -> None:
                engine.dispose()
                _database_statement(admin, "DROP DATABASE IF EXISTS {}", name)
                admin.dispose()

            return _Scratch(engine, discard)
        database = staging / "database" / "nickland.db"
        engine = create_engine(f"sqlite+pysqlite:///{database}")
        return _Scratch(engine, engine.dispose)

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
                return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:
            return None


def _database_statement(engine: Engine, template: str, name: str) -> None:
    """CREATE or DROP DATABASE, which take no parameters: the name is quoted by the driver."""
    from psycopg import sql

    with engine.connect() as connection:
        raw = connection.connection.driver_connection
        assert raw is not None
        raw.execute(sql.SQL(template).format(sql.Identifier(name)))


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
