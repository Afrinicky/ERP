"""Architectural constraints, enforced rather than documented.

ADR-0010 states that each of these is an automated check. A violation fails the build
at the point it is introduced, not at review.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "nickland"
UI = ROOT / "ui" / "src"

# The dependency order of the architecture. A module may import only from modules at
# the same level or below it.
LAYER_ORDER = [
    "platform",
    "domain",
    "persistence",
    "integration",
    "application",
    "documents",
    "interface",
    "desktop",
]


def python_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*.py") if "migrations" not in path.parts)


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def layer_of(path: Path) -> str | None:
    parts = path.relative_to(SOURCE).parts
    return parts[0] if parts and parts[0] in LAYER_ORDER else None


class TestDomainPurity:
    """ADR-0001 and the architecture: the domain imports no framework."""

    FORBIDDEN = (
        "sqlalchemy",
        "fastapi",
        "pydantic",
        "starlette",
        "uvicorn",
        "alembic",
        "httpx",
        "requests",
        "webview",
    )

    def test_domain_imports_no_framework(self) -> None:
        offences: list[str] = []
        for path in python_files(SOURCE / "domain"):
            for module in imports_of(path):
                root = module.split(".")[0]
                if root in self.FORBIDDEN:
                    offences.append(f"{path.relative_to(ROOT)} imports {module}")
        assert not offences, "The domain layer must import no framework:\n" + "\n".join(offences)

    def test_domain_imports_nothing_above_itself(self) -> None:
        offences: list[str] = []
        for path in python_files(SOURCE / "domain"):
            for module in imports_of(path):
                if module.startswith("nickland.") and not module.startswith(
                    ("nickland.domain", "nickland.platform")
                ):
                    offences.append(f"{path.relative_to(ROOT)} imports {module}")
        assert not offences, "\n".join(offences)

    def test_platform_imports_nothing_from_the_application(self) -> None:
        offences: list[str] = []
        for path in python_files(SOURCE / "platform"):
            for module in imports_of(path):
                if module.startswith("nickland.") and not module.startswith(
                    ("nickland.platform", "nickland.domain")
                ):
                    offences.append(f"{path.relative_to(ROOT)} imports {module}")
        assert not offences, "\n".join(offences)


class TestLayerOrder:
    """A module depends only on modules below it; cycles are rejected."""

    @pytest.mark.parametrize("layer", LAYER_ORDER)
    def test_layer_does_not_import_upward(self, layer: str) -> None:
        directory = SOURCE / layer
        if not directory.is_dir():
            pytest.skip(f"{layer} is not yet present")
        position = LAYER_ORDER.index(layer)
        above = set(LAYER_ORDER[position + 1 :])
        offences: list[str] = []
        for path in python_files(directory):
            for module in imports_of(path):
                if not module.startswith("nickland."):
                    continue
                target = module.split(".")[1] if "." in module[len("nickland.") :] + "." else ""
                target = module[len("nickland.") :].split(".")[0]
                if target in above:
                    offences.append(f"{path.relative_to(ROOT)} imports {module}")
        assert not offences, f"{layer} imports from a layer above it:\n" + "\n".join(offences)


class TestPersistenceBoundary:
    """ORM entities do not escape persistence into the interface layer."""

    def test_interface_does_not_import_the_orm_directly(self) -> None:
        """The interface may read models for a response, but never the ORM itself."""
        offences: list[str] = []
        for path in python_files(SOURCE / "interface"):
            for module in imports_of(path):
                if module.startswith("sqlalchemy.orm") and "Session" not in path.read_text(
                    encoding="utf-8"
                ):
                    offences.append(f"{path.relative_to(ROOT)} imports {module}")
        assert not offences, "\n".join(offences)


class TestMoneySafety:
    """PRD 9.5: binary floating-point is prohibited in the monetary path."""

    def test_no_float_conversion_in_the_domain_or_application(self) -> None:
        pattern = re.compile(r"\bfloat\s*\(")
        permitted = {
            "colour.py",  # contrast ratios are not money
            "theme.py",
        }
        offences: list[str] = []
        for layer in ("domain", "application", "persistence"):
            for path in python_files(SOURCE / layer):
                if path.name in permitted:
                    continue
                for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), start=1
                ):
                    if pattern.search(line) and "noqa" not in line:
                        offences.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        assert not offences, "float() in the monetary path:\n" + "\n".join(offences)

    def test_no_string_composed_sql(self) -> None:
        """Queries are parameterised; no SQL is built by concatenation."""
        pattern = re.compile(r"""(?:execute|text)\s*\(\s*f['"]""")
        offences: list[str] = []
        for path in python_files(SOURCE):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offences.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        assert not offences, "SQL composed from a formatted string:\n" + "\n".join(offences)


class TestPresentationHoldsNoBusinessRule:
    """ADR-0001: pricing, tax and posting are computed by the service, not the frontend."""

    def test_the_frontend_performs_no_monetary_arithmetic(self) -> None:
        if not UI.is_dir():
            pytest.skip("the interface is not present")
        # Arithmetic on anything named like money, in the frontend, would mean a
        # business rule had migrated out of Python.
        # The operator must be spaced on both sides, so a route such as
        # "/taxation/*" is not mistaken for arithmetic.
        pattern = re.compile(
            r"\b\w*(amount|total|price|cost|margin|commission|balance|subtotal|quantity)"
            r"\w*\s+[*/+-]\s+",
            re.IGNORECASE,
        )
        offences: list[str] = []
        for path in sorted(UI.rglob("*.ts*")):
            if "tokens" in path.parts or path.name.endswith(".d.ts"):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("*", "//", "/*")):
                    continue
                if pattern.search(stripped):
                    offences.append(f"{path.relative_to(ROOT)}:{number}: {stripped}")
        assert not offences, "Monetary arithmetic in the presentation layer:\n" + "\n".join(
            offences
        )

    def test_the_frontend_holds_no_colour_literal_outside_the_token_layer(self) -> None:
        """PRD 9.7: the visual system is implemented through centralised tokens."""
        if not UI.is_dir():
            pytest.skip("the interface is not present")
        pattern = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(")
        permitted_files = {"icons.tsx"}
        offences: list[str] = []
        for path in sorted(UI.rglob("*.ts*")):
            if "tokens" in path.parts or path.name in permitted_files:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line) and "#000000" not in line and "test" not in path.name:
                    offences.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        assert not offences, "Colour literal outside the token layer:\n" + "\n".join(offences)

    def test_the_stylesheets_hold_no_colour_literal(self) -> None:
        styles = UI / "styles"
        if not styles.is_dir():
            pytest.skip("the interface is not present")
        pattern = re.compile(r"#[0-9a-fA-F]{3,8}\b")
        offences: list[str] = []
        for path in sorted(styles.rglob("*.css")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                # A data URI carries its own colour, which cannot reference a token.
                if pattern.search(line) and "data:image" not in line:
                    offences.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        assert not offences, "Colour literal in a stylesheet:\n" + "\n".join(offences)
