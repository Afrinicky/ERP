"""Entry point for the desktop application."""

from __future__ import annotations


def main() -> int:
    from nickland.desktop.host import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
