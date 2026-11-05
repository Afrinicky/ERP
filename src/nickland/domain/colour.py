"""Colour measurement for theme validation.

The theme editor refuses any foreground-and-background pairing that fails WCAG AA,
per PRD 28.2. The thresholds are implemented here so they are testable in isolation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

AA_NORMAL_TEXT: Final = 4.5
"""Minimum contrast for body text."""

AA_LARGE_TEXT: Final = 3.0
"""Minimum contrast for text at 18px and above, or 14px bold."""

AA_NON_TEXT: Final = 3.0
"""Minimum contrast for interface components and graphical objects."""

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


@dataclass(frozen=True, slots=True)
class Colour:
    """An opaque sRGB colour."""

    red: int
    green: int
    blue: int

    @classmethod
    def parse(cls, value: str) -> Colour:
        """Parse a three- or six-digit hexadecimal colour."""
        text = value.strip()
        if not _HEX.match(text):
            raise ValueError(f"Not a hexadecimal colour: {value!r}")
        digits = text.lstrip("#")
        if len(digits) == 3:
            digits = "".join(digit * 2 for digit in digits)
        return cls(int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))

    def to_hex(self) -> str:
        return f"#{self.red:02X}{self.green:02X}{self.blue:02X}"

    @property
    def relative_luminance(self) -> float:
        """Relative luminance per WCAG 2.1."""

        def channel(component: int) -> float:
            proportion = component / 255.0
            if proportion <= 0.03928:
                return proportion / 12.92
            # float ** float is untyped, so the result is narrowed explicitly.
            return float(((proportion + 0.055) / 1.055) ** 2.4)

        luminance: float = (
            0.2126 * channel(self.red)
            + 0.7152 * channel(self.green)
            + 0.0722 * channel(self.blue)
        )
        return luminance


def contrast_ratio(first: Colour, second: Colour) -> float:
    """Contrast ratio between two colours, between 1.0 and 21.0."""
    lighter = max(first.relative_luminance, second.relative_luminance)
    darker = min(first.relative_luminance, second.relative_luminance)
    return (lighter + 0.05) / (darker + 0.05)


@dataclass(frozen=True, slots=True)
class ContrastResult:
    """The outcome of one pairing check."""

    foreground: str
    background: str
    ratio: float
    required: float
    label: str

    @property
    def passes(self) -> bool:
        # Guard against floating-point representation at the threshold.
        return round(self.ratio, 2) >= self.required

    def describe(self) -> str:
        verdict = "passes" if self.passes else "fails"
        return (
            f"{self.label}: {self.foreground} on {self.background} "
            f"is {self.ratio:.2f}:1, {verdict} the {self.required}:1 requirement"
        )


def check_pairing(
    foreground: str,
    background: str,
    *,
    required: float = AA_NORMAL_TEXT,
    label: str = "pairing",
) -> ContrastResult:
    """Measure one foreground-and-background pairing."""
    ratio = contrast_ratio(Colour.parse(foreground), Colour.parse(background))
    return ContrastResult(
        foreground=Colour.parse(foreground).to_hex(),
        background=Colour.parse(background).to_hex(),
        ratio=ratio,
        required=required,
        label=label,
    )


def readable_text_on(background: str, candidates: tuple[str, str] = ("#FFFFFF", "#252525")) -> str:
    """Choose whichever candidate reads better on the given background."""
    surface = Colour.parse(background)
    best = max(candidates, key=lambda candidate: contrast_ratio(Colour.parse(candidate), surface))
    return Colour.parse(best).to_hex()


def scale(value: str, factor: float) -> str:
    """Scale a colour towards black (factor below 1) or white (above 1)."""
    source = Colour.parse(value)

    def channel(component: int) -> int:
        if factor <= 1.0:
            return max(0, min(255, round(component * factor)))
        return max(0, min(255, round(component + (255 - component) * (factor - 1.0))))

    return Colour(channel(source.red), channel(source.green), channel(source.blue)).to_hex()


def darken(value: str, amount: float = 0.12) -> str:
    """Darken a colour by a proportion of each channel."""
    return scale(value, 1.0 - amount)


def lighten(value: str, amount: float = 0.12) -> str:
    """Lighten a colour towards white."""
    return scale(value, 1.0 + amount)


def tint(value: str, amount: float = 0.90) -> str:
    """Produce a very light surface tint of a colour, for accent surfaces."""
    return scale(value, 1.0 + amount)


def best_text_ratio(background: str) -> float:
    """The best contrast ratio any of the two text candidates achieves."""
    surface = Colour.parse(background)
    return max(
        contrast_ratio(Colour.parse("#FFFFFF"), surface),
        contrast_ratio(Colour.parse("#252525"), surface),
    )


def ensure_contrast(value: str, background: str, required: float) -> str:
    """Darken a colour until it meets a contrast requirement against a background.

    Used to derive a chart series from a brand accent. The brand orange #F7931E reads
    well behind charcoal text but only reaches 2.30:1 against a white card, so it
    cannot itself be a chart series; this produces the nearest shade that can.
    """
    surface = Colour.parse(background)
    if round(contrast_ratio(Colour.parse(value), surface), 2) >= required:
        return Colour.parse(value).to_hex()
    for step in range(1, 51):
        candidate = darken(value, step * 0.02)
        if round(contrast_ratio(Colour.parse(candidate), surface), 2) >= required:
            return candidate
    return "#252525"


def nearest_legible_shade(value: str, *, required: float = AA_NORMAL_TEXT) -> str | None:
    """Find the nearest shade of a colour that can carry legible text.

    A mid-tone cannot reach 4.5:1 against either white or charcoal, so the theme editor
    offers the closest darker or lighter shade that can, rather than simply refusing.
    """
    for step in range(1, 51):
        for candidate in (darken(value, step * 0.02), lighten(value, step * 0.02)):
            if round(best_text_ratio(candidate), 2) >= required:
                return candidate
    return None
