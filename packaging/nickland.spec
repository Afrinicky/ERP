# PyInstaller specification for Nickland ERP.
#
#   pyinstaller packaging/nickland.spec --noconfirm --distpath dist --workpath build
#
# Produces dist/NicklandERP/ holding two programs that share one runtime:
#   NicklandERP.exe   the desktop application (standalone, or a LAN workstation)
#   nickland-host.exe the LAN host's command (certificates, roles, upgrade, serve, backup)
# with the built interface, the migrations and, on Windows, the GTK and Pango libraries
# WeasyPrint draws PDFs with (from NICKLAND_GTK_DIR, set by the build workflow).

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
WINDOWS = sys.platform.startswith("win")
ICON = str(ROOT / "packaging" / "assets" / "nickland.ico")

datas = [
    (str(ROOT / "ui" / "dist"), "ui"),
    (str(SRC / "nickland" / "persistence" / "migrations"), "nickland/persistence/migrations"),
]
for package in ("weasyprint", "pyphen", "tinycss2", "cssselect2", "tinyhtml5", "fontTools"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass
try:
    datas += collect_data_files("webview")
except Exception:
    pass

binaries = []
gtk = os.environ.get("NICKLAND_GTK_DIR")
if gtk and Path(gtk).is_dir():
    # Every library the MSYS2 Pango installation holds, into one folder that
    # nickland.platform.frozen puts on the DLL search path before WeasyPrint loads.
    for library in Path(gtk, "bin").glob("*.dll"):
        binaries.append((str(library), "gtk"))
    fonts = Path(gtk, "etc", "fonts")
    if fonts.is_dir():
        datas.append((str(fonts), "gtk/etc/fonts"))

hidden = (
    collect_submodules("nickland")
    + collect_submodules("uvicorn")
    + collect_submodules("alembic", filter=lambda name: ".testing" not in name)
    + collect_submodules("sqlalchemy.dialects")
    + collect_submodules("psycopg")
    + ["psycopg_binary", "argon2", "multipart", "email_validator"]
)
excluded = ["pytest", "mypy", "ruff", "tkinter", "IPython", "matplotlib"]


def analysis(script: str) -> Analysis:
    return Analysis(
        [str(ROOT / "packaging" / script)],
        pathex=[str(SRC)],
        binaries=binaries,
        datas=datas,
        hiddenimports=hidden,
        excludes=excluded,
        noarchive=False,
    )


desktop = analysis("entry_desktop.py")
host = analysis("entry_host.py")

desktop_exe = EXE(
    PYZ(desktop.pure),
    desktop.scripts,
    [],
    exclude_binaries=True,
    name="NicklandERP",
    console=False,
    icon=ICON,
    version=str(ROOT / "packaging" / "version.txt") if (ROOT / "packaging" / "version.txt").exists() else None,
)
host_exe = EXE(
    PYZ(host.pure),
    host.scripts,
    [],
    exclude_binaries=True,
    name="nickland-host",
    console=True,
    icon=ICON,
)

COLLECT(
    desktop_exe,
    desktop.binaries,
    desktop.datas,
    host_exe,
    host.binaries,
    host.datas,
    name="NicklandERP",
)
