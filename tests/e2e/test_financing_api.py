"""Debt, loans and financing over HTTP: an agreement from preview to settlement, and the
registers that read it. PRD §25."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.e2e.conftest import API


def _bank(client: TestClient) -> str:
    accounts = client.get(f"{API}/treasury/accounts").json()
    return next(item["id"] for item in accounts if item["kind"] == "bank")


def _terms(bank: str, **changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "direction": "borrowing",
        "loan_type": "term_loan",
        "counterparty_name": "GCB Bank",
        "principal": "12000",
        "agreement_date": "2026-01-10",
        "model": "reducing_balance",
        "annual_rate": "24",
        "frequency": "monthly",
        "instalments": 12,
        "first_due_on": "2026-02-10",
        "financial_account_id": bank,
    }
    body.update(changes)
    return body


class TestAgreementsOverHttp:
    def test_preview_record_submit_draw_and_repay(self, admin_client: TestClient) -> None:
        bank = _bank(admin_client)
        preview = admin_client.post(f"{API}/financing/loans/preview", json=_terms(bank))
        assert preview.status_code == 200, preview.text
        assert preview.json()["summary"]["principal"].startswith("12000")
        assert len(preview.json()["rows"]) == 12

        made = admin_client.post(f"{API}/financing/loans", json=_terms(bank))
        assert made.status_code == 201, made.text
        loan = made.json()
        assert loan["state"] == "draft"
        assert loan["current"]["version"] == 1

        loan = admin_client.post(f"{API}/financing/loans/{loan['id']}/submit").json()
        assert loan["state"] == "active"

        drawn = admin_client.post(
            f"{API}/financing/loans/{loan['id']}/drawdowns",
            json={"amount": "12000", "on": "2026-01-10"},
        )
        assert drawn.status_code == 200, drawn.text
        assert drawn.json()["position"]["principal_outstanding"].startswith("12000")

        first = drawn.json()["current"]["rows"][0]
        repaid = admin_client.post(
            f"{API}/financing/loans/{loan['id']}/repayments",
            json={"amount": first["total"], "on": first["due_on"]},
        )
        assert repaid.status_code == 200, repaid.text
        body = repaid.json()
        assert body["position"]["agrees_with_ledger"] is True
        assert body["transactions"][0]["kind"] == "repayment"

        quote = admin_client.get(f"{API}/financing/loans/{loan['id']}/quote")
        assert quote.status_code == 200, quote.text

        listed = admin_client.get(f"{API}/financing/loans", params={"direction": "borrowing"})
        assert [item["number"] for item in listed.json()] == [loan["number"]]

    def test_money_is_refused_as_a_json_number(self, admin_client: TestClient) -> None:
        bank = _bank(admin_client)
        response = admin_client.post(f"{API}/financing/loans", json=_terms(bank, principal=12000.5))
        assert response.status_code == 422


class TestRegistersOverHttp:
    def test_the_registers_summary_and_calendar_read_the_agreement(
        self, admin_client: TestClient
    ) -> None:
        bank = _bank(admin_client)
        loan = admin_client.post(f"{API}/financing/loans", json=_terms(bank)).json()
        admin_client.post(f"{API}/financing/loans/{loan['id']}/submit")
        admin_client.post(
            f"{API}/financing/loans/{loan['id']}/drawdowns",
            json={"amount": "12000", "on": "2026-01-10"},
        )
        payable = admin_client.get(f"{API}/financing/register", params={"side": "payable"})
        assert payable.status_code == 200, payable.text
        assert [line["kind"] for line in payable.json()] == ["borrowing"]
        summary = admin_client.get(f"{API}/financing/summary").json()
        assert summary["loans_payable"]["count"] == 1
        assert admin_client.get(f"{API}/financing/calendar").status_code == 200
        assert admin_client.get(f"{API}/financing/maturity").status_code == 200
        flows = admin_client.get(
            f"{API}/financing/cash-flow", params={"start": "2026-01-01", "end": "2026-12-31"}
        )
        codes = {row["code"] for row in flows.json()}
        assert "loan_principal_borrowed" in codes

    def test_another_debt_is_recorded_and_settled(self, admin_client: TestClient) -> None:
        bank = _bank(admin_client)
        made = admin_client.post(
            f"{API}/financing/other-debts",
            json={
                "owed_to_us": False,
                "counterparty_name": "Tema Clinic",
                "description": "Bottle deposit held",
                "amount": "200",
                "financial_account_id": bank,
            },
        )
        assert made.status_code == 201, made.text
        settled = admin_client.post(
            f"{API}/financing/other-debts/{made.json()['id']}/settlements",
            json={"amount": "200", "financial_account_id": bank},
        )
        assert settled.json()["outstanding"].startswith("0")
