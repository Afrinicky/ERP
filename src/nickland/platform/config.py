"""Application configuration.

Configuration is read from the environment and from an optional settings file in the
application data directory. Secrets are never held here as literals; see
``nickland.platform.secrets``.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_dir
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APPLICATION_NAME = "Nickland ERP"
APPLICATION_AUTHOR = "Nickland"


class DeploymentMode(StrEnum):
    """How this installation is deployed."""

    STANDALONE = "standalone"
    """Single computer. Local service, SQLite, no network required."""

    LAN_HOST = "lan_host"
    """Host computer. Owns the PostgreSQL connection, serves authenticated clients."""

    LAN_CLIENT = "lan_client"
    """Client workstation. Holds no database connection."""


class Environment(StrEnum):
    """Runtime environment. Governs logging verbosity and error disclosure."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


def default_data_directory() -> Path:
    """The per-user application data directory."""
    return Path(user_data_dir(APPLICATION_NAME, APPLICATION_AUTHOR, roaming=False))


class Settings(BaseSettings):
    """Runtime settings.

    Read from the environment with the ``NICKLAND_`` prefix.
    """

    model_config = SettingsConfigDict(
        env_prefix="NICKLAND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.PRODUCTION
    deployment_mode: DeploymentMode = DeploymentMode.STANDALONE

    data_directory: Path = Field(default_factory=default_data_directory)

    database_url: str | None = None
    """Explicit database URL. When absent, a SQLite file in the data directory is used."""

    service_host: str = "127.0.0.1"
    service_port: int = 0
    """0 selects an ephemeral port. The desktop host reads the actual port after binding."""

    session_idle_minutes: int = 30
    session_absolute_hours: int = 12

    password_minimum_length: int = 10
    lockout_threshold: int = 5
    lockout_minutes: int = 15

    backup_enabled: bool = True
    backup_retain_count: int = 14
    backup_interval_hours: int = 24
    """How often the host takes a scheduled backup. 0 turns scheduled backups off."""

    postgres_bin: Path | None = None
    """Where pg_dump and pg_restore are, when they are not on the path."""

    lan_port: int = 8443
    """Where the LAN host listens for clients, over TLS."""
    migration_database_url: str | None = None
    """The schema owner's URL, used only to upgrade the schema. The running service
    connects with ``database_url``, a role that can change rows but not tables."""

    host_url: str | None = None
    """A LAN client: the host it opens, for example ``https://erp-host:8443``."""
    host_fingerprint: str | None = None
    """A LAN client: the SHA-256 fingerprint the host's certificate must present."""

    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("data_directory")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return Path(os.path.expandvars(value)).expanduser()

    @property
    def attachment_directory(self) -> Path:
        """Managed attachment store. Never served directly; access is mediated."""
        return self.data_directory / "attachments"

    @property
    def backup_directory(self) -> Path:
        return self.data_directory / "backups"

    @property
    def tls_directory(self) -> Path:
        return self.data_directory / "tls"

    @property
    def log_directory(self) -> Path:
        return self.data_directory / "logs"

    @property
    def database_file(self) -> Path:
        return self.data_directory / "nickland.db"

    def resolved_database_url(self) -> str:
        """The database URL in force.

        A LAN client never resolves a database URL; it holds no connection.
        """
        if self.deployment_mode is DeploymentMode.LAN_CLIENT:
            raise RuntimeError("A LAN client does not connect to the database directly")
        if self.database_url:
            return self.database_url
        return f"sqlite+pysqlite:///{self.database_file}"

    @property
    def is_sqlite(self) -> bool:
        return self.resolved_database_url().startswith("sqlite")

    def lan_host_problems(self) -> list[str]:
        """What stands in the way of serving clients on the network."""
        if self.deployment_mode is not DeploymentMode.LAN_HOST:
            return ["The deployment mode is not lan_host."]
        problems = []
        if not self.database_url or not self.database_url.startswith("postgresql"):
            problems.append(
                "A LAN host stores its data in PostgreSQL: set NICKLAND_DATABASE_URL to a "
                "postgresql+psycopg:// URL."
            )
        if not (self.tls_directory / "server.pem").is_file():
            problems.append("No TLS certificate has been issued: run `nickland-host certificates`.")
        return problems

    def ensure_directories(self) -> None:
        """Create the data directories. Idempotent."""
        for directory in (
            self.data_directory,
            self.attachment_directory,
            self.backup_directory,
            self.log_directory,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, read once."""
    return Settings()


def reset_settings_cache() -> None:
    """Discard the cached settings. For tests only."""
    get_settings.cache_clear()
