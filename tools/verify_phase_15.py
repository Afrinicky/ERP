#!/usr/bin/env python3
"""Verify each Phase 15 exit criterion from docs/00-build-plan.md.

Stands up a real LAN host against PostgreSQL, as an installer would: a fresh database, the
schema owner and the least-privilege service role, the schema upgraded by migration, a
certificate, and ``nickland-host serve`` in its own process over TLS. Then ten users work
it over HTTPS, the host is killed outright partway through and restarted, and the record
is checked; a backup is taken and drilled; and the whole automated suite is run against
both engines.

    .venv/bin/python tools/verify_phase_15.py                 # everything (about 35 minutes)
    .venv/bin/python tools/verify_phase_15.py --skip-suite    # the host criteria only

Needs a PostgreSQL superuser URL in NICKLAND_TEST_POSTGRES_URL (to create the scratch
database and roles) and the PostgreSQL client tools.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402
from sqlalchemy import MetaData, create_engine, func, make_url, select, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402

from nickland.application.context import SystemActor  # noqa: E402
from nickland.application.services.catalogue import CatalogueService, ProductDraft  # noqa: E402
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.integrity import IntegrityService  # noqa: E402
from nickland.application.services.inventory import (  # noqa: E402
    InventoryService,
    OpeningDraft,
    StockLineDraft,
)
from nickland.application.services.parties import PartyDraft, PartyService  # noqa: E402
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.application.services.units import UnitService  # noqa: E402
from nickland.persistence.engine import create_session_factory  # noqa: E402
from nickland.persistence.inventory_models import StockMovement, StockPosition  # noqa: E402
from nickland.persistence.models import Business, Warehouse  # noqa: E402
from nickland.persistence.roles import RolePlan  # noqa: E402

SUPER = os.environ.get(
    "NICKLAND_TEST_POSTGRES_URL", "postgresql+psycopg://postgres@localhost:55432/postgres"
)
PASSWORD = "Provision9Admin"
STAFF = "Counter9Staff"
USERS = 10
ROUNDS = 8
D = Decimal
Outcome = tuple[str, bool | None, str]
"""A criterion's label, whether it is met (None: not run here), and what was found."""


class Host:
    """A LAN host in a process of its own, as the service manager runs it."""

    def __init__(self, data: Path, service_url: str, owner_url: str, port: int) -> None:
        self.port = port
        self.environment = dict(os.environ)
        self.environment.update(
            {
                "NICKLAND_DEPLOYMENT_MODE": "lan_host",
                "NICKLAND_ENVIRONMENT": "production",
                "NICKLAND_DATA_DIRECTORY": str(data),
                "NICKLAND_DATABASE_URL": service_url,
                "NICKLAND_MIGRATION_DATABASE_URL": owner_url,
                "NICKLAND_BACKUP_INTERVAL_HOURS": "0",
                "NICKLAND_LOG_LEVEL": "WARNING",
                "PYTHONPATH": str(ROOT / "src"),
            }
        )
        self.process: subprocess.Popen[bytes] | None = None

    def command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "nickland.interface.hostctl", *arguments],
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def start(self) -> None:
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "nickland.interface.hostctl",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            env=self.environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError("The host did not start.")

    def kill(self) -> None:
        """No warning, no shutdown: what a power cut does to the service."""
        if self.process is not None:
            self.process.send_signal(signal.SIGKILL)
            self.process.wait(timeout=10)
            self.process = None

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def numbered_duplicates(engine) -> list[str]:  # type: ignore[no-untyped-def]
    metadata = MetaData()
    metadata.reflect(engine)
    found = []
    with engine.connect() as connection:
        for name, table in metadata.tables.items():
            if "number" not in table.columns or "business_id" not in table.columns:
                continue
            rows = connection.execute(
                select(table.c.number, func.count())
                .where(table.c.number.is_not(None))
                .group_by(table.c.business_id, table.c.number)
                .having(func.count() > 1)
            ).all()
            found += [f"{name}:{row[0]}" for row in rows]
    return found


