"""First-run provisioning endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from nickland.application.services.setup import SetupRequest
from nickland.interface.dependencies import LocalGuard, SetupDependency
from nickland.interface.schemas import (
    PresetReply,
    SetupContract,
    SetupReply,
    SetupStatusReply,
)

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("", response_model=SetupStatusReply, summary="Report whether setup is required")
def status(setup: SetupDependency, _local: LocalGuard) -> SetupStatusReply:
    """Whether provisioning is outstanding, and the choices it offers.

    Both theme presets are returned as equals; no default is imposed, per ADR-0002.
    """
    from nickland.domain.identity import PasswordPolicy

    return SetupStatusReply(
        setup_required=setup.is_required(),
        presets=[PresetReply(**item) for item in setup.available_presets()],
        password_requirements=PasswordPolicy().describe(),
    )


@router.post("", response_model=SetupReply, status_code=201, summary="Provision the business")
def provision(
    contract: SetupContract, setup: SetupDependency, _local: LocalGuard
) -> SetupReply:
    """Create the business, its administrator and the seeded configuration.

    Runs once. A second attempt is refused.
    """
    result = setup.provision(SetupRequest(**contract.model_dump()))
    return SetupReply(
        business_id=result.business_id,
        branch_id=result.branch_id,
        administrator_id=result.administrator_id,
        roles_created=result.roles_created,
        sequences_created=result.sequences_created,
    )


