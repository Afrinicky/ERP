"""Command-centre endpoints: measures, their lines, revenue breakdowns and layouts."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from nickland.application.services.command_centre import Layout, MeasureValue
from nickland.domain.finance import LAYOUT_LABELS, LAYOUT_PRESETS
from nickland.interface.dependencies import ActorDependency, CommandCentreDependency
from nickland.interface.finance_schemas import (
    BreakdownReply,
    DrillLineReply,
    DrillReply,
    LayoutContract,
    LayoutReply,
    MeasureReply,
    PresetReply,
)

router = APIRouter(prefix="/command-centre", tags=["command centre"])


def _measure(item: MeasureValue) -> MeasureReply:
    return MeasureReply(
        code=item.definition.code,
        label=item.definition.label,
        basis=item.definition.basis.value,
        description=item.definition.description,
        value=item.value,
        starts_on=item.starts_on,
        ends_on=item.ends_on,
    )


def _layout(layout: Layout) -> LayoutReply:
    return LayoutReply(preset=layout.preset, widgets=layout.widgets, source=layout.source)


@router.get("/measures", response_model=list[MeasureReply], summary="Headline measures")
def measures(
    actor: ActorDependency, centre: CommandCentreDependency, start: date, end: date
) -> list[MeasureReply]:
    return [_measure(item) for item in centre.measures(actor, start=start, end=end)]


@router.get("/measures/{code}/lines", response_model=DrillReply, summary="A measure's lines")
def drill(
    code: str, actor: ActorDependency, centre: CommandCentreDependency, start: date, end: date
) -> DrillReply:
    found = centre.drill(actor, code, start=start, end=end)
    return DrillReply(
        measure=_measure(found.measure),
        lines=[DrillLineReply.model_validate(line) for line in found.lines],
        total=found.total,
        truncated=found.truncated,
    )


@router.get(
    "/revenue/{dimension}", response_model=list[BreakdownReply], summary="Revenue by dimension"
)
def revenue(
    dimension: str, actor: ActorDependency, centre: CommandCentreDependency, start: date, end: date
) -> list[BreakdownReply]:
    return [
        BreakdownReply.model_validate(row)
        for row in centre.revenue_by(actor, dimension, start=start, end=end)
    ]


@router.get("/layout", response_model=LayoutReply, summary="My layout")
def layout(actor: ActorDependency, centre: CommandCentreDependency) -> LayoutReply:
    return _layout(centre.layout(actor))


@router.put("/layout", response_model=LayoutReply, summary="Save a layout")
def save_layout(
    body: LayoutContract, actor: ActorDependency, centre: CommandCentreDependency
) -> LayoutReply:
    return _layout(
        centre.save_layout(
            actor,
            [item.model_dump() for item in body.widgets],
            preset=body.preset,
            role_code=body.role_code,
        )
    )


@router.post("/layout/reset", response_model=LayoutReply, summary="Return to the default")
def reset_layout(actor: ActorDependency, centre: CommandCentreDependency) -> LayoutReply:
    return _layout(centre.reset_layout(actor))


@router.get("/presets", response_model=list[PresetReply], summary="Supplied layouts")
def presets(actor: ActorDependency, centre: CommandCentreDependency) -> list[PresetReply]:
    actor.require("report.view")
    return [
        PresetReply(code=code, label=LAYOUT_LABELS[code], widgets=centre.preset(code).widgets)
        for code in LAYOUT_PRESETS
    ]
