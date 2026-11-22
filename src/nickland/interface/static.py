"""Where the built interface is, for the service to serve beside the API."""

from __future__ import annotations

from pathlib import Path


def interface_root() -> Path | None:
    """The built interface: in the installed bundle, the source tree, or beside the package."""
    from nickland.platform.frozen import bundle_root

    bundle = bundle_root()
    if bundle is not None and (bundle / "ui").is_dir():
        return bundle / "ui"
    package = Path(__file__).resolve().parents[1]
    candidate = package.parents[1] / "ui" / "dist"
    if candidate.is_dir():
        return candidate
    bundled = package / "desktop" / "interface"
    return bundled if bundled.is_dir() else None
