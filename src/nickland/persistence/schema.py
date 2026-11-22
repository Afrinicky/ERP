"""The schema brought to the current revision when the application starts. PRD §9.5.

A newly installed copy has no database; an upgraded copy has a database a revision or more
behind the code. Either way the standalone application applies the migrations before it
serves anything. Before changing an existing database it copies it aside, so an upgrade
that goes wrong can be undone by putting the copy back.

The LAN host does not upgrade itself: its schema is changed by its owner role with
``nickland-host upgrade``, and the host refuses to serve a schema that is behind.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from nickland.platform.config import Settings

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def _config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def head_revision() -> str | None:
    return ScriptDirectory.from_config(_config("sqlite://")).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def is_current(engine: Engine) -> bool:
    return current_revision(engine) == head_revision()


def _copy_aside(settings: Settings, revision: str | None) -> Path | None:
    """A consistent copy of the SQLite database, taken before the schema changes."""
    source = settings.database_file
    if not settings.is_sqlite or not source.exists():
        return None
    settings.backup_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    destination = settings.backup_directory / f"before-upgrade-{revision or 'empty'}-{stamp}.db"
    reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        writer = sqlite3.connect(destination)
        try:
            reader.backup(writer)
        finally:
            writer.close()
    finally:
        reader.close()
    return destination


def ensure_current(settings: Settings, engine: Engine) -> tuple[str | None, str | None]:
    """Apply any outstanding migrations. Returns the revisions before and after."""
    before = current_revision(engine)
    head = head_revision()
    if before == head:
        return before, head
    if before is not None:
        _copy_aside(settings, before)
    command.upgrade(_config(engine.url.render_as_string(hide_password=False)), "head")
    return before, current_revision(engine)


def restore_copy(copy: Path, settings: Settings) -> None:
    """Put a pre-upgrade copy back, for use while the application is stopped."""
    shutil.copy2(copy, settings.database_file)


__all__ = [
    "MIGRATIONS",
    "current_revision",
    "ensure_current",
    "head_revision",
    "is_current",
    "restore_copy",
]
