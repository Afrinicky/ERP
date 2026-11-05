"""Theme model and validation.

Two presets ship as equals and no default is imposed, per ADR-0002. The administrator
may override a bounded set of colours; every override is validated against WCAG AA
before it is accepted, per PRD 28.2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from nickland.domain.colour import (
    AA_NON_TEXT,
    AA_NORMAL_TEXT,
    ContrastResult,
    best_text_ratio,
    check_pairing,
    darken,
    ensure_contrast,
    nearest_legible_shade,
    readable_text_on,
    tint,
)
from nickland.domain.errors import ValidationError

TOKENS_ROOT = Path(__file__).resolve().parents[3] / "tokens"


@dataclass(frozen=True, slots=True)
class Pairing:
    """One foreground-and-background combination the audit must verify."""

    label: str
    foreground: str
    background: str
    required: float


@dataclass(frozen=True, slots=True)
class Preset:
    """A shipped palette."""

    code: str
    name: str
    description: str
    source: str
    colour: dict[str, str]


@dataclass(slots=True)
class ThemeDefinition:
    """A preset with any administrator overrides applied."""

    preset: Preset
    overrides: dict[str, str] = field(default_factory=dict)

    @property
    def colour(self) -> dict[str, str]:
        return {**self.preset.colour, **self.overrides}

    def resolve(self, token: str) -> str:
        try:
            return self.colour[token]
        except KeyError as error:
            raise ValidationError(f"Unknown colour token: {token!r}", field=token) from error


@lru_cache(maxsize=1)
def _load_pairings() -> tuple[tuple[Pairing, ...], frozenset[str]]:
    data = json.loads((TOKENS_ROOT / "pairings.json").read_text(encoding="utf-8"))
    pairings = tuple(
        Pairing(
            label=item["label"],
            foreground=item["foreground"],
            background=item["background"],
            required=float(item["required"]),
        )
        for item in data["pairings"]
    )
    return pairings, frozenset(data["customisable"])


def pairings() -> tuple[Pairing, ...]:
    """Every pairing the contrast audit verifies."""
    return _load_pairings()[0]


def customisable_tokens() -> frozenset[str]:
    """The colour tokens an administrator may override, per PRD 28.2."""
    return _load_pairings()[1]


@lru_cache(maxsize=1)
def load_presets() -> dict[str, Preset]:
    """Load the shipped presets. Neither is marked as a default, per ADR-0002."""
    result: dict[str, Preset] = {}
    for path in sorted((TOKENS_ROOT / "presets").glob("*.json")):
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        preset = Preset(
            code=data["code"],
            name=data["name"],
            description=data["description"],
            source=data["source"],
            colour={str(key): str(value) for key, value in data["colour"].items()},
        )
        result[preset.code] = preset
    if not result:
        raise RuntimeError(f"No theme presets found in {TOKENS_ROOT / 'presets'}")
    return result


def get_preset(code: str) -> Preset:
    presets = load_presets()
    try:
        return presets[code]
    except KeyError as error:
        raise ValidationError(
            f"Unknown theme preset: {code!r}. Available: {sorted(presets)}",
            field="preset",
        ) from error


def audit(theme: ThemeDefinition) -> list[ContrastResult]:
    """Measure every pairing. Returns one result per pairing, in declared order."""
    return [
        check_pairing(
            theme.resolve(pairing.foreground),
            theme.resolve(pairing.background),
            required=pairing.required,
            label=pairing.label,
        )
        for pairing in pairings()
    ]


def failures(theme: ThemeDefinition) -> list[ContrastResult]:
    """Only the pairings that fail."""
    return [result for result in audit(theme) if not result.passes]


DERIVATIONS: dict[str, tuple[str, ...]] = {
    "accent-primary": (
        "accent-primary-hover",
        "accent-primary-active",
        "accent-primary-surface",
        "text-on-accent",
        "focus-ring",
        "chart-1",
        "text-link",
        "text-link-hover",
    ),
    "accent-secondary": (
        "accent-secondary-hover",
        "accent-secondary-surface",
        "text-on-accent-secondary",
    ),
    "surface-navigation": ("surface-navigation-hover", "surface-navigation-active", "text-inverse"),
}
"""Tokens derived from a customisable one.

