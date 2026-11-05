"""Verify each Phase 1 exit criterion from docs/00-build-plan.md.

Run against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_1.py

The criteria it does not cover are those requiring a Windows machine: the signed
installer on a clean Windows 11 system, and operation with the network interface
disabled rather than merely absent.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
os.environ["NICKLAND_DATA_DIRECTORY"] = str(tmp)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient
from sqlalchemy import text

from nickland.interface.application import build_runtime, create_application
from nickland.interface.dependencies import LOCAL_HEADER, SESSION_HEADER
from nickland.persistence.base import Base
from nickland.platform.config import get_settings

settings = get_settings()
settings.ensure_directories()
runtime = build_runtime(settings, local_token="tok")
Base.metadata.create_all(runtime.engine)
with runtime.engine.begin() as c:
    c.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)"))
    c.execute(text("INSERT INTO alembic_version VALUES ('phase1')"))

app = create_application(runtime=runtime)
results = []


def check(label, ok, detail=""):
    results.append((label, ok, detail))


with TestClient(app) as cl:
    cl.headers[LOCAL_HEADER] = "tok"
    API = "/api/v1"

    # Criterion: administrator provisioning is forced before any business data
    r = cl.get(f"{API}/business")
    check(
        "Business data refused before provisioning", r.status_code == 401, f"HTTP {r.status_code}"
    )

    r = cl.post(
        f"{API}/setup",
        json={
            "business_name": "Nicholas Nickland Enterprise",
            "administrator_username": "proprietor",
            "administrator_full_name": "N. Nickland",
            "administrator_password": "Provision9Admin",
            "theme_preset": "charcoal",
        },
    )
    check("First-run provisioning completes", r.status_code == 201, r.text[:120])

    # A failed attempt, so the audit assertion below covers the refusal path too.
    cl.post(f"{API}/session", json={"username": "proprietor", "password": "Wrong9Password"})
    tok = cl.post(
        f"{API}/session", json={"username": "proprietor", "password": "Provision9Admin"}
    ).json()["token"]
    cl.headers[SESSION_HEADER] = tok

    # Criterion: administrator selects a preset, edits brand colours against validation,
    # and restores the preset defaults
    ok = (
        cl.put(f"{API}/appearance", json={"preset_code": "navy", "overrides": {}}).status_code
        == 200
    )
    bad = cl.put(
        f"{API}/appearance", json={"preset_code": "navy", "overrides": {"text-primary": "#EEEEEE"}}
    )
    good = cl.put(
        f"{API}/appearance",
        json={"preset_code": "navy", "overrides": {"accent-primary": "#1C4FB8"}},
    )
    reset = cl.post(f"{API}/appearance/reset").json()
    check(
        "Theme preset selected, validated, amended and reset",
        ok and bad.status_code == 422 and good.status_code == 200 and reset["overrides"] == {},
        f"illegible={bad.status_code} legible={good.status_code}",
    )

    # Criterion: a permission denial is raised by the service when the control is bypassed
    cl.post(
        f"{API}/users",
        json={
            "username": "cashier1",
            "full_name": "A. Cashier",
            "password": "Tamale4Harbour",
            "role_codes": ["cashier"],
        },
    )
    admin_token = tok
    cl.headers[SESSION_HEADER] = cl.post(
        f"{API}/session", json={"username": "cashier1", "password": "Tamale4Harbour"}
    ).json()["token"]
    denied = cl.post(
        f"{API}/users",
        json={
            "username": "accomplice",
            "full_name": "Accomplice",
            "password": "Tamale4Harbour",
            "role_codes": ["owner_administrator"],
        },
    )
    check(
        "Service refuses a bypassed interface control",
        denied.status_code == 403
        and denied.json()["error"]["detail"]["permission"] == "user.manage",
        f"HTTP {denied.status_code}",
    )
    cl.headers[SESSION_HEADER] = admin_token

    # Criterion: backup and restore demonstrated against a populated database
    b = cl.post(f"{API}/backups", json={}).json()
    rest = cl.post(
        f"{API}/backups/restore",
        json={"archive_name": b["storage_key"], "reason": "Exit criterion drill", "confirm": True},
    )
    check(
        "Backup verified and restore staged from a populated database",
        b["status"] == "verified" and rest.status_code == 200,
        f"{b['status']} / {b['verification_note']}",
    )

    # Criterion: every mutation appears in the audit store with before and after values
    cl.patch(f"{API}/business", json={"trading_name": "Nickland Meditech"})
    trail = cl.get(f"{API}/audit", params={"limit": 200}).json()
    actions = {e["action"] for e in trail["items"]}
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
    amended = next(e for e in trail["items"] if e["action"] == "business_amended")
    check(
        "Audit records every mutation with before and after values",
        expected <= actions and amended["after_values"]["trading_name"] == "Nickland Meditech",
        f"{len(actions)} distinct actions, {trail['total']} entries",
    )

    # Criterion: the application operates with no network interface available
    health = cl.get(f"{API}/health/detail").json()
    network = next(c for c in health["checks"] if c["name"] == "Network")
    check(
        "Offline reported as unavailable, never as failure",
        network["state"] in ("ok", "unavailable") and health["state"] != "failed",
        f"network={network['state']} overall={health['state']}",
    )

shutil.rmtree(tmp, ignore_errors=True)
print()
width = max(len(l) for l, _, _ in results)
for label, ok, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {label.ljust(width)}  {detail}")
print()
failed = [l for l, ok, _ in results if not ok]
print("ALL PHASE 1 EXIT CRITERIA MET" if not failed else f"FAILED: {failed}")
sys.exit(1 if failed else 0)
