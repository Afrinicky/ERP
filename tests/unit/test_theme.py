"""Theme presets and validation. ADR-0002 ships two presets with no imposed default."""

from __future__ import annotations

import pytest

from nickland.domain.colour import check_pairing
from nickland.domain.errors import ValidationError
from nickland.domain.theme import (
    ThemeDefinition,
    audit,
    customisable_tokens,
    failures,
    get_preset,
    load_presets,
    pairings,
    validate_overrides,
)


def test_the_shipped_presets() -> None:
    presets = load_presets()
    assert set(presets) == {"charcoal", "harbour", "navy"}


def test_no_preset_is_marked_default() -> None:
    """ADR-0002: the administrator chooses at first run."""
    for preset in load_presets().values():
        assert not hasattr(preset, "is_default")


@pytest.mark.parametrize("code", ["charcoal", "harbour", "navy"])
def test_shipped_presets_pass_the_contrast_audit(code: str) -> None:
    assert failures(ThemeDefinition(preset=get_preset(code))) == []


@pytest.mark.parametrize("code", ["charcoal", "harbour", "navy"])
def test_every_pairing_is_measured(code: str) -> None:
    results = audit(ThemeDefinition(preset=get_preset(code)))
    assert len(results) == len(pairings())
    assert all(result.ratio >= 1.0 for result in results)


def test_presets_define_the_same_token_set() -> None:
    """A component references the semantic layer, so every preset must supply it all."""
    expected = set(get_preset("charcoal").colour)
    for code in ("navy", "harbour"):
        assert set(get_preset(code).colour) == expected, code


def test_charcoal_uses_charcoal_text_on_the_orange_accent() -> None:
    """The contrast finding: white on #F7931E is 2.30:1 and fails AA."""
    charcoal = get_preset("charcoal")
    assert charcoal.colour["accent-primary"] == "#F7931E"
    assert charcoal.colour["text-on-accent"] == "#252525"


def test_navy_uses_white_text_on_its_blue_accent() -> None:
    navy = get_preset("navy")
    assert navy.colour["text-on-accent"] == "#FFFFFF"


def test_unknown_preset_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Unknown theme preset"):
        get_preset("teal")


class TestOverrides:
    def test_accepts_a_legible_override(self) -> None:
        theme = validate_overrides("charcoal", {"accent-primary": "#A85C00"})
        assert theme.colour["accent-primary"] == "#A85C00"

    def test_derives_the_text_that_sits_on_a_custom_accent(self) -> None:
        """An administrator chooses an accent, not the text colour upon it."""
        dark = validate_overrides("charcoal", {"accent-primary": "#A85C00"})
        assert dark.colour["text-on-accent"] == "#FFFFFF"
        light = validate_overrides("charcoal", {"accent-primary": "#F7931E"})
        assert light.colour["text-on-accent"] == "#252525"

    def test_derives_hover_active_and_surface_shades(self) -> None:
        theme = validate_overrides("charcoal", {"accent-primary": "#A85C00"})
        for token in (
            "accent-primary-hover",
            "accent-primary-active",
            "accent-primary-surface",
            "focus-ring",
            "chart-1",
        ):
            assert theme.colour[token] != get_preset("charcoal").colour[token]

    def test_derives_a_chart_series_that_reads_against_a_white_card(self) -> None:
        """The brand orange reaches only 2.30:1 on white, so the series is darkened."""
        theme = validate_overrides("charcoal", {"accent-primary": "#F7931E"})
        assert theme.colour["chart-1"] != "#F7931E"
        assert check_pairing(theme.colour["chart-1"], "#FFFFFF", required=3.0).passes

    def test_derives_a_link_that_reads_against_the_canvas_not_merely_white(self) -> None:
        """A canvas is a shade off white, and that shade is the harder of the two."""
        for code in ("charcoal", "harbour", "navy"):
            theme = validate_overrides(code, {"accent-primary": "#F7931E"})
            canvas = theme.colour["surface-canvas"]
            assert check_pairing(theme.colour["text-link"], canvas, required=4.5).passes, code
            assert check_pairing(
                theme.colour["text-link"], theme.colour["surface-raised"], required=4.5
            ).passes, code

    def test_derives_navigation_text_from_a_custom_navigation_surface(self) -> None:
        theme = validate_overrides("navy", {"surface-navigation": "#0F2436"})
        assert theme.colour["text-inverse"] == "#FFFFFF"

    def test_an_explicit_choice_overrides_a_derivation(self) -> None:
        theme = validate_overrides(
            "charcoal", {"accent-primary": "#A85C00", "text-primary": "#111111"}
        )
        assert theme.colour["text-primary"] == "#111111"

    def test_a_mid_tone_accent_is_refused_with_a_suggested_shade(self) -> None:
        """No text colour reaches 4.5:1 on a mid-tone, so the editor offers the nearest."""
        with pytest.raises(ValidationError) as caught:
            validate_overrides("charcoal", {"accent-primary": "#B8640A"})
        assert "mid-tone" in str(caught.value)
        assert caught.value.context["remedies"]

    def test_refuses_an_illegible_override(self) -> None:
        """PRD 28.2: a failing combination is refused, not merely flagged."""
        with pytest.raises(ValidationError, match="not legible enough"):
            validate_overrides("charcoal", {"text-primary": "#EEEEEE"})

    def test_refuses_a_token_that_is_not_customisable(self) -> None:
        with pytest.raises(ValidationError, match="cannot be customised"):
            validate_overrides("charcoal", {"status-error": "#FF0000"})

    def test_refuses_a_malformed_colour(self) -> None:
        with pytest.raises(ValidationError, match="hexadecimal"):
            validate_overrides("charcoal", {"accent-primary": "orange"})

    def test_normalises_to_upper_case_hex(self) -> None:
        theme = validate_overrides("navy", {"accent-primary": "#2560d6"})
        assert theme.colour["accent-primary"] == "#2560D6"

    def test_empty_overrides_are_the_preset(self) -> None:
        theme = validate_overrides("navy", {})
        assert theme.colour == get_preset("navy").colour

    def test_every_customisable_token_exists_in_every_preset(self) -> None:
        for code in ("charcoal", "navy", "harbour"):
            assert customisable_tokens() <= set(get_preset(code).colour)
