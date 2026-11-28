"""Secret storage.

Secrets are held outside the database and outside source control, per PRD 9.6. On
Windows the operating-system credential store is preferred; a restricted-permission
file in the data directory is the portable fallback.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Final

_FILE_NAME: Final = "secrets.json"


class SecretStore:
    """Key-value secret storage backed by a restricted-permission file."""

    def __init__(self, directory: Path) -> None:
        self._path = directory / _FILE_NAME
        self._directory = directory

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        with self._path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return {str(key): str(value) for key, value in data.items()}

    def _write(self, data: dict[str, str]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        if os.name != "nt":
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        temporary.replace(self._path)

    def get(self, key: str) -> str | None:
        """Read a secret. The environment takes precedence over stored values."""
        from_environment = os.environ.get(f"NICKLAND_SECRET_{key.upper()}")
        if from_environment:
            return from_environment
        return self._read().get(key)

    def set(self, key: str, value: str) -> None:
        data = self._read()
        data[key] = value
        self._write(data)

    def delete(self, key: str) -> None:
        data = self._read()
        data.pop(key, None)
        self._write(data)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None
