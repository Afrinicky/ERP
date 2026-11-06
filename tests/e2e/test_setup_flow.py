"""First-run provisioning over HTTP. PRD 9.6."""

from __future__ import annotations

from fastapi.testclient import TestClient

from nickland.interface.dependencies import LOCAL_HEADER, SESSION_HEADER
from tests.e2e.conftest import API, SETUP_PAYLOAD


def test_a_fresh_installation_reports_that_setup_is_required(client: TestClient) -> None:
    body = client.get(f"{API}/setup").json()
    assert body["setup_required"] is True
    assert {item["code"] for item in body["presets"]} == {
        "nickland",
        "charcoal",
        "harbour",
        "navy",
    }
    assert body["password_requirements"]


def test_the_house_palette_leads_and_the_rest_are_offered(client: TestClient) -> None:
    """ADR-0002: one recommendation, and no preset withheld."""
    presets = client.get(f"{API}/setup").json()["presets"]
    assert presets[0]["code"] == "nickland"
    assert [item["code"] for item in presets if item["recommended"]] == ["nickland"]
    sources = {item["code"]: item["source"] for item in presets}
    assert sources == {
        "nickland": "Brand mark",
        "charcoal": "PRD 28.1",
        "navy": "PRD 7.1",
        "harbour": "the pharmacy terminal reference",
    }


def test_provisioning_creates_the_business(client: TestClient) -> None:
    response = client.post(f"{API}/setup", json=SETUP_PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert body["roles_created"] == 8
    assert body["sequences_created"] == 22
    assert client.get(f"{API}/setup").json()["setup_required"] is False


def test_provisioning_runs_only_once(provisioned_client: TestClient) -> None:
    response = provisioned_client.post(f"{API}/setup", json=SETUP_PAYLOAD)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_a_weak_password_is_refused_with_the_field_named(client: TestClient) -> None:
    response = client.post(
        f"{API}/setup", json={**SETUP_PAYLOAD, "administrator_password": "short"}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["field"] == "password"


def test_an_unknown_preset_is_refused(client: TestClient) -> None:
    response = client.post(f"{API}/setup", json={**SETUP_PAYLOAD, "theme_preset": "teal"})
    assert response.status_code == 422
    assert "Unknown theme preset" in response.json()["error"]["message"]


def test_an_unknown_field_is_rejected_rather_than_ignored(client: TestClient) -> None:
    response = client.post(f"{API}/setup", json={**SETUP_PAYLOAD, "sneaky": "value"})
    assert response.status_code == 422


def test_a_request_without_the_local_token_is_refused(client: TestClient) -> None:
    """The local service accepts only requests bearing the desktop host's token."""
    client.headers.pop(LOCAL_HEADER)
    response = client.get(f"{API}/setup")
    assert response.status_code == 401


def test_health_is_available_before_sign_in(client: TestClient) -> None:
    """The status strip must be able to report a problem that prevents sign-in."""
    body = client.get(f"{API}/health").json()
    assert body["state"] in ("ok", "degraded", "unavailable")
    assert {check["name"] for check in body["checks"]} >= {
        "Application service",
        "Database",
        "Schema",
        "Storage",
        "Network",
    }


def test_an_unauthenticated_request_is_refused(provisioned_client: TestClient) -> None:
    provisioned_client.headers.pop(SESSION_HEADER, None)
    response = provisioned_client.get(f"{API}/business")
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Sign in to continue."
