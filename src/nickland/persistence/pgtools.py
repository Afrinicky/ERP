"""PostgreSQL's own dump and restore tools, driven from the host. PRD §9.5.

The password travels in the child process's environment, never on its command line, so
it is not visible in a process list.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from pathlib import Path

from sqlalchemy import URL, make_url

SEARCH = (
    "/usr/lib/postgresql/*/bin",
    "/usr/local/pgsql/bin",
    "/opt/homebrew/opt/postgresql@*/bin",
    r"C:\Program Files\PostgreSQL\*\bin",
)


class PostgresToolError(RuntimeError):
    pass


def tool(name: str, directory: Path | None = None) -> str:
    """The path of ``pg_dump``, ``pg_restore`` and so on: configured, on the path, or in
    the usual installation directories, newest first."""
    candidates: list[str] = []
    if directory is not None:
        candidates.append(str(directory / name))
    found = shutil.which(name)
    if found:
        candidates.append(found)
    for pattern in SEARCH:
        for folder in sorted(glob.glob(pattern), reverse=True):
            candidates.extend([str(Path(folder) / name), str(Path(folder) / f"{name}.exe")])
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise PostgresToolError(
        f"{name} was not found. Install the PostgreSQL client tools on the host, or set "
        "NICKLAND_POSTGRES_BIN to their folder."
    )


def environment(url: str | URL, *, database: str | None = None) -> dict[str, str]:
    parsed = make_url(url) if isinstance(url, str) else url
    values = dict(os.environ)
    values.update(
        {
            "PGHOST": parsed.host or "localhost",
            "PGPORT": str(parsed.port or 5432),
            "PGUSER": parsed.username or "",
            "PGDATABASE": database or parsed.database or "",
        }
    )
    if parsed.password:
        values["PGPASSWORD"] = str(parsed.password)
    return values


def run(arguments: list[str], url: str | URL, *, database: str | None = None) -> str:
    completed = subprocess.run(
        arguments,
        env=environment(url, database=database),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise PostgresToolError(
            f"{Path(arguments[0]).name} failed: {completed.stderr.strip()[:500]}"
        )
    return completed.stdout


def schema_of(url: str | URL) -> str | None:
    """The schema a URL confines its connections to with ``options=-csearch_path=…``."""
    parsed = make_url(url) if isinstance(url, str) else url
    options = parsed.query.get("options")
    text = options if isinstance(options, str) else " ".join(options or ())
    for part in text.split():
        if part.startswith("-csearch_path="):
            return part.split("=", 1)[1].split(",")[0] or None
    return None


def dump(
    url: str | URL,
    destination: Path,
    *,
    snapshot: str | None = None,
    directory: Path | None = None,
    schema: str | None = None,
) -> None:
    """A custom-format dump: compressed, and restorable table by table."""
    arguments = [
        tool("pg_dump", directory),
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        f"--file={destination}",
    ]
    if snapshot:
        arguments.append(f"--snapshot={snapshot}")
    if schema:
        arguments.append(f"--schema={schema}")
    run(arguments, url)


def restore(url: str | URL, source: Path, database: str, *, directory: Path | None = None) -> None:
    run(
        [
            tool("pg_restore", directory),
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            f"--dbname={database}",
            str(source),
        ],
        url,
        database=database,
    )


def list_contents(source: Path, *, directory: Path | None = None) -> int:
    """How many objects the dump's table of contents lists. Fails on a damaged file."""
    output = subprocess.run(
        [tool("pg_restore", directory), "--list", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    if output.returncode != 0:
        raise PostgresToolError(f"The dump could not be read: {output.stderr.strip()[:300]}")
    return sum(1 for line in output.stdout.splitlines() if line and not line.startswith(";"))


__all__ = [
    "PostgresToolError",
    "dump",
    "environment",
    "list_contents",
    "restore",
    "run",
    "schema_of",
    "tool",
]
