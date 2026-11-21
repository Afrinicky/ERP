"""Reports, exports, saved runs, schedules and the period close over HTTP. PRD §8, §20.6,
§29.8, AC-22."""

from __future__ import annotations

import csv
import io
import json
import zipfile

from fastapi.testclient import TestClient

from tests.e2e.conftest import API

MONTH = {"granularity": "month", "anchor": "2026-11-05", "comparison": "prior", "filters": {}}


class TestReportsOverHttp:
    def test_the_catalogue_offers_the_pack_and_the_twelve(self, admin_client: TestClient) -> None:
        catalogue = admin_client.get(f"{API}/reports/catalogue")
        assert catalogue.status_code == 200, catalogue.text
        entries = catalogue.json()
        assert len(entries) == 13
        assert entries[0]["id"] == "management_pack"
        assert all(entry["available"] and entry["exportable"] for entry in entries)

    def test_every_report_runs(self, admin_client: TestClient) -> None:
        for entry in admin_client.get(f"{API}/reports/catalogue").json():
            response = admin_client.post(
                f"{API}/reports/run",
                json={"report_id": entry["id"], **MONTH}
                if entry["comparisons"]
                else {"report_id": entry["id"], "granularity": "month"},
            )
            assert response.status_code == 200, (entry["id"], response.text)
            header = response.json()["header"]
            assert header["currency"] and header["basis"] and header["generated_at"]

    def test_the_exports_carry_the_figures_the_screen_shows(self, admin_client: TestClient) -> None:
        shown = admin_client.post(f"{API}/reports/run", json={"report_id": "finance", **MONTH})
        assert shown.status_code == 200, shown.text
        figures = {
            cell
            for section in shown.json()["sections"]
            for table in section["tables"]
            for row in [*table["rows"], *([table["total"]] if table["total"] else [])]
            for key, cell in row["cells"].items()
            if isinstance(cell, str)
            and any(
                column["key"] == key and column["kind"] == "money" for column in table["columns"]
            )
        }
        assert figures
        query = {
            "report_id": "finance",
            "granularity": "month",
            "anchor": "2026-11-05",
            "comparison": "prior",
            "filters": json.dumps({}),
        }
        as_csv = admin_client.get(f"{API}/reports/export", params={**query, "format": "csv"})
        assert as_csv.status_code == 200, as_csv.text
        assert "attachment" in as_csv.headers["content-disposition"]
        cells = {
            cell
            for row in csv.reader(io.StringIO(as_csv.content.decode("utf-8-sig")))
            for cell in row
        }
        assert figures <= cells
        as_xlsx = admin_client.get(f"{API}/reports/export", params={**query, "format": "xlsx"})
        with zipfile.ZipFile(io.BytesIO(as_xlsx.content)) as book:
            assert "xl/worksheets/sheet1.xml" in book.namelist()
        as_pdf = admin_client.get(f"{API}/reports/export", params={**query, "format": "pdf"})
        assert as_pdf.content.startswith(b"%PDF")

    def test_a_run_is_saved_annotated_and_reopened(self, admin_client: TestClient) -> None:
        saved = admin_client.post(
            f"{API}/reports/runs", json={"report_id": "management_pack", **MONTH}
        )
        assert saved.status_code == 201, saved.text
        run_id = saved.json()["id"]
        noted = admin_client.put(
            f"{API}/reports/runs/{run_id}/notes",
            json={"section": "summary", "text": "A quiet first month."},
        )
        assert noted.status_code == 200, noted.text
        assert noted.json()["section_notes"] == {"summary": "A quiet first month."}
        listed = admin_client.get(f"{API}/reports/runs").json()
        assert listed[0]["id"] == run_id and listed[0]["notes"] == 1
        opened = admin_client.get(f"{API}/reports/runs/{run_id}").json()
        assert opened["report"]["reconciled"] is True
        exported = admin_client.get(f"{API}/reports/runs/{run_id}/export", params={"format": "csv"})
        assert "A quiet first month." in exported.content.decode("utf-8-sig")
        assert admin_client.delete(f"{API}/reports/runs/{run_id}").status_code == 204

    def test_a_schedule_is_made_run_and_delivered(self, admin_client: TestClient) -> None:
        recipients = admin_client.get(
            f"{API}/reports/recipients", params={"report_id": "management_pack"}
        ).json()
        assert recipients
        made = admin_client.post(
            f"{API}/reports/schedules",
            json={
                "name": "Monthly pack",
                "report_id": "management_pack",
                "frequency": "monthly",
                "run_day": 1,
                "run_hour": 7,
                "recipients": [recipients[0]["id"]],
                "formats": ["pdf"],
                "parameters": {"comparison": "prior"},
            },
        )
        assert made.status_code == 201, made.text
        schedule = made.json()
        assert schedule["next_run_at"]
        ran = admin_client.post(f"{API}/reports/schedules/{schedule['id']}/run")
        assert ran.status_code == 200, ran.text
        assert ran.json()["scheduled"] is True
        inbox = admin_client.get(f"{API}/reports/inbox").json()
        assert len(inbox) == 1 and inbox[0]["read_at"] is None
        admin_client.get(f"{API}/reports/runs/{inbox[0]['id']}")
        assert admin_client.get(f"{API}/reports/inbox").json()[0]["read_at"]
        assert admin_client.delete(f"{API}/reports/schedules/{schedule['id']}").status_code == 204


class TestPeriodCloseOverHttp:
    def test_the_checklist_close_and_reopening_request(self, admin_client: TestClient) -> None:
        periods = admin_client.get(f"{API}/ledger/periods").json()
        january = next(item for item in periods if item["sequence"] == 1)
        checklist = admin_client.get(f"{API}/ledger/periods/{january['id']}/checklist").json()
        assert not checklist["ready"]
        refused = admin_client.post(f"{API}/ledger/periods/{january['id']}/close", json={})
        assert refused.status_code == 409
        for step in checklist["steps"]:
            confirmed = admin_client.put(
                f"{API}/ledger/periods/{january['id']}/checklist/{step['key']}",
                json={"done": True},
            )
            assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["ready"] is True
        closed = admin_client.post(f"{API}/ledger/periods/{january['id']}/close", json={})
        assert closed.status_code == 200, closed.text
        asked = admin_client.post(
            f"{API}/ledger/periods/{january['id']}/reopen-requests",
            json={"reason": "A supplier invoice for January arrived late"},
        )
        assert asked.status_code == 201, asked.text
        decided = admin_client.post(
            f"{API}/ledger/reopen-requests/{asked.json()['id']}/decision", json={"approve": True}
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["self_approved"] is True
        listed = admin_client.get(f"{API}/ledger/reopen-requests").json()
        assert listed[0]["state"] == "approved"
        after = admin_client.get(f"{API}/ledger/periods/{january['id']}/checklist").json()
        assert after["period"]["state"] == "reopened"
