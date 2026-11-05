"""Business configuration endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from nickland.domain.organisation import DOCUMENT_KIND_LABELS, DocumentKind
from nickland.interface.dependencies import ActorDependency, OrganisationDependency
from nickland.interface.schemas import (
    AmendBusinessContract,
    AmendSequenceContract,
    BranchReply,
    BusinessReply,
    CreateBranchContract,
    CreateWarehouseContract,
    SequenceReply,
    WarehouseReply,
)

router = APIRouter(tags=["organisation"])


@router.get("/business", response_model=BusinessReply, summary="Describe the business")
def get_business(
    actor: ActorDependency, organisation: OrganisationDependency
) -> BusinessReply:
    return BusinessReply.model_validate(organisation.get_business(actor))


@router.patch("/business", response_model=BusinessReply, summary="Amend the business")
def amend_business(
    contract: AmendBusinessContract,
    actor: ActorDependency,
    organisation: OrganisationDependency,
) -> BusinessReply:
    changes = contract.model_dump(exclude_unset=True)
    return BusinessReply.model_validate(organisation.amend_business(actor, changes))


@router.get("/branches", response_model=list[BranchReply], summary="List branches")
def list_branches(
    actor: ActorDependency,
    organisation: OrganisationDependency,
    include_inactive: bool = False,
) -> list[BranchReply]:
    return [
        BranchReply.model_validate(branch)
        for branch in organisation.list_branches(actor, include_inactive=include_inactive)
    ]


@router.post("/branches", response_model=BranchReply, status_code=201, summary="Add a branch")
def create_branch(
    contract: CreateBranchContract,
    actor: ActorDependency,
    organisation: OrganisationDependency,
) -> BranchReply:
    return BranchReply.model_validate(
        organisation.create_branch(actor, **contract.model_dump())
    )


@router.get("/warehouses", response_model=list[WarehouseReply], summary="List warehouses")
def list_warehouses(
    actor: ActorDependency, organisation: OrganisationDependency
) -> list[WarehouseReply]:
    return [
        WarehouseReply.model_validate(warehouse)
        for warehouse in organisation.list_warehouses(actor)
    ]


@router.post(
    "/warehouses", response_model=WarehouseReply, status_code=201, summary="Add a warehouse"
)
def create_warehouse(
    contract: CreateWarehouseContract,
    actor: ActorDependency,
    organisation: OrganisationDependency,
) -> WarehouseReply:
    return WarehouseReply.model_validate(
        organisation.create_warehouse(actor, **contract.model_dump())
    )


def _sequence_reply(
    actor: ActorDependency, organisation: OrganisationDependency, sequence: object
) -> SequenceReply:
    kind = DocumentKind(sequence.document_kind)  # type: ignore[attr-defined]
    label, _ = DOCUMENT_KIND_LABELS[kind]
    reply = SequenceReply.model_validate(sequence)
    reply.document_label = label
    reply.preview = organisation.preview_number(actor, kind)
    return reply


@router.get(
    "/numbering", response_model=list[SequenceReply], summary="List numbering sequences"
)
def list_sequences(
    actor: ActorDependency, organisation: OrganisationDependency
) -> list[SequenceReply]:
    """Every sequence, with a preview of the next number it would issue."""
    return [
        _sequence_reply(actor, organisation, sequence)
        for sequence in organisation.list_sequences(actor)
    ]


@router.patch(
    "/numbering/{document_kind}",
    response_model=SequenceReply,
    summary="Amend a numbering sequence",
)
def amend_sequence(
    document_kind: DocumentKind,
    contract: AmendSequenceContract,
    actor: ActorDependency,
    organisation: OrganisationDependency,
) -> SequenceReply:
    """Amend a sequence. The next number may be raised but never lowered."""
    sequence = organisation.amend_sequence(
        actor, document_kind, **contract.model_dump(exclude_unset=True)
    )
    return _sequence_reply(actor, organisation, sequence)


@router.get(
    "/numbering/{document_kind}/preview",
    response_model=str,
    summary="Preview a numbering pattern",
)
def preview_number(
    document_kind: DocumentKind,
    actor: ActorDependency,
    organisation: OrganisationDependency,
    pattern: str | None = None,
    prefix: str | None = None,
    branch_id: uuid.UUID | None = None,
) -> str:
    """Render what a pattern would produce, without allocating a number."""
    return organisation.preview_number(
        actor, document_kind, pattern=pattern, prefix=prefix, branch_id=branch_id
    )
