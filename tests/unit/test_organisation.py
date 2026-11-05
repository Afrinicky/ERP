"""Numbering patterns and code validation. PRD 6.7."""

from __future__ import annotations

from datetime import date

import pytest

from nickland.domain.errors import ValidationError
from nickland.domain.organisation import (
    DEFAULT_PATTERN,
    DOCUMENT_KIND_LABELS,
    DocumentKind,
    NumberPattern,
    ResetCadence,
    period_key,
    validate_code,
)

ON = date(2026, 11, 5)


def test_default_pattern() -> None:
    assert DEFAULT_PATTERN.render(counter=184, prefix="QTN", on=ON) == "QTN-2026-0184"


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("{prefix}-{counter}", "INV-7"),
        ("{prefix}-{counter:6}", "INV-000007"),
        ("{prefix}/{year2}{month}/{counter:4}", "INV/2611/0007"),
        ("{prefix}-{branch}-{year}{month}{day}-{counter:3}", "INV-ACC-20261105-007"),
    ],
)
def test_placeholders(template: str, expected: str) -> None:
    assert (
        NumberPattern(template).render(counter=7, prefix="INV", branch_code="ACC", on=ON)
        == expected
    )


def test_pattern_requires_a_counter() -> None:
    with pytest.raises(ValidationError, match="must include"):
        NumberPattern("{prefix}-{year}")


def test_unknown_placeholder_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Unknown placeholder"):
        NumberPattern("{prefix}-{quarter}-{counter}")


@pytest.mark.parametrize(
    ("cadence", "expected"),
    [
        (ResetCadence.NEVER, "all"),
        (ResetCadence.ANNUALLY, "2026"),
        (ResetCadence.MONTHLY, "2026-11"),
    ],
)
def test_period_buckets(cadence: ResetCadence, expected: str) -> None:
    assert period_key(cadence, ON) == expected


def test_every_document_kind_has_a_label_and_prefix() -> None:
    assert set(DOCUMENT_KIND_LABELS) == set(DocumentKind)
    prefixes = [prefix for _, prefix in DOCUMENT_KIND_LABELS.values()]
    assert len(set(prefixes)) == len(prefixes), "document prefixes must be distinct"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Batch Number", "batch_number"), ("  MAIN  ", "main"), ("unit-cost", "unit_cost")],
)
def test_code_normalisation(raw: str, expected: str) -> None:
    assert validate_code(raw) == expected


@pytest.mark.parametrize("raw", ["a", "ab", "1abc", "_abc", "abc_", "a" * 60, ""])
def test_invalid_codes(raw: str) -> None:
    with pytest.raises(ValidationError):
        validate_code(raw)
