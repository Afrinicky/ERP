"""Budgets, the forecast and the charts over HTTP. PRD §20.7, §29.6, §29.8."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.e2e.conftest import API


class TestPlanningOverHttp:
    def test_a_budget_is_drawn_approved_and_compared(self, admin_client: TestClient) -> None:
        made = admin_client.post(
            f"{API}/planning/budgets",
            json={
                "name": "Operating budget",
                "period_kind": "monthly",
                "starts_on": "2026-01-01",
                "ends_on": "2026-12-31",
            },
        )
        assert made.status_code == 201, made.text
        budget = made.json()
        assert len(budget["periods"]) == 12
        lined = admin_client.put(
            f"{API}/planning/budgets/{budget['id']}/lines",
            json={"lines": [{"account_code": "60000", "total": "18000"}]},
        )
        assert lined.status_code == 200, lined.text
        assert lined.json()["total"].startswith("18000")
        admin_client.post(f"{API}/planning/budgets/{budget['id']}/submit")
        approved = admin_client.post(f"{API}/planning/budgets/{budget['id']}/approve", json={})
        assert approved.json()["state"] == "approved"
        comparison = admin_client.get(f"{API}/planning/budgets/{budget['id']}/comparison")
        assert comparison.status_code == 200, comparison.text
        assert comparison.json()["bases"]["actual"] == "Actual, posted"

    def test_the_forecast_and_alerts_answer(self, admin_client: TestClient) -> None:
        forecast = admin_client.get(f"{API}/planning/forecast", params={"horizon_days": 30})
        assert forecast.status_code == 200, forecast.text
        assert forecast.json()["basis"] == "forecast"
        what_if = admin_client.post(
            f"{API}/planning/forecast",
            json={
                "horizon_days": 30,
                "assumptions": {
                    "collection_delay_days": 15,
                    "extra_flows": [
                        {"on": "2026-12-01", "amount": "5000", "inward": False, "label": "Van"}
                    ],
                },
            },
        )
        assert what_if.status_code == 200, what_if.text
        assert what_if.json()["totals"]["planned_out"].startswith("5000")
        assert admin_client.get(f"{API}/planning/alerts").status_code == 200
        assert len(admin_client.get(f"{API}/planning/scenarios").json()) == 3

    def test_every_chart_answers_with_its_bases(self, admin_client: TestClient) -> None:
        charts = admin_client.get(f"{API}/analytics/charts").json()
        assert len(charts) == 11
        for item in charts:
            response = admin_client.get(
                f"{API}/analytics/charts/{item['id']}",
                params={"start": "2026-01-01", "end": "2026-12-31"},
            )
            assert response.status_code == 200, (item["id"], response.text)
            assert all(series["basis_label"] for series in response.json()["series"])
        assert (
            admin_client.get(
                f"{API}/analytics/profitability",
                params={"dimension": "product", "start": "2026-01-01", "end": "2026-12-31"},
            ).status_code
            == 200
        )
        assert (
            admin_client.get(
                f"{API}/analytics/erosion", params={"start": "2026-01-01", "end": "2026-12-31"}
            ).status_code
            == 200
        )
