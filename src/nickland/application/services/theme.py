"""Theme selection and customisation.

Two presets ship as equals and no default is imposed, per ADR-0002. An override is
validated against WCAG AA before it is accepted; a failing combination is refused
rather than warned about, per PRD 28.2.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditAction, AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import NotFoundError
from nickland.domain.theme import (
    ThemeDefinition,
    customisable_tokens,
    get_preset,
    load_presets,
    validate_overrides,
)
from nickland.domain.theme import (
    audit as audit_theme,
)
from nickland.persistence.models import ThemeProfile
from nickland.platform.clock import Clock, SystemClock


@dataclass(frozen=True, slots=True)
class ThemeView:
    """The active theme, as the interface consumes it."""

    preset_code: str
    preset_name: str
    preset_source: str
    colour: dict[str, str]
    overrides: dict[str, str]
    customisable: list[str]
    dark_mode_enabled: bool


@dataclass(frozen=True, slots=True)
class AuditLine:
    """One pairing's measurement, for the theme editor's preview."""

    label: str
    foreground: str
    background: str
    ratio: float
    required: float
    passes: bool


class ThemeService:
    """Theme selection, customisation and the contrast audit."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    def presets(self) -> list[dict[str, str]]:
        """The shipped presets. Neither is marked as a default."""
        return [
            {
                "code": preset.code,
                "name": preset.name,
                "description": preset.description,
                "source": preset.source,
            }
            for preset in load_presets().values()
        ]

    def active(self, actor: AnyActor) -> ThemeView:
        """The theme in force, with any overrides applied."""
        profile = self._require_profile(actor)
        definition = ThemeDefinition(
            preset=get_preset(profile.preset_code),
            overrides=dict(profile.overrides or {}),
        )
        return ThemeView(
            preset_code=definition.preset.code,
            preset_name=definition.preset.name,
            preset_source=definition.preset.source,
            colour=definition.colour,
            overrides=dict(definition.overrides),
            customisable=sorted(customisable_tokens()),
            dark_mode_enabled=profile.dark_mode_enabled,
        )

    def preview(
        self, actor: AnyActor, *, preset_code: str, overrides: dict[str, str]
    ) -> tuple[ThemeView, list[AuditLine]]:
        """Measure a proposed theme without saving it.

        The editor calls this as the administrator works, so a failing combination is
        visible before it is submitted, per PRD 28.2.
        """
        actor.require("theme.manage")
        definition = ThemeDefinition(preset=get_preset(preset_code), overrides=dict(overrides))
        lines = [
            AuditLine(
                label=result.label,
                foreground=result.foreground,
                background=result.background,
                ratio=round(result.ratio, 2),
                required=result.required,
                passes=result.passes,
            )
            for result in audit_theme(definition)
        ]
        view = ThemeView(
            preset_code=definition.preset.code,
            preset_name=definition.preset.name,
            preset_source=definition.preset.source,
            colour=definition.colour,
            overrides=dict(definition.overrides),
            customisable=sorted(customisable_tokens()),
            dark_mode_enabled=False,
        )
        return view, lines

    def apply(
        self,
        actor: AnyActor,
        *,
        preset_code: str,
        overrides: dict[str, str] | None = None,
        dark_mode_enabled: bool | None = None,
    ) -> ThemeView:
        """Save a theme. Raises if any pairing fails WCAG AA."""
        actor.require("theme.manage")
        profile = self._require_profile(actor)
        before = {
            "preset_code": profile.preset_code,
            "overrides": str(profile.overrides),
        }

        definition = validate_overrides(preset_code, overrides or {})
        profile.preset_code = definition.preset.code
        profile.overrides = dict(definition.overrides)
        profile.audit_passed_at = self._clock.now()
        if dark_mode_enabled is not None:
            profile.dark_mode_enabled = dark_mode_enabled

        self._audit.record(
            actor,
            AuditAction.THEME_AMENDED,
            entity_type="theme_profile",
            entity_id=profile.id,
            entity_label=definition.preset.name,
            before=before,
            after={"preset_code": profile.preset_code, "overrides": str(profile.overrides)},
        )
        return self.active(actor)

    def reset(self, actor: AnyActor) -> ThemeView:
        """Restore the selected preset's published defaults, per PRD 28.2."""
        actor.require("theme.manage")
        profile = self._require_profile(actor)
        before = {"overrides": str(profile.overrides)}
        profile.overrides = {}
        profile.audit_passed_at = self._clock.now()

        self._audit.record(
            actor,
            AuditAction.THEME_RESET,
            entity_type="theme_profile",
            entity_id=profile.id,
            entity_label=profile.preset_code,
            before=before,
            after={"overrides": "{}"},
        )
        return self.active(actor)

    def _require_profile(self, actor: AnyActor) -> ThemeProfile:
        profile = self._db.scalar(
            select(ThemeProfile).where(ThemeProfile.business_id == actor.business_id)
        )
        if profile is None:
            raise NotFoundError("No theme profile configured for this business.")
        return profile
