"""Theme endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from nickland.interface.dependencies import ActorDependency, ThemeDependency
from nickland.interface.schemas import (
    ApplyThemeContract,
    AuditLineReply,
    PresetReply,
    ThemePreviewReply,
    ThemeReply,
)

router = APIRouter(prefix="/appearance", tags=["appearance"])


@router.get("/presets", response_model=list[PresetReply], summary="List theme presets")
def presets(actor: ActorDependency, theme: ThemeDependency) -> list[PresetReply]:
    """Both shipped presets. Neither is marked as a default, per ADR-0002."""
    actor.require("business.view")
    return [PresetReply(**item) for item in theme.presets()]


@router.get("", response_model=ThemeReply, summary="Describe the active theme")
def active(actor: ActorDependency, theme: ThemeDependency) -> ThemeReply:
    return ThemeReply.model_validate(theme.active(actor))


@router.post("/preview", response_model=ThemePreviewReply, summary="Measure a proposed theme")
def preview(
    contract: ApplyThemeContract, actor: ActorDependency, theme: ThemeDependency
) -> ThemePreviewReply:
    """Measure every pairing of a proposed theme without saving it.

    The editor calls this as the administrator works, so a combination that would be
    refused is visible before it is submitted, per PRD 28.2.
    """
    view, lines = theme.preview(
        actor, preset_code=contract.preset_code, overrides=contract.overrides
    )
    measured = [AuditLineReply.model_validate(line) for line in lines]
    return ThemePreviewReply(
        theme=ThemeReply.model_validate(view),
        audit=measured,
        passes=all(line.passes for line in measured),
    )


@router.put("", response_model=ThemeReply, summary="Apply a theme")
def apply(
    contract: ApplyThemeContract, actor: ActorDependency, theme: ThemeDependency
) -> ThemeReply:
    """Save a theme. Refused if any pairing fails WCAG AA."""
    return ThemeReply.model_validate(
        theme.apply(
            actor,
            preset_code=contract.preset_code,
            overrides=contract.overrides,
            dark_mode_enabled=contract.dark_mode_enabled,
        )
    )


@router.post("/reset", response_model=ThemeReply, summary="Restore preset defaults")
def reset(actor: ActorDependency, theme: ThemeDependency) -> ThemeReply:
    return ThemeReply.model_validate(theme.reset(actor))
