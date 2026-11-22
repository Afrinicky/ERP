"""PostgreSQL roles for the LAN host. PRD §9.6: least-privilege database credentials.

Two roles, so that the service that answers clients cannot change the shape of the
database or rewrite its history:

* the **owner** owns the schema, runs migrations and creates the restore drill's scratch
  database. Its password is used only by those steps, at the host, by an administrator;
* the **service** role is what the running service connects as. It may read and write
  rows, and nothing else: it cannot create, alter or drop a table, and it cannot alter or
  delete an audit entry.

PostgreSQL itself listens on the host's loopback address only, so no client machine can
reach it at all; ``hba_lines`` gives the matching ``pg_hba.conf`` entries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

APPEND_ONLY_TABLES = ("audit_events",)
"""Tables the service may add to but never alter or delete from."""

_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _identifier(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"{name!r} is not a plain lower-case PostgreSQL name.")
    return name


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@dataclass(frozen=True, slots=True)
class RolePlan:
    database: str
    owner: str
    service: str
    schema: str = "public"

    def owner_statements(self, password: str) -> list[str]:
        owner = _identifier(self.owner)
        return [
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{owner}') "
            f"THEN CREATE ROLE {owner} LOGIN; END IF; END $$",
            f"ALTER ROLE {owner} WITH LOGIN CREATEDB PASSWORD {_literal(password)}",
            f"ALTER DATABASE {_identifier(self.database)} OWNER TO {owner}",
            f"ALTER SCHEMA {_identifier(self.schema)} OWNER TO {owner}",
        ]

    def service_statements(self, password: str) -> list[str]:
        owner, service = _identifier(self.owner), _identifier(self.service)
        schema, database = _identifier(self.schema), _identifier(self.database)
        statements = [
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{service}') "
            f"THEN CREATE ROLE {service} LOGIN; END IF; END $$",
            f"ALTER ROLE {service} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            f"PASSWORD {_literal(password)}",
            f"REVOKE ALL ON DATABASE {database} FROM PUBLIC",
            f"GRANT CONNECT, TEMPORARY ON DATABASE {database} TO {service}",
            f"REVOKE CREATE ON SCHEMA {schema} FROM PUBLIC",
            f"GRANT USAGE ON SCHEMA {schema} TO {service}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {service}",
            f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA {schema} TO {service}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {schema} "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {service}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {schema} "
            f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {service}",
        ]
        statements += [
            f"REVOKE UPDATE, DELETE, TRUNCATE ON {schema}.{_identifier(table)} FROM {service}"
            for table in APPEND_ONLY_TABLES
        ]
        return statements

    def transfer_statements(self) -> list[str]:
        """Give the owner every table an earlier installer created as someone else."""
        return [
            f"DO $$ DECLARE item record; BEGIN FOR item IN SELECT tablename FROM pg_tables "
            f"WHERE schemaname = '{_identifier(self.schema)}' LOOP EXECUTE format("
            f"'ALTER TABLE %I.%I OWNER TO {_identifier(self.owner)}', "
            f"'{_identifier(self.schema)}', item.tablename); END LOOP; END $$",
        ]

    def hba_lines(self) -> list[str]:
        return [
            "# Nickland ERP: the database answers the host itself and nothing else.",
            f"host  {self.database}  {self.service}  127.0.0.1/32  scram-sha-256",
            f"host  {self.database}  {self.owner}    127.0.0.1/32  scram-sha-256",
            f"host  {self.database}  {self.service}  ::1/128       scram-sha-256",
            f"host  {self.database}  {self.owner}    ::1/128       scram-sha-256",
            "# postgresql.conf: listen_addresses = 'localhost'",
        ]


__all__ = ["APPEND_ONLY_TABLES", "RolePlan"]
