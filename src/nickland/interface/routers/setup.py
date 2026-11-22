"""First-run provisioning endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from nickland.application.services.setup import SetupRequest
from nickland.interface.dependencies import (
    LocalGuard,
    RuntimeDependency,
    SetupDependency,
    verify_setup_code,
)
from nickland.interface.schemas import (
    PresetReply,
    SetupContract,
    SetupReply,
    SetupStatusReply,
)

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("", response_model=SetupStatusReply, summary="Report whether setup is required")
def status(
    setup: SetupDependency, runtime: RuntimeDependency, _local: LocalGuard
) -> SetupStatusReply:
    """Whether provisioning is outstanding, and the choices it offers.

    Both theme presets are returned as equals; no default is imposed, per ADR-0002.
    """
    from nickland.domain.identity import PasswordPolicy

    required = setup.is_required()
    return SetupStatusReply(
        setup_required=required,
        presets=[PresetReply(**item) for item in setup.available_presets()],
        password_requirements=PasswordPolicy().describe(),
        setup_code_required=required and runtime.setup_code is not None,
    )


@router.post("", response_model=SetupReply, status_code=201, summary="Provision the business")
def provision(
    contract: SetupContract,
    setup: SetupDependency,
    runtime: RuntimeDependency,
    _local: LocalGuard,
) -> SetupReply:
    """Create the business, its administrator and the seeded configuration.

    Runs once. A second attempt is refused. On a LAN host that issued a setup code, the
    request must quote it.
    """
    verify_setup_code(runtime, contract.setup_code)
    result = setup.provision(SetupRequest(**contract.model_dump(exclude={"setup_code"})))
    return SetupReply(
        business_id=result.business_id,
        branch_id=result.branch_id,
        administrator_id=result.administrator_id,
        roles_created=result.roles_created,
        sequences_created=result.sequences_created,
    )
