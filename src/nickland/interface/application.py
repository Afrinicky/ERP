"""The FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from nickland.interface.dependencies import Runtime
from nickland.interface.errors import install_error_handlers
from nickland.interface.routers import (
    appearance,
    catalogue,
    commissions,
    custom_fields,
    documents,
    importing,
    journals,
    ledger,
    ledger_reports,
    organisation,
    parties,
    platform,
    quotations,
    session,
    setup,
    sourcing,
    taxation,
    users,
)
from nickland.persistence.engine import create_database_engine, create_session_factory
from nickland.platform.clock import SystemClock
from nickland.platform.config import DeploymentMode, Environment, Settings, get_settings
from nickland.platform.logging import configure_logging, get_logger

logger = get_logger(__name__)

API_PREFIX = "/api/v1"

DESCRIPTION = """
Local application service for Nickland ERP.

Business rules reside in Python domain and application services. This interface routes
requests, validates contracts and translates domain errors; it holds no business rule.

Authorisation is enforced in the application layer. The permission list returned by
`/session/current` lets the interface hide controls, which is a convenience and never a
control in itself.
""".strip()


def build_runtime(settings: Settings | None = None, *, local_token: str | None = None) -> Runtime:
    """Construct the process-wide runtime."""
    resolved = settings or get_settings()
    resolved.ensure_directories()
    configure_logging(
        level=resolved.log_level,
        directory=resolved.log_directory,
        as_json=resolved.log_json,
    )
    engine = create_database_engine(resolved, echo=False)
    return Runtime(
        settings=resolved,
        engine=engine,
        session_factory=create_session_factory(engine),
        clock=SystemClock(),
        local_token=local_token,
    )


def create_application(
    settings: Settings | None = None,
    *,
    local_token: str | None = None,
    runtime: Runtime | None = None,
    static_root: Path | None = None,
) -> FastAPI:
    """Build the application.

    ``static_root`` serves the built interface from the same origin as the API, so the
    desktop host needs no second server and no cross-origin configuration.
    """
    resolved_runtime = runtime or build_runtime(settings, local_token=local_token)
    active = resolved_runtime.settings

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.runtime = resolved_runtime
        logger.info(
            "Service starting: %s mode, %s environment",
            active.deployment_mode.value,
            active.environment.value,
        )
        _apply_staged_restore(active)
        yield
        resolved_runtime.engine.dispose()
        logger.info("Service stopped")

    application = FastAPI(
        title="Nickland ERP",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if active.environment is not Environment.PRODUCTION else None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    application.state.runtime = resolved_runtime

    if active.deployment_mode is DeploymentMode.LAN_HOST:
        # Clients are served from the host, so only the host origin is permitted.
        application.add_middleware(
            CORSMiddleware,
            allow_origins=[],
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["*"],
        )

    install_error_handlers(application)

    for router in (
        setup.router,
        session.router,
        users.router,
        organisation.router,
        appearance.router,
        custom_fields.router,
        documents.router,
        catalogue.router,
        parties.router,
        importing.router,
        sourcing.router,
        quotations.router,
        commissions.router,
        ledger.router,
        journals.router,
        ledger_reports.router,
        taxation.router,
        platform.router,
    ):
        application.include_router(router, prefix=API_PREFIX)

    if static_root is not None and static_root.is_dir():
        application.mount("/", StaticFiles(directory=static_root, html=True), name="interface")
        logger.info("Serving interface from %s", static_root)

    return application


def _apply_staged_restore(settings: Settings) -> None:
    """Apply a restore staged by the backup service, before anything opens the database.

    Doing this at start-up rather than on request is deliberate: swapping a database
    file while connections hold it open risks a partially written result.
    """
    staging = settings.data_directory / "restore-pending"
    candidate = staging / "database" / "nickland.db"
    if not candidate.exists():
        return

    import shutil
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    superseded = settings.data_directory / f"nickland-superseded-{stamp}.db"
    if settings.database_file.exists():
        shutil.move(str(settings.database_file), str(superseded))
    shutil.move(str(candidate), str(settings.database_file))

    restored_attachments = staging / "attachments"
    if restored_attachments.is_dir():
        target = settings.attachment_directory
        if target.exists():
            shutil.move(str(target), str(target.with_name(f"attachments-superseded-{stamp}")))
        shutil.move(str(restored_attachments), str(target))

    shutil.rmtree(staging, ignore_errors=True)
    logger.warning("Restore applied. The superseded database is retained at %s", superseded.name)
