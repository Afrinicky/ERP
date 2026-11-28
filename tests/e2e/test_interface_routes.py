"""The built interface is served for its own routes, so a reload or a link works."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from nickland.interface.application import create_application
from nickland.interface.dependencies import Runtime


def test_an_interface_route_is_served_the_page(runtime: Runtime, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>Nickland</title>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('app')")
    with TestClient(create_application(runtime=runtime, static_root=tmp_path)) as client:
        for path in ("/", "/sales", "/settings/access", "/register/"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert "<title>Nickland</title>" in response.text
        assert client.get("/assets/app.js").text == "console.log('app')"
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/favicon.ico").status_code == 404
