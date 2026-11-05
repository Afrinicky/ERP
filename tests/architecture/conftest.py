"""End-to-end fixtures. Exercises the HTTP surface against a real database."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from nickland.interface.application import create_application
from nickland.interface.dependencies import LOCAL_HEADER, SESSION_HEADER, Runtime
from nickland.platform.clock import FixedClock
from nickland.platform.config import Settings

LOCAL_TOKEN = "test-local-token"
API = "/api/v1"


@pytest.fixture
def runtime(
    settings: Settings,
    engine: Engine,
    session_factory: sessionmaker[Session],
    clock: FixedClock,
) -> Runtime:
    return Runtime(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        clock=clock,
        local_token=LOCAL_TOKEN,
    )


@pytest.fixture
def client(runtime: Runtime) -> Iterator[TestClient]:
    application = create_application(runtime=runtime)
    with TestClient(application) as test_client:
        test_client.headers[LOCAL_HEADER] = LOCAL_TOKEN
        yield test_client


SETUP_PAYLOAD = {
    "business_name": "Nicholas Nickland Enterprise",
    "administrator_username": "proprietor",
    "administrator_full_name": "N. Nickland",
    "administrator_password": "Provision9Admin",
    "theme_preset": "charcoal",
    "tax_identifier": "C0001234567",
}


@pytest.fixture
def provisioned_client(client: TestClient) -> TestClient:
    """A client against a provisioned installation."""
    response = client.post(f"{API}/setup", json=SETUP_PAYLOAD)
    assert response.status_code == 201, response.text
    return client


@pytest.fixture
def admin_client(provisioned_client: TestClient) -> TestClient:
    """A client signed in as the administrator."""
    response = provisioned_client.post(
        f"{API}/session",
        json={"username": "proprietor", "password": "Provision9Admin", "device": "pytest"},
    )
    assert response.status_code == 200, response.text
    provisioned_client.headers[SESSION_HEADER] = response.json()["token"]
    return provisioned_client


def sign_in(client: TestClient, username: str, password: str) -> str:
    response = client.post(f"{API}/session", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["token"])
