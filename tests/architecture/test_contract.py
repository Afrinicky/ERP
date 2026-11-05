"""The interface client and the service contract must not drift apart.

ADR-0010 requires the client to be generated. Until the generator is in place, this
asserts the same property: every path the client calls exists in the published OpenAPI
schema, with the method it uses.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "ui" / "src" / "api" / "client.ts"

# request<T>('METHOD', '/path' ...) or request<Page<T>>('METHOD', `/path/${id}` ...).
# The type argument may itself be generic, so it is matched non-greedily up to the call.
CALL = re.compile(
    r"request<.*?>\(\s*'(GET|POST|PUT|PATCH|DELETE)',\s*([`'])([^`']+)\2",
    re.DOTALL,
)


# A download is a browser navigation rather than a fetch, so the client builds its URL
# instead of calling it: `template: (kind) => `${BASE}/imports/template/${kind}``.
DOWNLOAD = re.compile(r"`\$\{BASE\}(/[^`]*)`")


def client_calls() -> set[tuple[str, str]]:
    """Every endpoint the interface addresses, with path parameters normalised."""
    if not CLIENT.exists():
        return set()
    source = CLIENT.read_text(encoding="utf-8")
    found: set[tuple[str, str]] = set()
    for method, _quote, path in CALL.findall(source):
        # `${id}` in a template literal becomes a path parameter.
        found.add((method, re.sub(r"\$\{[^}]+\}", "{}", path)))
    for path in DOWNLOAD.findall(source):
        # A URL the interface builds for the browser to open is addressed by GET.
        found.add(("GET", re.sub(r"\$\{[^}]+\}", "{}", path)))
    return found


def schema_paths(client: TestClient) -> set[tuple[str, str]]:
    document = client.get("/api/openapi.json").json()
    found: set[tuple[str, str]] = set()
    for path, operations in document["paths"].items():
        trimmed = path.removeprefix("/api/v1")
        normalised = re.sub(r"\{[^}]+\}", "{}", trimmed)
        for method in operations:
            found.add((method.upper(), normalised))
    return found


@pytest.mark.parametrize("engine_name", ["sqlite"], indirect=True)
def test_every_client_call_exists_in_the_schema(client: TestClient) -> None:
    calls = client_calls()
    if not calls:
        pytest.skip("the interface client is not present")
    published = schema_paths(client)
    missing = sorted(call for call in calls if call not in published)
    assert not missing, "The interface calls endpoints the service does not publish:\n" + "\n".join(
        f"  {method} {path}" for method, path in missing
    )


@pytest.mark.parametrize("engine_name", ["sqlite"], indirect=True)
def test_the_client_covers_every_published_endpoint(client: TestClient) -> None:
    """Every endpoint has a caller, so no dead surface accumulates."""
    calls = client_calls()
    if not calls:
        pytest.skip("the interface client is not present")
    published = schema_paths(client)
    unused = sorted(path for path in published if path not in calls)
    assert not unused, "The service publishes endpoints the interface never calls:\n" + "\n".join(
        f"  {method} {path}" for method, path in unused
    )
