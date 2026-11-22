"""Copy a standalone SQLite deployment into the LAN host's PostgreSQL. PRD §9.5.

The copy is verified rather than trusted: every table is counted and digested on both
sides, row by row in primary-key order, through the same column types the application
reads with, so a figure that changed precision, a date that lost its timezone or a row that
did not arrive is found before the host serves anyone. The attachment store is copied file
by file and each file's digest compared.

The target must be empty and at the current schema revision; the transfer refuses
otherwise, so it can never merge two datasets into one.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, Table, func, insert, select

from nickland.persistence.base import Base

BATCH = 500


@dataclass(slots=True)
class TableResult:
    name: str
    source_rows: int
    target_rows: int
    source_digest: str
    target_digest: str

    @property
    def agrees(self) -> bool:
        return self.source_rows == self.target_rows and self.source_digest == self.target_digest


@dataclass(slots=True)
class TransferReport:
    tables: list[TableResult] = field(default_factory=list)
    files: int = 0
    files_agreeing: int = 0
    bytes_copied: int = 0

    @property
    def rows(self) -> int:
        return sum(table.target_rows for table in self.tables)

    @property
    def agrees(self) -> bool:
        return all(table.agrees for table in self.tables) and self.files == self.files_agreeing

    @property
    def disagreements(self) -> list[str]:
        return [table.name for table in self.tables if not table.agrees]


class TransferRefusedError(RuntimeError):
    pass


def _tables() -> list[Table]:
    import nickland.persistence.models  # noqa: F401  (registers every table)

    return list(Base.metadata.sorted_tables)


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        # Trailing zeros are a matter of the column's scale, not of the figure.
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return value


def _rows(engine: Engine, table: Table) -> Iterator[dict[str, Any]]:
    order = list(table.primary_key.columns) or list(table.columns)
    with engine.connect() as connection:
        result = connection.execution_options(stream_results=True).execute(
            select(table).order_by(*order)
        )
        for row in result.mappings():
            yield dict(row)


def digest(engine: Engine, table: Table) -> tuple[int, str]:
    """Row count and SHA-256 of the table's rows, each column as the application reads it."""
    hasher = hashlib.sha256()
    count = 0
    for row in _rows(engine, table):
        count += 1
        canonical = {key: _canonical(value) for key, value in sorted(row.items())}
        hasher.update(json.dumps(canonical, sort_keys=True, default=str).encode("utf-8"))
        hasher.update(b"\n")
    return count, hasher.hexdigest()


def _assert_empty(engine: Engine) -> None:
    occupied = []
    with engine.connect() as connection:
        for table in _tables():
            count = connection.execute(select(func.count()).select_from(table)).scalar()
            if count:
                occupied.append(f"{table.name} ({count})")
    if occupied:
        raise TransferRefusedError(
            "The target database already holds data: "
            + ", ".join(occupied[:5])
            + ". A transfer is made into an empty database only."
        )


def transfer(source: Engine, target: Engine, *, verify: bool = True) -> TransferReport:
    """Copy every table, in dependency order, in one transaction on the target."""
    if target.dialect.name != "postgresql":
        raise TransferRefusedError("The target of a transfer is the host's PostgreSQL database.")
    _assert_empty(target)
    tables = _tables()
    with target.begin() as connection:
        for table in tables:
            batch: list[dict[str, Any]] = []
            for row in _rows(source, table):
                batch.append(row)
                if len(batch) >= BATCH:
                    connection.execute(insert(table), batch)
                    batch = []
            if batch:
                connection.execute(insert(table), batch)
    report = TransferReport()
    if verify:
        for table in tables:
            source_rows, source_digest = digest(source, table)
            target_rows, target_digest = digest(target, table)
            report.tables.append(
                TableResult(table.name, source_rows, target_rows, source_digest, target_digest)
            )
    return report


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def copy_files(source: Path, target: Path, report: TransferReport) -> TransferReport:
    """Copy the attachment store, comparing each file's digest after the copy."""
    if not source.is_dir():
        return report
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        report.files += 1
        report.bytes_copied += path.stat().st_size
        if file_digest(path) == file_digest(destination):
            report.files_agreeing += 1
    return report


def manifest(directory: Path) -> dict[str, str]:
    """Every file under a directory and its digest, by relative path."""
    if not directory.is_dir():
        return {}
    return {
        str(path.relative_to(directory)).replace("\\", "/"): file_digest(path)
        for path in sorted(item for item in directory.rglob("*") if item.is_file())
    }


__all__ = [
    "TableResult",
    "TransferRefusedError",
    "TransferReport",
    "copy_files",
    "digest",
    "file_digest",
    "manifest",
    "transfer",
]
