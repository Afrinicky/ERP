"""What an installed copy relies on: the schema created on first start and upgraded with a
copy taken first, the bundle found when frozen, and the self-test the installer build
runs before it packages."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from alembic import command

from nickland.persistence.engine import create_database_engine
from nickland.persistence.schema import (
    _config,
    current_revision,
    ensure_current,
    head_revision,
    is_current,
)
from nickland.platform.config import Environment, Settings
from nickland.platform.frozen import bundle_root, prepare

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def standalone(tmp_path: Path) -> Settings:
    settings = Settings(environment=Environment.TEST, data_directory=tmp_path / "data")
    settings.ensure_directories()
    return settings


class TestFirstStart:
    def test_a_new_installation_is_given_the_current_schema(self, standalone: Settings) -> None:
        engine = create_database_engine(standalone)
        try:
            before, after = ensure_current(standalone, engine)
            assert before is None and after == head_revision()
            assert is_current(engine)
            assert list(standalone.backup_directory.iterdir()) == []
        finally:
            engine.dispose()

    def test_an_upgrade_copies_the_database_aside_first(self, standalone: Settings) -> None:
        engine = create_database_engine(standalone)
        try:
            ensure_current(standalone, engine)
            command.downgrade(_config(str(engine.url)), "-1")
            behind = current_revision(engine)
            assert behind != head_revision()
            before, after = ensure_current(standalone, engine)
            assert (before, after) == (behind, head_revision())
            copies = list(standalone.backup_directory.glob("before-upgrade-*.db"))
            assert len(copies) == 1 and behind in copies[0].name
        finally:
            engine.dispose()


class TestBundle:
    def test_from_source_there_is_no_bundle_and_nothing_to_prepare(self) -> None:
        assert bundle_root() is None
        prepare()

    def test_a_frozen_bundle_is_found_and_its_libraries_announced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "gtk" / "etc" / "fonts").mkdir(parents=True)
        (tmp_path / "ui").mkdir()
        (tmp_path / "ui" / "index.html").write_text("<!doctype html>")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.delenv("WEASYPRINT_DLL_DIRECTORIES", raising=False)
        monkeypatch.delenv("FONTCONFIG_PATH", raising=False)
        monkeypatch.setattr("os.add_dll_directory", lambda _: None, raising=False)
        from nickland.interface.static import interface_root

        assert bundle_root() == tmp_path
        prepare()
        import os

        assert os.environ["WEASYPRINT_DLL_DIRECTORIES"] == str(tmp_path / "gtk")
        assert os.environ["FONTCONFIG_PATH"] == str(tmp_path / "gtk" / "etc" / "fonts")
        assert interface_root() == tmp_path / "ui"


@pytest.mark.skipif(
    not (ROOT / "ui" / "dist" / "index.html").is_file(), reason="the interface has not been built"
)
def test_the_self_test_finds_the_interface_and_draws_a_pdf(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from nickland.desktop.__main__ import self_test

    assert self_test() == 0
    assert '"pdf": true' in capsys.readouterr().out


def test_the_packaging_files_are_present() -> None:
    for relative in (
        "packaging/nickland.spec",
        "packaging/installer.iss",
        "packaging/entry_desktop.py",
        "packaging/entry_host.py",
        "packaging/assets/nickland.ico",
        ".github/workflows/build-windows-installer.yml",
    ):
        assert (ROOT / relative).is_file(), relative