def verify(run_suite: bool, *, in_container: bool = False) -> list[Outcome]:
    outcomes: list[Outcome] = []
    tag = secrets.token_hex(4)
    database = f"nickland_verify_{tag}"
    owner, service = f"nkv_owner_{tag}", f"nkv_service_{tag}"
    owner_password, service_password = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
    base = make_url(SUPER)
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    plan = RolePlan(database=database, owner=owner, service=service)
    target = base.set(database=database)
    owner_url = target.set(username=owner, password=owner_password, host="localhost")
    service_url = target.set(username=service, password=service_password, host="localhost")
    workspace = Path(tempfile.mkdtemp(prefix="nickland-phase15-"))
    host = Host(
        workspace,
        service_url.render_as_string(hide_password=False),
        owner_url.render_as_string(hide_password=False),
        free_port(),
    )
    try:
        superuser = create_engine(target, isolation_level="AUTOCOMMIT")
        with superuser.connect() as connection:
            for statement in plan.owner_statements(owner_password):
                connection.execute(text(statement))
        upgraded = host.command("upgrade")
        assert upgraded.returncode == 0, upgraded.stderr
        with superuser.connect() as connection:
            for statement in plan.service_statements(service_password):
                connection.execute(text(statement))
        superuser.dispose()

        # The business, its staff and a little stock, written as the service role.
        engine = create_engine(service_url)
        session = create_session_factory(engine)()
        SetupService(session).provision(
            SetupRequest(
                business_name="Nicholas Nickland Enterprise",
                administrator_username="proprietor",
                administrator_full_name="N. Nickland",
                administrator_password=PASSWORD,
                theme_preset="nickland",
            )
        )
        session.commit()
        business = session.scalar(select(Business.id))
        assert business is not None
        system = SystemActor(business_id=business, reason="Verification")
        identity = IdentityService(session)
        for index in range(USERS):
            identity.create_user(
                system,
                username=f"user{index}",
                full_name=f"Staff {index}",
                password=STAFF,
                role_codes=["administrator" if index < 4 else "sales_person"],
                must_change_password=False,
            )
        warehouse = session.scalar(select(Warehouse.id))
        unit = UnitService(session).units(system)[0]
        catalogue = CatalogueService(session)
        products = [
            catalogue.create_product(
                system,
                ProductDraft(
                    sku=f"VF-{n}", name=f"Item {n}", stock_unit_id=unit.id, list_price=D("60")
                ),
            )
            for n in range(3)
        ]
        session.flush()
        InventoryService(session).open_stock(
            system,
            OpeningDraft(
                warehouse_id=warehouse,  # type: ignore[arg-type]
                lines=[
                    StockLineDraft(product_id=item.id, quantity=D("60"), unit_cost=D("25"))
                    for item in products
                ],
            ),
        )
        supplier = PartyService(session).create(
            system, PartyDraft(name="Medisupply", is_supplier=True)
        )
        session.commit()
        (workspace / "attachments" / "a1").mkdir(parents=True, exist_ok=True)
        (workspace / "attachments" / "a1" / "agreement.pdf").write_bytes(b"%PDF-1.4" * 400)

        issued = host.command("certificates", "--name", "erp-host")
        assert issued.returncode == 0, issued.stderr
        authority = ssl.create_default_context(cafile=str(workspace / "tls" / "authority.pem"))
        host.start()
        url = f"https://localhost:{host.port}/api/v1"

        # 3. Only the authenticated service writes.
        problems: list[str] = []
        client = httpx.Client(verify=authority, timeout=30)
        schema = client.get(f"https://localhost:{host.port}/api/openapi.json").json()
        writes = 0
        for path, operations in schema["paths"].items():
            for method in operations:
                if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
                    continue
                if path in ("/api/v1/setup", "/api/v1/session"):
                    continue
                concrete = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
                status = client.request(
                    method.upper(), f"https://localhost:{host.port}{concrete}", json={}
                ).status_code
                writes += 1
                if status not in (401, 403):
                    problems.append(f"{method.upper()} {path} answered {status}")
        try:
            httpx.get(f"http://127.0.0.1:{host.port}/api/v1/health", timeout=5)
            problems.append("the host answered plain HTTP")
        except httpx.HTTPError:
            pass
        with engine.connect() as connection:
            listening = connection.execute(text("SHOW listen_addresses")).scalar()
        loopback = listening in ("localhost", "127.0.0.1", "::1")
        if not loopback and not in_container:
            problems.append(f"PostgreSQL listens on {listening}, beyond the host")
        for forbidden in (
            "CREATE TABLE intruder (id int)",
            "DROP TABLE parties",
            "UPDATE audit_events SET action = 'x'",
            "DELETE FROM audit_events",
        ):
            try:
                with engine.begin() as connection:
                    connection.execute(text(forbidden))
                problems.append(f"the service role could run: {forbidden}")
            except DBAPIError:
                pass
        outcomes.append(
            (
                "Only the authenticated service writes",
                not problems,
                f"{writes} write routes refused without a session over TLS; plain HTTP refused; "
                + (
                    f"PostgreSQL listens on {listening} only; "
                    if loopback
                    else f"PostgreSQL listens on {listening}, allowed only because "
                    "--database-in-container was given (a CI container, not a host); "
                )
                + "the service role cannot create, drop or alter tables, nor alter or delete "
                "the audit trail"
                if not problems
                else "; ".join(problems[:4]),
            )
        )

        # 2 and 4. Ten users over HTTPS; the host killed outright halfway, then restarted.
        tally: Counter[str] = Counter()
        lock = threading.Lock()

        def note(name: str) -> None:
            with lock:
                tally[name] += 1

        def call(
            person: httpx.Client, method: str, path: str, **kwargs: object
        ) -> httpx.Response | None:
            for _ in range(40):
                try:
                    return person.request(method, f"{url}{path}", **kwargs)  # type: ignore[arg-type]
                except httpx.TransportError:
                    note("connection lost")
                    time.sleep(0.5)
            return None

        def user(index: int) -> None:
            chance = random.Random(index)
            person = httpx.Client(verify=authority, timeout=60)
            token = person.post(
                f"{url}/session", json={"username": f"user{index}", "password": STAFF}
            ).json()["token"]
            person.headers["X-Nickland-Session"] = token
            call(
                person,
                "POST",
                "/pos/shifts",
                json={"opening_float": "100", "register_name": f"Till {index + 1}"},
            )
            for round_ in range(ROUNDS):
                sale = call(person, "GET", "/pos/sale")
                if sale is not None and sale.status_code == 200:
                    sale_id = sale.json()["id"]
                    call(
                        person,
                        "POST",
                        f"/pos/sales/{sale_id}/scan",
                        json={
                            "code": chance.choice(["VF-0", "VF-1", "VF-2"]),
                            "quantity": str(chance.randint(1, 2)),
                        },
                    )
                    call(
                        person,
                        "POST",
                        f"/pos/sales/{sale_id}/tenders",
                        json={"method": "cash", "cash_received": "500"},
                    )
                    done = call(person, "POST", f"/pos/sales/{sale_id}/complete")
                    note("sale" if done is not None and done.status_code == 200 else "sale refused")
                if index < 4:
                    made = call(
                        person,
                        "POST",
                        "/purchases/orders",
                        json={
                            "supplier_id": str(supplier.id),
                            "lines": [
                                {
                                    "description": "Gloves",
                                    "quantity": "5",
                                    "unit_cost": "18",
                                    "product_id": str(products[1].id),
                                }
                            ],
                        },
                    )
                    if made is not None and made.status_code == 201:
                        order = made.json()["id"]
                        call(person, "POST", f"/purchases/orders/{order}/approve")
                        call(person, "POST", f"/purchases/orders/{order}/issue")
                        note("purchase")
                    posted = call(
                        person,
                        "POST",
                        "/journals",
                        json={
                            "posting_date": "2026-11-20",
                            "narration": "Petty cash top-up",
                            "idempotency_key": f"verify:{round_}:{index // 2}",
                            "lines": [
                                {"account_code": "10100", "side": "debit", "amount": "10"},
                                {"account_code": "10200", "side": "credit", "amount": "10"},
                            ],
                        },
                    )
                    note(
                        "posting"
                        if posted is not None and posted.status_code == 201
                        else "posting refused"
                    )
            person.close()

        with ThreadPoolExecutor(max_workers=USERS) as pool:
            futures = [pool.submit(user, index) for index in range(USERS)]
            # Killed in the middle of the day, with requests in flight.
            while tally["sale"] < USERS * ROUNDS // 3:
                time.sleep(0.1)
            host.kill()
            host.start()
            for future in futures:
                future.result()

        problems = []
        duplicates = numbered_duplicates(engine)
        if duplicates:
            problems.append(f"duplicate numbers: {duplicates[:5]}")
        with engine.connect() as connection:
            keys = connection.execute(
                text(
                    "SELECT idempotency_key, count(*) FROM journal_entries WHERE idempotency_key "
                    "LIKE 'verify:%' GROUP BY idempotency_key HAVING count(*) > 1"
                )
            ).all()
        if keys:
            problems.append(f"a retried posting was posted twice: {keys[:3]}")
        session = create_session_factory(engine)()
        for position in session.scalars(select(StockPosition)).all():
            held = session.scalar(
                select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
                    StockMovement.product_fk == position.product_fk,
                    StockMovement.warehouse_fk == position.warehouse_fk,
                    StockMovement.state == "reserved",
                )
            )
            if position.reserved != D(held) or position.usable < 0:
                problems.append(f"stock position {position.product_fk} lost a reservation")
        report = IntegrityService(session).check(system)
        session.close()
        problems += report.problems
        busy = tally["sale"] >= USERS * ROUNDS * 0.6 and tally["purchase"] >= 4 * ROUNDS * 0.9
        outcomes.append(
            (
                "Ten users, no duplicate and no lost reservation",
                not duplicates
                and not keys
                and busy
                and not any("reservation" in p for p in problems),
                f"{tally['sale']} register sales, {tally['purchase']} purchase orders and "
                f"{tally['posting']} postings by {USERS} users over HTTPS; no number repeated, "
                "every retried posting posted once, every reservation accounted for"
                if busy and not problems
                else f"{dict(tally)}; {problems[:4]}",
            )
        )

        resilience = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "tests/integration/test_resilience.py",
            ],
            cwd=ROOT,
            env={**os.environ, "NICKLAND_TEST_POSTGRES_URL": SUPER},
            capture_output=True,
            text=True,
            check=False,
        )
        outcomes.append(
            (
                "Interruptions leave no partial posting",
                report.whole and resilience.returncode == 0,
                f"host killed with requests in flight and restarted ({tally['connection lost']} "
                "requests retried): every posted entry balances, every sale posted whole, stock "
                "agrees with "
                "its movements and layers, the trial balance balances; an interrupted sale, a "
                "payment callback delivered six times at once and a client hanging up mid-request "
                "each leave the record whole on both engines"
                if report.whole and resilience.returncode == 0
                else "; ".join(report.problems[:4]) + resilience.stdout[-400:],
            )
        )

        # 5. A restore drill reproduces the dataset and its attachments.
        host.stop()
        taken = host.command("backup")
        drilled = host.command("drill")
        reproduced = taken.returncode == 0 and drilled.returncode == 0
        outcomes.append(
            (
                "Restore drill",
                reproduced,
                drilled.stdout.strip().replace("\n", " ")
                if reproduced
                else (taken.stderr + drilled.stderr + drilled.stdout)[-400:],
            )
        )
        engine.dispose()
    finally:
        host.stop()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
            for row in connection.execute(
                text("SELECT datname FROM pg_database WHERE datname LIKE :name"),
                {"name": f"{database}_drill_%"},
            ).all():
                connection.execute(text(f'DROP DATABASE IF EXISTS "{row[0]}" WITH (FORCE)'))
            connection.execute(text(f"DROP ROLE IF EXISTS {service}"))
            connection.execute(text(f"DROP ROLE IF EXISTS {owner}"))
        admin.dispose()
        shutil.rmtree(workspace, ignore_errors=True)

    # 1. The whole suite, on both engines.
    if run_suite:
        suite = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs"],
            cwd=ROOT,
            env={**os.environ, "NICKLAND_TEST_POSTGRES_URL": SUPER},
            capture_output=True,
            text=True,
            check=False,
        )
        marks = "".join(
            line.split(" ")[0]
            for line in suite.stdout.splitlines()
            if re.search(r"\[ *\d+%\]$", line)
        )
        counts = {
            "passed": marks.count("."),
            "skipped": marks.count("s"),
            "failed": marks.count("F"),
            "error": marks.count("E"),
        }
        passed = suite.returncode == 0 and not counts.get("failed") and not counts.get("error")
        outcomes.insert(
            0,
            (
                "PostgreSQL behaves as SQLite",
                passed,
                "the whole automated suite passes with the integration and end-to-end layers "
                f"run on both engines ({counts or 'see the log'})"
                if passed
                else suite.stdout[-600:],
            ),
        )
    else:
        outcomes.insert(
            0,
            (
                "PostgreSQL behaves as SQLite",
                None,
                "not run here (--skip-suite): run without it, or see the test step, which runs "
                "the whole suite on both engines",
            ),
        )
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-suite", action="store_true")
    parser.add_argument(
        "--database-in-container",
        action="store_true",
        help="The PostgreSQL under test is a container that must listen beyond loopback (CI). "
        "A real host is always held to listen_addresses = 'localhost'.",
    )
    arguments = parser.parse_args()
    outcomes = verify(
        run_suite=not arguments.skip_suite, in_container=arguments.database_in_container
    )
    width = max(len(label) for label, _, _ in outcomes)
    print()
    for label, passed, detail in outcomes:
        state = "SKIP" if passed is None else ("PASS" if passed else "FAIL")
        print(f"  {state}  {label.ljust(width)}  {detail}")
    print()
    broken = [label for label, passed, _ in outcomes if passed is False]
    if broken:
        print(f"{len(broken)} criterion(a) not met: {broken}")
        return 1
    print("Every Phase 15 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
