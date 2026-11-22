"""Running as an installed application rather than from source.

The installer ships the application frozen by PyInstaller: the interpreter, the packages,
the built interface and the libraries WeasyPrint draws PDFs with, in one folder. This
module finds that folder and points WeasyPrint at its libraries before anything imports it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def bundle_root() -> Path | None:
    """The folder the frozen application unpacked into, or None when run from source."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))


def prepare() -> None:
    """Make the bundled GTK and Pango libraries visible to WeasyPrint on Windows."""
    root = bundle_root()
    if root is None:
        return
    libraries = root / "gtk"
    if libraries.is_dir():
        os.environ.setdefault("WEASYPRINT_DLL_DIRECTORIES", str(libraries))
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(libraries))
        os.environ["PATH"] = f"{libraries}{os.pathsep}{os.environ.get('PATH', '')}"
    fonts = root / "gtk" / "etc" / "fonts"
    if fonts.is_dir():
        os.environ.setdefault("FONTCONFIG_PATH", str(fonts))
