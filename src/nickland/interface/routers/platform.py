"""Health, backup and audit endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from nickland.interface.dependencies import (
    ActorDependency,
    BackupDependency,
    HealthDependency,
    LocalGuard,
    SessionDependency,
)
from nickland.interface.schemas import (
    AuditEventReply,
    BackupReply,
    CheckReply,
    HealthReply,
    PageReply,
    RestoreContract,
    RunBackupContract,
)
from nickland.persistence.models import AuditEvent

router = APIRouter(tags=["platform"])


@router.get("/health", response_model=HealthReply, summary="Report system health")
def health(health_service: HealthDependency, _local: LocalGuard) -> HealthReply:
    """Service, database, schema, storage, network and worker state.

    Unauthenticated, so the status strip can report a problem that prevents sign-in.
    The backup check is omitted here, because it is business-scoped.
    """
    report = health_service.report()
    return HealthReply(
        state=report.state.value,
        deployment_mode=report.deployment_mode,
        checks=[
            CheckReply(
                name=check.name,
                state=check.state.value,
                detail=check.detail,
                measurements=check.measurements,
            )
            for check in report.checks
        ],
    )


@router.get("/health/detail", response_model=HealthReply, summary="Full health report")
def health_detail(actor: ActorDependency, health_service: HealthDependency) -> HealthReply:
    """The full report, including the backup position."""
    actor.require("health.view")
    report = health_service.report(actor)
    return HealthReply(
        state=report.state.value,
        deployment_mode=report.deployment_mode,
        checks=[
            CheckReply(
                name=check.name,
                state=check.state.value,
                detail=check.detail,
                measurements=check.measurements,
            )
            for check in report.checks
        ],
    )


@router.get("/backups", response_model=list[BackupReply], summary="List backups")
def list_backups(
    actor: ActorDependency, backup: BackupDependency, limit: int = 30
) -> list[BackupReply]:
    return [BackupReply.model_validate(item) for item in backup.history(actor, limit)]


@router.post("/backups", response_model=BackupReply, status_code=201, summary="Take a backup")
def run_backup(
    contract: RunBackupContract,
    actor: ActorDependency,
    backup: BackupDependency,
    db: SessionDependency,
) -> BackupReply:
    """Take and verify a backup of the database and attachment store."""
    outcome = backup.run(actor, trigger="manual", include_attachments=contract.include_attachments)
    db.flush()
    from nickland.persistence.models import BackupRecord

    record = db.get(BackupRecord, outcome.record_id)
    return BackupReply.model_validate(record)


@router.post("/backups/restore", response_model=dict[str, str], summary="Stage a restore")
def restore(
    contract: RestoreContract, actor: ActorDependency, backup: BackupDependency
) -> dict[str, str]:
    """Stage a restore. The swap is applied at the next application start."""
    staged = backup.restore(
        actor,
        contract.archive_name,
        reason=contract.reason,
        confirm=contract.confirm,
    )
    return {
        "status": "staged",
        "detail": "The restore is staged and will be applied when the application next starts.",
        "path": str(staged),
    }


@router.get("/audit", response_model=PageReply, summary="Read the audit trail")
def audit_trail(
    actor: ActorDependency,
    db: SessionDependency,
    entity_type: str | None = None,
    action: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PageReply:
    """The audit trail, newest first. Read-only; the store is append-only."""
    actor.require("audit.view")
    conditions = [AuditEvent.business_id == actor.business_id]
    if entity_type:
        conditions.append(AuditEvent.entity_type == entity_type)
    if action:
        conditions.append(AuditEvent.action == action)

    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*conditions)) or 0
    events = db.scalars(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return PageReply(
        items=[AuditEventReply.model_validate(event) for event in events],
        total=total,
        offset=offset,
        limit=limit,
    )
