"""The LAN host: certificates, the service that owns the database, least-privilege roles,
the verified transfer from a standalone deployment, backup and the restore drill.
PRD §9.4, §9.5, §9.6, AC-12."""

from __future__ import annotations

import socket
import ssl
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from nickland.application.context import Actor, SystemActor
from nickland.application.services.backup import BackupService
from nickland.application.services.health import HealthService
from nickland.desktop.host import verify_host
from nickland.interface.application import build_runtime, create_application
from nickland.interface.dependencies import SESSION_HEADER
from nickland.persistence.base import Base
from nickland.persistence.models import AuditEvent
from nickland.persistence.roles import RolePlan
from nickland.persistence.transfer import TransferRefusedError, transfer
from nickland.platform.clock import FixedClock
from nickland.platform.config import DeploymentMode, Environment, Settings
from nickland.platform.tls import inspect, peer_fingerprint, provision
from tests.conftest import postgres_url

needs_postgres = pytest.mark.skipif(postgres_url() is None, reason="needs PostgreSQL")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def certificates(tmp_path: Path) -> Path:
    directory = tmp_path / "tls"
    provision(
        directory,
        business="Nicholas Nickland Enterprise",
        names=["erp-host"],
        addresses=["192.168.1.20"],
    )
    return directory


class TestCertificates:
    def test_the_host_certificate_is_signed_by_its_own_authority(self, certificates: Path) -> None:
        status = inspect(certificates)
        assert status is not None
        assert {"erp-host", "localhost"} <= set(status.names)
        assert "192.168.1.20" in status.addresses
        context = ssl.create_default_context(cafile=str(certificates / "authority.pem"))
        store = context.get_ca_certs()
        assert store and "local authority" in str(store[0]["subject"])

    def test_reissuing_keeps_the_authority_clients_trust(self, certificates: Path) -> None:
        before = inspect(certificates)
        provision(certificates, business="Nicholas Nickland Enterprise", names=["erp-host2"])
        after = inspect(certificates)
        assert before is not None and after is not None
        assert after.authority_fingerprint == before.authority_fingerprint
        assert after.fingerprint != before.fingerprint
        assert "erp-host2" in after.names


