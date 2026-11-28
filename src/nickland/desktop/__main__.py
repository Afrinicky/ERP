"""Entry point for the desktop application."""

from __future__ import annotations

import sys


def self_test() -> int:
    """What an installed copy needs, checked without opening a window: the interface, the
    migrations, and a PDF actually drawn. Run by the installer build before it packages."""
    import json

    from nickland.interface.static import interface_root
    from nickland.persistence.schema import head_revision

    results: dict[str, object] = {}
    interface = interface_root()
    results["interface"] = bool(interface and (interface / "index.html").is_file())
    results["schema_revision"] = head_revision()
    try:
        from weasyprint import HTML

        document = HTML(string="<h1>Nickland ERP</h1><p>GH₵ 1 234.50</p>").write_pdf()
        results["pdf"] = bool(document and document.startswith(b"%PDF"))
    except Exception as error:  # the libraries WeasyPrint needs are missing
        results["pdf"] = f"failed: {error}"
    print(json.dumps(results, indent=2))
    healthy = (
        results["interface"] is True and bool(results["schema_revision"]) and results["pdf"] is True
    )
    return 0 if healthy else 1


def main() -> int:
    from nickland.platform.frozen import prepare

    prepare()
    if "--self-test" in sys.argv[1:]:
        return self_test()
    from nickland.desktop.host import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
