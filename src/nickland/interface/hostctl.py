"""``nickland-host``: install, run and look after the LAN host. PRD §9.4, §9.5, §9.6.

    nickland-host certificates [--name erp-host] [--address 192.168.1.20]
    nickland-host roles --admin-url URL --owner-password-env VAR --service-password-env VAR
    nickland-host upgrade
    nickland-host transfer --from C:\\path\\to\\nickland.db [--attachments DIR]
    nickland-host serve
    nickland-host service --platform windows|linux
    nickland-host backup
    nickland-host drill [--archive NAME]
    nickland-host status

Every command reads the same settings as the service (``NICKLAND_*``), so the host is
configured once, in its environment or ``.env``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, make_url, select, text

from nickland.platform.config import DeploymentMode, Settings, get_settings

SERVICE_NAME = "NicklandERPHost"


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _business_name(settings: Settings) -> str:
    try:
        from nickland.persistence.models import Business

        engine = create_engine(settings.resolved_database_url())
        with engine.connect() as connection:
            name = connection.execute(select(Business.trading_name, Business.name)).first()
        engine.dispose()
        if name:
            return str(name[0] or name[1])
    except Exception:  # the database may not exist yet
        pass
    return "Nickland ERP"


# -- certificates ---------------------------------------------------------------------


def certificates(settings: Settings, arguments: argparse.Namespace) -> int:
    from nickland.platform.tls import provision

    status = provision(
        settings.tls_directory,
        business=_business_name(settings),
        names=arguments.name or [],
        addresses=arguments.address or [],
    )
    print("Server certificate issued.")
    print(f"  Names:       {', '.join(status.names)}")
    print(f"  Addresses:   {', '.join(status.addresses)}")
    print(f"  Valid until: {status.not_after:%d %B %Y}")
    print(f"  Fingerprint: {status.fingerprint}")
    print()
    print("On each client, set NICKLAND_HOST_FINGERPRINT to this fingerprint, or install")
    print(f"{settings.tls_directory / 'authority.pem'} as a trusted root.")
    return 0


# -- database roles -------------------------------------------------------------------


def roles(settings: Settings, arguments: argparse.Namespace) -> int:
    from nickland.persistence.roles import RolePlan

    database = arguments.database or (
        make_url(settings.database_url).database if settings.database_url else None
    )
    if not database:
        return _fail("Name the database with --database, or set NICKLAND_DATABASE_URL.")
    plan = RolePlan(database=database, owner=arguments.owner, service=arguments.service)
    owner_password = os.environ.get(arguments.owner_password_env or "", "")
    service_password = os.environ.get(arguments.service_password_env or "", "")
    if not owner_password or not service_password:
        return _fail(
            "Put the two passwords in environment variables and name them with "
            "--owner-password-env and --service-password-env; they are never typed on the "
            "command line."
        )
    statements = (
        plan.owner_statements(owner_password)
        + plan.transfer_statements()
        + plan.service_statements(service_password)
    )
    if not arguments.apply:
        for statement in statements:
            print(
                statement.replace(owner_password, "********").replace(service_password, "********")
                + ";"
            )
        print()
        print("\n".join(plan.hba_lines()))
        print("\nRun again with --apply and --admin-url to carry these out.")
        return 0
    if not arguments.admin_url:
        return _fail("--apply needs --admin-url: a superuser connection to the database.")
    engine = create_engine(arguments.admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        for statement in statements:
            connection.execute(text(statement))
    engine.dispose()
    print(f"Roles {plan.owner} (schema owner) and {plan.service} (service) are in place.")
    print("\n".join(plan.hba_lines()))
    return 0


# -- schema ---------------------------------------------------------------------------


def upgrade(settings: Settings, _: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    url = settings.migration_database_url or settings.database_url
    if not url:
        return _fail("Set NICKLAND_MIGRATION_DATABASE_URL to the schema owner's URL.")
    root = Path(__file__).resolve().parents[1] / "persistence" / "migrations"
    config = Config()
    config.set_main_option("script_location", str(root))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    print("The schema is at the current revision.")
    return 0


# -- transfer -------------------------------------------------------------------------


def transfer(settings: Settings, arguments: argparse.Namespace) -> int:
    from nickland.persistence.transfer import TransferRefusedError, copy_files
    from nickland.persistence.transfer import transfer as copy_database

    source_file = Path(arguments.source).expanduser()
    if not source_file.is_file():
        return _fail(f"No standalone database at {source_file}.")
    url = settings.migration_database_url or settings.database_url
    if not url or not url.startswith("postgresql"):
        return _fail("Set NICKLAND_DATABASE_URL (or the migration URL) to the host's database.")
    source = create_engine(f"sqlite+pysqlite:///{source_file}")
    target = create_engine(url)
    try:
        report = copy_database(source, target)
    except TransferRefusedError as refusal:
        return _fail(str(refusal))
    finally:
        source.dispose()
        target.dispose()
    attachments = (
        Path(arguments.attachments)
        if arguments.attachments
        else (source_file.parent / "attachments")
    )
    copy_files(attachments, settings.attachment_directory, report)
    print(f"Copied {report.rows} rows in {len(report.tables)} tables and {report.files} files.")
    if not report.agrees:
        print("These did not arrive intact: " + ", ".join(report.disagreements), file=sys.stderr)
        return 1
    print("Every table and file was verified against the standalone copy.")
    return 0


# -- serving --------------------------------------------------------------------------


def serve(settings: Settings, arguments: argparse.Namespace) -> int:
    import uvicorn

    from nickland.interface.application import create_application
    from nickland.interface.static import interface_root
    from nickland.platform.tls import CertificateSet

    problems = settings.lan_host_problems()
    if problems:
        return _fail("The host cannot serve yet:\n  " + "\n  ".join(problems))
    files = CertificateSet(settings.tls_directory)
    application = create_application(settings, static_root=interface_root())
    uvicorn.run(
        application,
        host=arguments.host or "0.0.0.0",
        port=arguments.port or settings.lan_port,
        ssl_certfile=str(files.certificate),
        ssl_keyfile=str(files.key),
        log_config=None,
        access_log=False,
        timeout_graceful_shutdown=20,
        ws="none",
    )
    return 0


def service(settings: Settings, arguments: argparse.Namespace) -> int:
    """The service definition that keeps the host running and restarts it if it stops."""
    executable = Path(sys.executable)
    if arguments.platform == "linux":
        print(
            "\n".join(
                [
                    "[Unit]",
                    "Description=Nickland ERP host service",
                    "After=network-online.target postgresql.service",
                    "Requires=postgresql.service",
                    "",
                    "[Service]",
                    f"ExecStart={executable} -m nickland.interface.hostctl serve",
                    f"WorkingDirectory={settings.data_directory}",
                    "Environment=NICKLAND_DEPLOYMENT_MODE=lan_host",
                    "Restart=always",
                    "RestartSec=5",
                    "TimeoutStopSec=30",
                    "",
                    "[Install]",
                    "WantedBy=multi-user.target",
                ]
            )
        )
        return 0
    print(
        "\n".join(
            [
                "<service>",
                f"  <id>{SERVICE_NAME}</id>",
                "  <name>Nickland ERP host</name>",
                "  <description>Serves Nickland ERP to the workstations on this network."
                "</description>",
                f"  <executable>{executable}</executable>",
                "  <arguments>-m nickland.interface.hostctl serve</arguments>",
                f"  <workingdirectory>{settings.data_directory}</workingdirectory>",
                '  <env name="NICKLAND_DEPLOYMENT_MODE" value="lan_host"/>',
                "  <depend>postgresql-x64-16</depend>",
                "  <startmode>Automatic</startmode>",
                '  <onfailure action="restart" delay="5 sec"/>',
                '  <onfailure action="restart" delay="30 sec"/>',
                "  <stoptimeout>30 sec</stoptimeout>",
                f"  <logpath>{settings.log_directory}</logpath>",
                '  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold>'
                "<keepFiles>8</keepFiles></log>",
                "</service>",
            ]
        )
    )
    print(
        "\n<!-- Save as NicklandERPHost.xml beside WinSW renamed NicklandERPHost.exe, then run "
        "`NicklandERPHost.exe install` as an administrator. -->",
        file=sys.stderr,
    )
    return 0


# -- operations -----------------------------------------------------------------------


def _system(settings: Settings):  # type: ignore[no-untyped-def]
    from nickland.application.context import SystemActor
    from nickland.persistence.engine import create_database_engine, create_session_factory
    from nickland.persistence.models import Business

    engine = create_database_engine(settings)
    session = create_session_factory(engine)()
    business = session.scalar(select(Business.id))
    if business is None:
        raise SystemExit("The business has not been set up.")
    return engine, session, SystemActor(business_id=business, reason="Host administration")


def backup(settings: Settings, _: argparse.Namespace) -> int:
    from nickland.application.services.backup import BackupService

    engine, session, actor = _system(settings)
    try:
        outcome = BackupService(session, engine, settings).run(actor, trigger="manual")
        session.commit()
    finally:
        session.close()
        engine.dispose()
    print(f"{outcome.path.name}: {outcome.size_bytes} bytes. {outcome.note}")
    return 0 if outcome.verified else 1


def drill(settings: Settings, arguments: argparse.Namespace) -> int:
    from nickland.application.services.backup import BackupService

    engine, session, actor = _system(settings)
    try:
        report = BackupService(session, engine, settings).drill(actor, arguments.archive)
        session.commit()
    finally:
        session.close()
        engine.dispose()
    print(
        f"{report.archive}: {report.tables} tables, {report.rows} rows and {report.files} "
        f"attachments restored and compared in {report.seconds:.1f} s."
    )
    if not report.reproduced:
        print(
            "Mismatched: " + ", ".join(report.table_mismatches + report.file_mismatches),
            file=sys.stderr,
        )
        return 1
    print("The backup reproduces the dataset and its attachments.")
    return 0


def status(settings: Settings, _: argparse.Namespace) -> int:
    from nickland.platform.tls import inspect

    certificate = inspect(settings.tls_directory)
    report = {
        "deployment_mode": settings.deployment_mode.value,
        "problems": settings.lan_host_problems(),
        "certificate": {
            "fingerprint": certificate.fingerprint,
            "valid_until": certificate.not_after.isoformat(),
            "names": certificate.names,
            "addresses": certificate.addresses,
        }
        if certificate
        else None,
    }
    print(json.dumps(report, indent=2))
    return 0 if not report["problems"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nickland-host", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    issue = commands.add_parser("certificates", help="Issue the host's TLS certificate")
    issue.add_argument("--name", action="append", help="A name clients use for the host")
    issue.add_argument("--address", action="append", help="An address clients use")

    grant = commands.add_parser("roles", help="Create the owner and service database roles")
    grant.add_argument("--database")
    grant.add_argument("--owner", default="nickland_owner")
    grant.add_argument("--service", default="nickland_service")
    grant.add_argument("--owner-password-env")
    grant.add_argument("--service-password-env")
    grant.add_argument("--admin-url")
    grant.add_argument("--apply", action="store_true")

    commands.add_parser("upgrade", help="Bring the schema to the current revision")

    move = commands.add_parser("transfer", help="Move a standalone database onto the host")
    move.add_argument("--from", dest="source", required=True)
    move.add_argument("--attachments")

    run = commands.add_parser("serve", help="Serve clients over TLS")
    run.add_argument("--host")
    run.add_argument("--port", type=int)

    unit = commands.add_parser("service", help="Print the service definition")
    unit.add_argument("--platform", choices=("windows", "linux"), default="windows")

    commands.add_parser("backup", help="Take and verify a backup now")
    practice = commands.add_parser("drill", help="Restore a backup aside and compare it")
    practice.add_argument("--archive")
    commands.add_parser("status", help="Report what the host needs")

    arguments = parser.parse_args(argv)
    settings = get_settings()
    if arguments.command in ("serve", "status") and (
        settings.deployment_mode is not DeploymentMode.LAN_HOST
    ):
        return _fail("Set NICKLAND_DEPLOYMENT_MODE=lan_host on the host.")
    settings.ensure_directories()
    handler = {
        "certificates": certificates,
        "roles": roles,
        "upgrade": upgrade,
        "transfer": transfer,
        "serve": serve,
        "service": service,
        "backup": backup,
        "drill": drill,
        "status": status,
    }[arguments.command]
    return handler(settings, arguments)


if __name__ == "__main__":
    raise SystemExit(main())
