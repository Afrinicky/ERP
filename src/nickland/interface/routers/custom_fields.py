"""Custom-field endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from nickland.domain.organisation import CustomFieldTarget, CustomFieldType
from nickland.interface.dependencies import ActorDependency, CustomFieldDependency
from nickland.interface.schemas import (
    AmendCustomFieldContract,
    CustomFieldReply,
    DefineCustomFieldContract,
    WithdrawContract,
)

router = APIRouter(prefix="/custom-fields", tags=["custom fields"])


@router.get("/targets", response_model=dict[str, list[str]], summary="List targets and types")
def catalogue(actor: ActorDependency) -> dict[str, list[str]]:
    """The records that accept custom fields, and the types available."""
    actor.require("business.view")
    return {
        "targets": [item.value for item in CustomFieldTarget],
        "types": [item.value for item in CustomFieldType],
    }


@router.get(
    "/{target}", response_model=list[CustomFieldReply], summary="List fields on a record type"
)
def definitions(
    target: CustomFieldTarget,
    actor: ActorDependency,
    fields: CustomFieldDependency,
    include_inactive: bool = False,
) -> list[CustomFieldReply]:
    return [
        CustomFieldReply.model_validate(item)
        for item in fields.definitions(actor, target, include_inactive=include_inactive)
    ]


@router.post("", response_model=CustomFieldReply, status_code=201, summary="Define a field")
def define(
    contract: DefineCustomFieldContract,
    actor: ActorDependency,
    fields: CustomFieldDependency,
) -> CustomFieldReply:
    return CustomFieldReply.model_validate(fields.define(actor, **contract.model_dump()))


@router.patch("/{definition_id}", response_model=CustomFieldReply, summary="Amend a field")
def amend(
    definition_id: uuid.UUID,
    contract: AmendCustomFieldContract,
    actor: ActorDependency,
    fields: CustomFieldDependency,
) -> CustomFieldReply:
    """Amend a field. Its code and type are fixed once it exists."""
    return CustomFieldReply.model_validate(
        fields.amend(actor, definition_id, contract.model_dump(exclude_unset=True))
    )


@router.post(
    "/{definition_id}/withdraw", response_model=CustomFieldReply, summary="Withdraw a field"
)
def withdraw(
    definition_id: uuid.UUID,
    contract: WithdrawContract,
    actor: ActorDependency,
    fields: CustomFieldDependency,
) -> CustomFieldReply:
    """Deactivate a field. Captured values are retained."""
    return CustomFieldReply.model_validate(
        fields.withdraw(actor, definition_id, reason=contract.reason)
    )
