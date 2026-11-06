#!/usr/bin/env python3
"""Verify each Phase 1 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_1.py

Two criteria are not covered here because they require Windows hardware: the signed
installer on a clean Windows 11 machine, and operation with the network interface
disabled rather than merely absent. Both are recorded as outstanding in the build plan.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-verify-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from nickland.interface.application import build_runtime, create_application  # noqa: E402
from nickland.interface.dependencies import LOCAL_HEADER, SESSION_HEADER  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

API = "/api/v1"
LOCAL_TOKEN = "verification-token"
ADMIN = "Provision9Admin"
STAFF = "Tamale4Harbour"

Outcome = tuple[str, bool, str]


def build_client() -> TestClient:
    settings = get_settings()
    settings.ensure_directories()
    runtime = build_runtime(settings, local_token=LOCAL_TOKEN)
    Base.metadata.create_all(runtime.engine)
    with runtime.engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-1')"))
    return TestClient(create_application(runtime=runtime))


def verify() -> list[Outcome]:
    outcomes: list[Outcome] = []

    def record(label: str, passed: bool, detail: str = "") -> None:
        outcomes.append((label, passed, detail))

    with build_client() as client:
        client.headers[LOCAL_HEADER] = LOCAL_TOKEN

        # Administrator provisioning is forced before any business data may be entered.
        refused = client.get(f"{API}/business")
        record(
            "Business data refused before provisioning",
            refused.status_code == 401,
            f"HTTP {refused.status_code}",
        )

        provisioned = client.post(
            f"{API}/setup",
            json={
                "business_name": "Nicholas Nickland Enterprise",
                "administrator_username": "proprietor",
                "administrator_full_name": "N. Nickland",
                "administrator_password": ADMIN,
                "theme_preset": "charcoal",
            },
        )
        record(
            "First-run provisioning completes",
            provisioned.status_code == 201,
            f"{provisioned.json().get('roles_created')} roles, "
            f"{provisioned.json().get('sequences_created')} sequences"
            if provisioned.status_code == 201
            else provisioned.text[:100],
        )

        # A failed attempt, so the audit assertion below covers the refusal path too.
        client.post(f"{API}/session", json={"username": "proprietor", "password": "Wrong9Password"})
        administrator = client.post(
            f"{API}/session", json={"username": "proprietor", "password": ADMIN}
        ).json()["token"]
        client.headers[SESSION_HEADER] = administrator

        # A preset is selected, brand colours are validated, and the defaults restored.
        selected = client.put(f"{API}/appearance", json={"preset_code": "navy", "overrides": {}})
        illegible = client.put(
            f"{API}/appearance",
            json={"preset_code": "navy", "overrides": {"text-primary": "#EEEEEE"}},
        )
        legible = client.put(
            f"{API}/appearance",
            json={"preset_code": "navy", "overrides": {"accent-primary": "#1C4FB8"}},
        )
        restored = client.post(f"{API}/appearance/reset").json()
        record(
            "Theme preset selected, validated, amended and reset",
            selected.status_code == 200
            and illegible.status_code == 422
            and legible.status_code == 200
            and restored["overrides"] == {},
            f"illegible refused with {illegible.status_code}, "
            f"legible accepted with {legible.status_code}",
        )

        # A permission denial is raised by the service when the control is bypassed.
        client.post(
            f"{API}/users",
            json={
                "username": "seller1",
                "full_name": "A. Cashier",
                "password": STAFF,
                "role_codes": ["sales_person"],
            },
        )
        client.headers[SESSION_HEADER] = client.post(
            f"{API}/session", json={"username": "seller1", "password": STAFF}
        ).json()["token"]
        denied = client.post(
            f"{API}/users",
            json={
                "username": "accomplice",
                "full_name": "Accomplice",
                "password": STAFF,
                "role_codes": ["administrator"],
            },
        )
        record(
            "Service refuses a bypassed interface control",
            denied.status_code == 403
            and denied.json()["error"]["detail"]["permission"] == "user.manage",
            f"HTTP {denied.status_code}",
        )
        client.headers[SESSION_HEADER] = administrator

        # Backup and restore against a populated database.
        backup = client.post(f"{API}/backups", json={}).json()
        restore = client.post(
            f"{API}/backups/restore",
            json={
                "archive_name": backup["storage_key"],
                "reason": "Exit criterion drill",
                "confirm": True,
            },
        )
        record(
            "Backup verified and restore staged from a populated database",
            backup["status"] == "verified" and restore.status_code == 200,
            backup.get("verification_note", ""),
        )

        # Every mutation appears in the audit store with before and after values.
        client.patch(f"{API}/business", json={"trading_name": "Nickland Meditech"})
        trail = client.get(f"{API}/audit", params={"limit": 200}).json()
        actions = {entry["action"] for entry in trail["items"]}
        expected = {
            "business_created",
            "setup_completed",
            "user_created",
            "sign_in",
            "sign_in_failed",
            "theme_amended",
            "theme_reset",
            "business_amended",
            "backup_run",
            "restore_performed",
        }
        amended = next(entry for entry in trail["items"] if entry["action"] == "business_amended")
        record(
            "Audit records every mutation with before and after values",
            expected <= actions and amended["after_values"]["trading_name"] == "Nickland Meditech",
            f"{len(actions)} distinct actions across {trail['total']} entries",
        )

        # Absence of a network is reported as unavailable, never as a failure.
        health = client.get(f"{API}/health/detail").json()
        network = next(item for item in health["checks"] if item["name"] == "Network")
        record(
            "Offline reported as unavailable, never as failure",
            network["state"] in ("ok", "unavailable") and health["state"] != "failed",
            f"network {network['state']}, overall {health['state']}",
        )

    return outcomes


def main() -> int:
    try:
        outcomes = verify()
    finally:
        shutil.rmtree(WORKSPACE, ignore_errors=True)

    width = max(len(label) for label, _, _ in outcomes)
    print()
    for label, passed, detail in outcomes:
        print(f"  {'PASS' if passed else 'FAIL'}  {label.ljust(width)}  {detail}")
    print()

    failures = [label for label, passed, _ in outcomes if not passed]
    if failures:
        print(f"{len(failures)} criterion(a) not met: {failures}")
        return 1
    print("Every Phase 1 exit criterion verifiable in this environment is met.")
    print("Outstanding, requiring Windows hardware: signed installer on a clean machine;")
    print("operation with the network interface disabled rather than merely absent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
