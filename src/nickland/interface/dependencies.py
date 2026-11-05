"""Request dependencies: the unit of work, the actor and the service container.

Authentication happens here. Authorisation happens in the application layer.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from nickland.application.context import Actor
from nickland.application.services.backup import BackupService
from nickland.application.services.catalogue import CatalogueService
from nickland.application.services.custom_fields import CustomFieldService
from nickland.application.services.health import HealthService
from nickland.application.services.identity import IdentityService
from nickland.application.services.importing import ImportService
from nickland.application.services.organisation import OrganisationService
from nickland.application.services.parties import PartyService
from nickland.application.services.pricing_lists import PriceListService
from nickland.application.services.setup import SetupService
from nickland.application.services.theme import ThemeService
from nickland.application.services.units import UnitService
from nickland.domain.errors import AuthenticationError, ConflictError
from nickland.domain.identity import LockoutPolicy, PasswordPolicy, SessionPolicy
from nickland.persistence.models import Business
from nickland.platform.clock import Clock
from nickland.platform.config import Settings

SESSION_HEADER = "X-Nickland-Session"
LOCAL_HEADER = "X-Nickland-Local-Token"


@dataclass(slots=True)
class Runtime:
    """Process-wide objects, attached to the application at start-up."""

    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    clock: Clock
    local_token: str | None = None

    @property
    def password_policy(self) -> PasswordPolicy:
        return PasswordPolicy(minimum_length=self.settings.password_minimum_length)

    @property
    def lockout_policy(self) -> LockoutPolicy:
        from datetime import timedelta

        return LockoutPolicy(
            threshold=self.settings.lockout_threshold,
            duration=timedelta(minutes=self.settings.lockout_minutes),
        )

    @property
    def session_policy(self) -> SessionPolicy:
        from datetime import timedelta

        return SessionPolicy(
            idle=timedelta(minutes=self.settings.session_idle_minutes),
            absolute=timedelta(hours=self.settings.session_absolute_hours),
        )


def get_runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDependency = Annotated[Runtime, Depends(get_runtime)]


def get_unit_of_work(runtime: RuntimeDependency) -> Iterator[Session]:
    """One transaction per request.

    Commits when the handler returns, rolls back on any exception, so a failed
    operation leaves no partial write.
    """
    session = runtime.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


SessionDependency = Annotated[Session, Depends(get_unit_of_work)]


def verify_local_token(
    runtime: RuntimeDependency,
    local_token: Annotated[str | None, Header(alias=LOCAL_HEADER)] = None,
) -> None:
    """Reject a request not bearing the token issued by the desktop host.

    The local service binds to loopback and accepts only this token, so another
    process on the machine cannot drive the application.
    """
    if runtime.local_token is None:
        return
    if local_token != runtime.local_token:
        raise AuthenticationError("This request did not come from the application.")


LocalGuard = Annotated[None, Depends(verify_local_token)]


def get_identity_service(session: SessionDependency, runtime: RuntimeDependency) -> IdentityService:
    return IdentityService(
        session,
        clock=runtime.clock,
        password_policy=runtime.password_policy,
        lockout_policy=runtime.lockout_policy,
        session_policy=runtime.session_policy,
    )


IdentityDependency = Annotated[IdentityService, Depends(get_identity_service)]


def get_actor(
    identity: IdentityDependency,
    session_token: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
    user_agent: Annotated[str | None, Header()] = None,
) -> Actor:
    """Resolve the authenticated actor, or refuse the request."""
    if not session_token:
        raise AuthenticationError("Sign in to continue.")
    return identity.resolve_session(session_token, device=user_agent)


ActorDependency = Annotated[Actor, Depends(get_actor)]


def get_setup_service(session: SessionDependency, runtime: RuntimeDependency) -> SetupService:
    return SetupService(session, clock=runtime.clock)


def get_organisation_service(
    session: SessionDependency, runtime: RuntimeDependency
) -> OrganisationService:
    return OrganisationService(session, clock=runtime.clock)


def get_theme_service(session: SessionDependency, runtime: RuntimeDependency) -> ThemeService:
    return ThemeService(session, clock=runtime.clock)


def get_custom_field_service(
    session: SessionDependency, runtime: RuntimeDependency
) -> CustomFieldService:
    return CustomFieldService(session, clock=runtime.clock)


def get_backup_service(session: SessionDependency, runtime: RuntimeDependency) -> BackupService:
    return BackupService(session, runtime.engine, runtime.settings, clock=runtime.clock)


def get_health_service(session: SessionDependency, runtime: RuntimeDependency) -> HealthService:
    return HealthService(session, runtime.engine, runtime.settings, clock=runtime.clock)


SetupDependency = Annotated[SetupService, Depends(get_setup_service)]
OrganisationDependency = Annotated[OrganisationService, Depends(get_organisation_service)]
ThemeDependency = Annotated[ThemeService, Depends(get_theme_service)]
CustomFieldDependency = Annotated[CustomFieldService, Depends(get_custom_field_service)]
BackupDependency = Annotated[BackupService, Depends(get_backup_service)]
HealthDependency = Annotated[HealthService, Depends(get_health_service)]


def sole_business_id(session: Session) -> uuid.UUID:
    """The identifier of the single configured business.

    The interface exposes one business, per ADR-0003. Sign-in therefore need not ask
    which business is meant. When multi-business operation is activated in Phase 16,
    sign-in takes the business explicitly and this helper is withdrawn.
    """
    identifiers = list(session.scalars(select(Business.id).limit(2)))
    if not identifiers:
        raise ConflictError("This installation has not been set up.")
    if len(identifiers) > 1:
        raise ConflictError(
            "More than one business is configured. Multi-business sign-in is not "
            "enabled in this release."
        )
    return identifiers[0]


def get_unit_service(session: SessionDependency, runtime: RuntimeDependency) -> UnitService:
    return UnitService(session, clock=runtime.clock)


def get_catalogue_service(
    session: SessionDependency, runtime: RuntimeDependency
) -> CatalogueService:
    return CatalogueService(session, clock=runtime.clock)


def get_party_service(session: SessionDependency, runtime: RuntimeDependency) -> PartyService:
    return PartyService(session, clock=runtime.clock)


def get_price_list_service(
    session: SessionDependency, runtime: RuntimeDependency
) -> PriceListService:
    return PriceListService(session, clock=runtime.clock)


def get_import_service(session: SessionDependency, runtime: RuntimeDependency) -> ImportService:
    return ImportService(session, clock=runtime.clock)


UnitDependency = Annotated[UnitService, Depends(get_unit_service)]
CatalogueDependency = Annotated[CatalogueService, Depends(get_catalogue_service)]
PartyDependency = Annotated[PartyService, Depends(get_party_service)]
PriceListDependency = Annotated[PriceListService, Depends(get_price_list_service)]
ImportDependency = Annotated[ImportService, Depends(get_import_service)]
