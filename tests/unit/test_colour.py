"""Contrast measurement. PRD 28.2 requires validated accessible combinations."""

from __future__ import annotations

import pytest

from nickland.domain.colour import (
    AA_NORMAL_TEXT,
    Colour,
    check_pairing,
    contrast_ratio,
    readable_text_on,
)


def test_parses_three_and_six_digit_forms() -> None:
    assert Colour.parse("#FFF") == Colour(255, 255, 255)
    assert Colour.parse("#252525").to_hex() == "#252525"
    assert Colour.parse("  #f7931e ").to_hex() == "#F7931E"


@pytest.mark.parametrize("value", ["252525", "#GGGGGG", "#1234", "", "rgb(0,0,0)"])
def test_rejects_invalid_colours(value: str) -> None:
    with pytest.raises(ValueError, match="hexadecimal"):
        Colour.parse(value)


def test_extremes() -> None:
    assert round(contrast_ratio(Colour.parse("#FFF"), Colour.parse("#000")), 2) == 21.0
    assert round(contrast_ratio(Colour.parse("#FFF"), Colour.parse("#FFF")), 2) == 1.0


def test_ratio_is_symmetric() -> None:
    first, second = Colour.parse("#F7931E"), Colour.parse("#252525")
    assert contrast_ratio(first, second) == contrast_ratio(second, first)


def test_white_on_brand_orange_fails_aa() -> None:
    """The finding that set text.on-accent to charcoal in the Charcoal preset."""
    result = check_pairing("#FFFFFF", "#F7931E", required=AA_NORMAL_TEXT)
    assert not result.passes
    assert round(result.ratio, 2) == 2.30


def test_charcoal_on_brand_orange_passes_aa() -> None:
    result = check_pairing("#252525", "#F7931E", required=AA_NORMAL_TEXT)
    assert result.passes
    assert round(result.ratio, 2) == 6.68


def test_readable_text_chooses_the_better_candidate() -> None:
    assert readable_text_on("#F7931E") == "#252525"
    assert readable_text_on("#252525") == "#FFFFFF"


def test_describe_states_the_verdict() -> None:
    assert "fails" in check_pairing("#FFFFFF", "#F7931E").describe()
    assert "passes" in check_pairing("#252525", "#FFFFFF").describe()