An administrator who changes the accent does not also choose the shade of its hover
state or the colour of the text that sits on it. Deriving these keeps the palette
coherent, and is what makes a custom accent acceptable to the audit at all.
"""


def derive(token: str, value: str, surfaces: dict[str, str] | None = None) -> dict[str, str]:
    """Compute the tokens that follow from one override.

    ``surfaces`` carries the palette the override lands in, so a derived colour is
    measured against the surfaces it will actually sit on. Measuring against plain
    white is not enough: a canvas is usually a shade off white, and that shade is the
    harder of the two to read against.
    """
    palette = surfaces or {}
    card = palette.get("surface-raised", "#FFFFFF")
    canvas = palette.get("surface-canvas", "#FFFFFF")

    def legible_on_both(candidate: str, required: float) -> str:
        """Darken until the colour meets the requirement against card and canvas."""
        result = ensure_contrast(candidate, card, required)
        return ensure_contrast(result, canvas, required)

    if token == "accent-primary":
        link = legible_on_both(value, AA_NORMAL_TEXT)
        return {
            "accent-primary-hover": darken(value, 0.10),
            "accent-primary-active": darken(value, 0.20),
            "accent-primary-surface": tint(value, 0.90),
            "text-on-accent": readable_text_on(value),
            "focus-ring": darken(value, 0.20),
            "chart-1": legible_on_both(value, AA_NON_TEXT),
            # A link is body text, so it needs the full 4.5:1 rather than 3:1.
            "text-link": link,
            "text-link-hover": darken(link, 0.16),
        }
    if token == "accent-secondary":
        return {
            "accent-secondary-hover": darken(value, 0.10),
            "accent-secondary-surface": tint(value, 0.90),
            "text-on-accent-secondary": readable_text_on(value),
        }
    if token == "surface-navigation":
        return {
            "surface-navigation-hover": darken(value, -0.14),
            "surface-navigation-active": darken(value, -0.08),
            "text-inverse": readable_text_on(value),
        }
    return {}


def expand(overrides: dict[str, str], surfaces: dict[str, str] | None = None) -> dict[str, str]:
    """Apply every derivation, without overwriting an explicit choice."""
    palette = {**(surfaces or {}), **overrides}
    expanded: dict[str, str] = {}
    for token, value in overrides.items():
        for derived_token, derived_value in derive(token, value, palette).items():
            expanded.setdefault(derived_token, derived_value)
    expanded.update(overrides)
    return expanded


def validate_overrides(preset_code: str, overrides: dict[str, str]) -> ThemeDefinition:
    """Validate an administrator's overrides.

    Raises if a token is not customisable, is not a valid colour, or produces a
    pairing that fails WCAG AA. A failing combination is refused rather than warned
    about, per PRD 28.2.
    """
    allowed = customisable_tokens()
    unknown = set(overrides) - allowed
    if unknown:
        raise ValidationError(
            f"These colours cannot be customised: {sorted(unknown)}. "
            f"Customisable: {sorted(allowed)}",
            field="overrides",
        )

    from nickland.domain.colour import Colour

    normalised: dict[str, str] = {}
    for token, value in overrides.items():
        try:
            normalised[token] = Colour.parse(value).to_hex()
        except ValueError as error:
            raise ValidationError(str(error), field=token) from error

    # An accent is chosen; the shades and the text that sit on it are derived, and
    # measured against this preset's own surfaces.
    preset = get_preset(preset_code)
    theme = ThemeDefinition(preset=preset, overrides=expand(normalised, preset.colour))
    failed = failures(theme)
    if not failed:
        return theme

    remedies: list[str] = []
    for token, value in normalised.items():
        if (
            token in ("accent-primary", "accent-secondary", "surface-navigation")
            and round(best_text_ratio(value), 2) < 4.5
        ):
            suggestion = nearest_legible_shade(value)
            remedies.append(
                f"{value} is a mid-tone, so neither white nor charcoal text reaches "
                f"4.5:1 against it."
                + (f" The nearest shade that does is {suggestion}." if suggestion else "")
            )
    raise ValidationError(
        "These colour combinations are not legible enough to accept:\n"
        + "\n".join(f"  • {result.describe()}" for result in failed)
        + ("\n\n" + "\n".join(f"  {line}" for line in remedies) if remedies else ""),
        field="overrides",
        failures=[result.describe() for result in failed],
        remedies=remedies,
    )
