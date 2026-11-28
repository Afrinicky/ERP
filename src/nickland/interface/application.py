"""The FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from nickland.interface.dependencies import Runtime
from nickland.interface.errors import install_error_handlers
from nickland.interface.routers import (
    access,
    appearance,
    attachments,
    catalogue,
    command_centre,
    commissions,
    custom_fields,
    designer,
    dispatch,
    documents,
    expenses,
    financing,
    importing,
    inventory,
    journals,
    ledger,
    ledger_reports,
    orders,
    organisation,
    parties,
    planning,
    platform,
    pos,
    procurement,
    quotations,
    receivables,
    reconciliation,
    reporting,
    session,
    setup,
    sourcing,
    taxation,
    treasury,
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


class InterfaceFiles(StaticFiles):
    """The built interface, with its own routes served the page that renders them.

    The interface routes in the browser, so /sales or /settings/access exists only as a
    route inside index.html. Without the fallback, reloading such a page, or opening a
    link to it, finds no file and returns Not Found. A path that names a file, or one
    under the API, is still Not Found when absent.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            last = path.rsplit("/", 1)[-1]
            if error.status_code != 404 or path.startswith("api") or "." in last:
                raise
            return await super().get_response("index.html", scope)


SCHEDULE_INTERVAL_SECONDS = 60


BACKUP_CHECK_SECONDS = 900


async def _run_report_schedules(runtime: Runtime) -> None:
    """Scheduled reports, generated in the background and delivered to inboxes.

    Each pass is one unit of work in a worker thread, so a slow report never holds up a
    request, and a failure is logged and tried again on the next pass.
    """
    from nickland.application.services.report_runs import run_schedules
    from nickland.application.workers import beat, register
    from nickland.persistence.engine import unit_of_work

    def one_pass() -> int:
        with unit_of_work(runtime.session_factory) as session:
            return run_schedules(session, clock=runtime.clock)

    register("Scheduled reports", SCHEDULE_INTERVAL_SECONDS)
    await asyncio.sleep(15)
    while True:
        try:
            produced = await asyncio.to_thread(one_pass)
            beat("Scheduled reports", runtime.clock.now())
            if produced:
                logger.info("Scheduled reports generated: %d", produced)
        except Exception as error:
            beat("Scheduled reports", runtime.clock.now(), error=str(error)[:300])
            logger.exception("Scheduled reports could not be generated")
        await asyncio.sleep(SCHEDULE_INTERVAL_SECONDS)


async def _run_backups(runtime: Runtime) -> None:
    """A verified backup whenever the last is older than the configured interval."""
    from nickland.application.workers import beat, register

    register("Scheduled backups", BACKUP_CHECK_SECONDS)
    await asyncio.sleep(60)
    while True:
        try:
            taken = await asyncio.to_thread(_backup_if_due, runtime)
            beat("Scheduled backups", runtime.clock.now())
            if taken:
                logger.info("Scheduled backup taken")
        except Exception as error:
            beat("Scheduled backups", runtime.clock.now(), error=str(error)[:300])
            logger.exception("Scheduled backup failed")
        await asyncio.sleep(BACKUP_CHECK_SECONDS)


def _backup_if_due(runtime: Runtime) -> bool:
    from datetime import timedelta

    from sqlalchemy import select

    from nickland.application.context import SystemActor
    from nickland.application.services.backup import BackupService
    from nickland.persistence.engine import unit_of_work
    from nickland.persistence.models import Business

    settings = runtime.settings
    if not settings.backup_enabled or settings.backup_interval_hours <= 0:
        return False
    with unit_of_work(runtime.session_factory) as session:
        business = session.scalar(select(Business.id))
        if business is None:
            return False
        actor = SystemActor(business_id=business, reason="Scheduled backup")
        service = BackupService(session, runtime.engine, settings, clock=runtime.clock)
        latest = service.latest(actor)
        due = latest is None or runtime.clock.now() - latest.started_at >= timedelta(
            hours=settings.backup_interval_hours
        )
        if due:
            service.run(actor, trigger="scheduled")
        return due


SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


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
        runners = (
            [
                asyncio.create_task(_run_report_schedules(resolved_runtime)),
                asyncio.create_task(_run_backups(resolved_runtime)),
            ]
            if active.environment is not Environment.TEST
            else []
        )
        yield
        for runner in runners:
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner
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

        @application.middleware("http")
        async def secure(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            # Served to the network: the browser is told to keep to TLS, never to frame
            # the application, and never to keep a page of business records in its cache.
            response = await call_next(request)
            if request.url.path.startswith("/api/"):
                for name, value in SECURITY_HEADERS.items():
                    response.headers.setdefault(name, value)
            else:
                response.headers.setdefault("X-Frame-Options", "DENY")
                response.headers.setdefault("X-Content-Type-Options", "nosniff")
            return response

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
        access.router,
        organisation.router,
        appearance.router,
        custom_fields.router,
        documents.router,
        catalogue.router,
        parties.router,
        designer.router,
        importing.router,
        sourcing.router,
        quotations.router,
        orders.router,
        procurement.router,
        inventory.router,
        dispatch.router,
        pos.router,
        attachments.router,
        commissions.router,
        ledger.router,
        journals.router,
        ledger_reports.router,
        treasury.router,
        receivables.router,
        expenses.router,
        financing.router,
        planning.router,
        reporting.router,
        reconciliation.router,
        command_centre.router,
        taxation.router,
        platform.router,
    ):
        application.include_router(router, prefix=API_PREFIX)

    if static_root is not None and static_root.is_dir():
        application.mount("/", InterfaceFiles(directory=static_root, html=True), name="interface")
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