@pytest.fixture
def tls_server(settings: Settings, engine: Engine, certificates: Path) -> Iterator[tuple[str, int]]:
    """The application served over TLS on a free port, as the LAN host serves it."""
    runtime = build_runtime(settings)
    application = create_application(runtime=runtime)
    port = free_port()
    config = uvicorn.Config(
        application,
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
        ws="none",
        ssl_certfile=str(certificates / "server.pem"),
        ssl_keyfile=str(certificates / "server.key"),
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield "127.0.0.1", port
    server.should_exit = True
    thread.join(timeout=10)


class TestTransport:
    def test_clients_reach_the_host_over_tls_and_can_pin_it(
        self, tls_server: tuple[str, int], certificates: Path
    ) -> None:
        import httpx

        host, port = tls_server
        response = httpx.get(
            f"https://localhost:{port}/api/v1/health",
            verify=ssl.create_default_context(cafile=str(certificates / "authority.pem")),
        )
        assert response.status_code == 200
        status = inspect(certificates)
        assert status is not None
        assert peer_fingerprint(host, port) == status.fingerprint

    def test_a_client_refuses_a_host_presenting_another_certificate(
        self, tls_server: tuple[str, int], tmp_path: Path
    ) -> None:
        _, port = tls_server
        client = Settings(
            deployment_mode=DeploymentMode.LAN_CLIENT,
            data_directory=tmp_path / "client",
            host_url=f"https://localhost:{port}",
            host_fingerprint="00:" * 31 + "00",
        )
        with pytest.raises(RuntimeError, match="not the one this workstation trusts"):
            verify_host(client)
        plain = client.model_copy(update={"host_url": f"http://localhost:{port}"})
        with pytest.raises(RuntimeError, match="https only"):
            verify_host(plain)


class TestHostConfiguration:
    def test_a_lan_host_needs_postgresql_and_a_certificate(self, tmp_path: Path) -> None:
        host = Settings(
            deployment_mode=DeploymentMode.LAN_HOST,
            data_directory=tmp_path,
            environment=Environment.TEST,
        )
        problems = host.lan_host_problems()
        assert any("PostgreSQL" in item for item in problems)
        assert any("certificate" in item for item in problems)

    def test_a_lan_client_holds_no_database_connection(self, tmp_path: Path) -> None:
        client = Settings(deployment_mode=DeploymentMode.LAN_CLIENT, data_directory=tmp_path)
        with pytest.raises(RuntimeError, match="does not connect"):
            client.resolved_database_url()


@pytest.fixture
def lan_client(
    db: Session, settings: Settings, engine: Engine, admin: Actor
) -> Iterator[TestClient]:
    """The service as a LAN host runs it: no loopback token, sessions only."""
    db.commit()
    lan = settings.model_copy(update={"deployment_mode": DeploymentMode.LAN_HOST})
    runtime = build_runtime(lan)
    with TestClient(create_application(runtime=runtime)) as client:
        yield client


OPEN_WRITES = {
    ("POST", "/api/v1/setup"),
    ("POST", "/api/v1/session"),
}
"""The two writes a client makes before it has a session: the first-run setup, refused once
the business exists, and signing in."""


class TestOnlyTheAuthenticatedServiceWrites:
    def test_every_write_is_refused_without_a_session(self, lan_client: TestClient) -> None:
        schema = lan_client.get("/api/openapi.json").json()
        refused = 0
        for path, operations in schema["paths"].items():
            for method in operations:
                verb = method.upper()
                if verb not in {"POST", "PUT", "PATCH", "DELETE"}:
                    continue
                concrete = path
                for name in [part[1:-1] for part in path.split("/") if part.startswith("{")]:
                    concrete = concrete.replace("{" + name + "}", str(uuid.uuid4()))
                response = lan_client.request(verb, concrete, json={})
                if (verb, path) in OPEN_WRITES:
                    assert response.status_code in {401, 409, 422}, (verb, path)
                    continue
                assert response.status_code in {401, 403}, (verb, path, response.status_code)
                refused += 1
        assert refused > 250

    def test_responses_to_the_network_carry_the_security_headers(
        self, lan_client: TestClient
    ) -> None:
        response = lan_client.get("/api/v1/health")
        assert response.headers["strict-transport-security"].startswith("max-age=")
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cache-control"] == "no-store"

    def test_a_signed_in_client_writes_through_the_service(self, lan_client: TestClient) -> None:
        signed = lan_client.post(
            "/api/v1/session", json={"username": "proprietor", "password": "Provision9Admin"}
        )
        assert signed.status_code == 200, signed.text
        lan_client.headers[SESSION_HEADER] = signed.json()["token"]
        made = lan_client.post("/api/v1/parties", json={"name": "Korle Bu", "is_customer": True})
        assert made.status_code == 201, made.text


@needs_postgres
class TestDatabaseRoles:
    @pytest.fixture
    def database(self) -> Iterator[tuple[str, str]]:
        base = postgres_url()
        assert base is not None
        name = f"roles_{uuid.uuid4().hex[:8]}"
        admin = create_engine(base, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        yield base, name
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            for role in ("nk_owner_t", "nk_service_t"):
                connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()

    def test_the_service_role_writes_rows_but_cannot_reshape_or_rewrite_history(
        self, database: tuple[str, str]
    ) -> None:
        base, name = database
        url = make_url(base).set(database=name)
        plan = RolePlan(database=name, owner="nk_owner_t", service="nk_service_t")
        admin = create_engine(url, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            for statement in plan.owner_statements("Owner9Password"):
                connection.execute(text(statement))
        owner = create_engine(
            url.set(username="nk_owner_t", password="Owner9Password", host="localhost")
        )
        Base.metadata.create_all(owner)
        owner.dispose()
        with admin.connect() as connection:
            for statement in plan.service_statements("Service9Password"):
                connection.execute(text(statement))
        admin.dispose()
        service = create_engine(
            url.set(username="nk_service_t", password="Service9Password", host="localhost")
        )
        try:
            with service.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO audit_events (id, business_id, occurred_at, action, "
                        "entity_type) VALUES (:id, :business, now(), 'test', 'test')"
                    ),
                    {"id": uuid.uuid4(), "business": uuid.uuid4()},
                )
                assert connection.execute(select(AuditEvent.id)).first() is not None
            for forbidden in (
                "CREATE TABLE intruder (id int)",
                "DROP TABLE parties",
                "ALTER TABLE parties ADD COLUMN x int",
                "UPDATE audit_events SET action = 'altered'",
                "DELETE FROM audit_events",
            ):
                with pytest.raises(DBAPIError), service.begin() as connection:
                    connection.execute(text(forbidden))
        finally:
            service.dispose()


@pytest.fixture
def standalone(tmp_path: Path, clock: FixedClock) -> Iterator[Path]:
    """A provisioned standalone database with a little trading in it."""
    from nickland.application.services.setup import SetupRequest, SetupService
    from nickland.persistence.engine import create_session_factory

    path = tmp_path / "standalone" / "nickland.db"
    path.parent.mkdir()
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    Base.metadata.create_all(engine)
    session = create_session_factory(engine)()
    SetupService(session, clock=clock).provision(
        SetupRequest(
            business_name="Nicholas Nickland Enterprise",
            administrator_username="proprietor",
            administrator_full_name="N. Nickland",
            administrator_password="Provision9Admin",
            theme_preset="nickland",
        )
    )
    session.commit()
    session.close()
    engine.dispose()
    yield path


@needs_postgres
class TestTransfer:
    def test_a_standalone_database_arrives_verified(
        self, standalone: Path, settings: Settings, engine: Engine, engine_name: str
    ) -> None:
        if engine_name != "postgresql":
            pytest.skip("the transfer target is PostgreSQL")
        source = create_engine(f"sqlite+pysqlite:///{standalone}")
        try:
            report = transfer(source, engine)
        finally:
            source.dispose()
        assert report.agrees, report.disagreements
        assert report.rows > 200
        with pytest.raises(TransferRefusedError, match="already holds data"):
            transfer(create_engine(f"sqlite+pysqlite:///{standalone}"), engine)


class TestBackupAndDrill:
    def test_a_backup_is_restored_aside_and_reproduces_everything(
        self, db: Session, engine: Engine, settings: Settings, admin: Actor, clock: FixedClock
    ) -> None:
        folder = settings.attachment_directory / "3f"
        folder.mkdir(parents=True)
        (folder / "receipt.pdf").write_bytes(b"%PDF-1.4 proof of payment" * 50)
        db.commit()
        service = BackupService(db, engine, settings, clock=clock)
        outcome = service.run(admin)
        db.commit()
        assert outcome.verified, outcome.note
        report = service.drill(admin, outcome.path.name)
        db.commit()
        assert report.reproduced, (report.table_mismatches, report.file_mismatches)
        assert report.files == 1 and report.rows > 200

    def test_an_altered_attachment_in_the_archive_fails_verification(
        self, db: Session, engine: Engine, settings: Settings, admin: Actor, clock: FixedClock
    ) -> None:
        import zipfile

        (settings.attachment_directory / "note.txt").write_text("original")
        db.commit()
        service = BackupService(db, engine, settings, clock=clock)
        outcome = service.run(admin)
        db.commit()
        tampered = outcome.path.with_name("tampered.zip")
        with zipfile.ZipFile(outcome.path) as source, zipfile.ZipFile(tampered, "w") as target:
            for item in source.infolist():
                data = source.read(item)
                target.writestr(item, b"altered" if item.filename.endswith("note.txt") else data)
        verified, note = service._verify(tampered)
        assert not verified and "altered" in note


class TestHealth:
    def test_the_host_reports_its_certificate_and_workers(
        self,
        db: Session,
        engine: Engine,
        settings: Settings,
        certificates: Path,
        clock: FixedClock,
    ) -> None:
        from nickland.application.workers import beat, register, reset

        host = settings.model_copy(
            update={
                "deployment_mode": DeploymentMode.LAN_HOST,
                "data_directory": certificates.parent,
            }
        )
        reset()
        register("Scheduled reports", 60)
        beat("Scheduled reports", clock.now())
        report = HealthService(db, engine, host, clock=clock).report()
        checks = {check.name: check for check in report.checks}
        assert checks["Encrypted transport"].state.value == "ok"
        assert checks["Background work"].state.value == "ok"
        reset()

    def test_a_system_actor_takes_the_scheduled_backup(
        self, db: Session, engine: Engine, settings: Settings, admin: Actor, clock: FixedClock
    ) -> None:
        assert admin.business_id is not None
        service = BackupService(db, engine, settings, clock=clock)
        outcome = service.run(
            SystemActor(business_id=admin.business_id, reason="Scheduled"), trigger="scheduled"
        )
        assert outcome.verified
