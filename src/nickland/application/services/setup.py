"""First-run provisioning.

Administrator provisioning is forced before any business data may be entered, per
PRD 9.6. The business, its primary branch, a default warehouse, the eight seeded
roles, the numbering sequences and the chosen theme are created in one transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditAction, AuditRecorder
from nickland.application.context import SystemActor
from nickland.application.services.identity import IdentityService
from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.identity import validate_username
from nickland.domain.organisation import (
    DEFAULT_PATTERN,
    DOCUMENT_KIND_LABELS,
    DocumentKind,
    ResetCadence,
    validate_code,
)
from nickland.domain.permissions import SEEDED_ROLES
from nickland.domain.theme import get_preset, load_presets
from nickland.persistence.models import (
    Branch,
    Business,
    NumberSequence,
    Role,
    RolePermission,
    ThemeProfile,
    User,
    Warehouse,
)
from nickland.platform.clock import Clock, SystemClock
from nickland.platform.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class SetupRequest:
    """Everything the first-run wizard collects."""

    business_name: str
    administrator_username: str
    administrator_full_name: str
    administrator_password: str
    theme_preset: str

    legal_name: str | None = None
    tax_identifier: str | None = None
    registration_number: str | None = None
    email: str | None = None
    telephone: str | None = None
    website: str | None = None

    base_currency: str = "GHS"
    currency_symbol: str = "GH₵"
    currency_precision: int = 2
    timezone: str = "Africa/Accra"
    financial_year_start_month: int = 1

    branch_code: str = "main"
    branch_name: str = "Head office"
    warehouse_code: str = "main"
    warehouse_name: str = "Main store"

    administrator_email: str | None = None
    extra_branches: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SetupResult:
    """What provisioning created."""

    business_id: uuid.UUID
    branch_id: uuid.UUID
    warehouse_id: uuid.UUID
    administrator_id: uuid.UUID
    roles_created: int
    sequences_created: int
    units_created: int
    account_groups_created: int
    accounts_created: int
    periods_created: int
    document_types_created: int


class SetupService:
    """First-run provisioning."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    def is_required(self) -> bool:
        """Whether provisioning has yet to run."""
        return self._db.scalar(select(func.count()).select_from(Business)) == 0

    def available_presets(self) -> list[dict[str, object]]:
        """The shipped theme presets, the recommended one first, per ADR-0002."""
        return [
            {
                "code": preset.code,
                "name": preset.name,
                "description": preset.description,
                "source": preset.source,
                "recommended": preset.recommended,
            }
            for preset in load_presets().values()
        ]

    def provision(self, request: SetupRequest) -> SetupResult:
        """Create the business and its administrator. Runs once."""
        if not self.is_required():
            raise ConflictError("This installation has already been set up.")

        if not request.business_name.strip():
            raise ValidationError("Business name is required.", field="business_name")
        if request.currency_precision < 0 or request.currency_precision > 6:
            raise ValidationError(
                "Currency precision must be between 0 and 6.", field="currency_precision"
            )
        if not 1 <= request.financial_year_start_month <= 12:
            raise ValidationError(
                "Financial year start month must be between 1 and 12.",
                field="financial_year_start_month",
            )
        if len(request.base_currency) != 3 or not request.base_currency.isalpha():
            raise ValidationError(
                "Base currency must be a three-letter code.", field="base_currency"
            )

        preset = get_preset(request.theme_preset)
        username = validate_username(request.administrator_username)
        branch_code = validate_code(request.branch_code, field="branch_code")
        warehouse_code = validate_code(request.warehouse_code, field="warehouse_code")

        now = self._clock.now()

        business = Business(
            name=request.business_name.strip(),
            legal_name=request.legal_name or None,
            tax_identifier=request.tax_identifier or None,
            registration_number=request.registration_number or None,
            email=request.email or None,
            telephone=request.telephone or None,
            website=request.website or None,
            base_currency=request.base_currency.upper(),
            currency_symbol=request.currency_symbol,
            currency_precision=request.currency_precision,
            timezone=request.timezone,
            financial_year_start_month=request.financial_year_start_month,
        )
        self._db.add(business)
        self._db.flush()

        actor = SystemActor(business_id=business.id, reason="First-run provisioning")

        branch = Branch(
            business_id=business.id,
            business_fk=business.id,
            code=branch_code,
            name=request.branch_name.strip() or "Head office",
            is_primary=True,
        )
        self._db.add(branch)
        self._db.flush()

        warehouse = Warehouse(
            business_id=business.id,
            branch_fk=branch.id,
            code=warehouse_code,
            name=request.warehouse_name.strip() or "Main store",
            is_default=True,
        )
        self._db.add(warehouse)

        for code, name in request.extra_branches:
            self._db.add(
                Branch(
                    business_id=business.id,
                    business_fk=business.id,
                    code=validate_code(code, field="branch_code"),
                    name=name.strip(),
                )
            )

        roles = self._seed_roles(business.id)
        sequences = self._seed_sequences(business.id)
        units = self._seed_units(business.id)
        groups, accounts, periods = self._seed_ledger(business, now.date())
        document_types = self._seed_documents(business.id)

        self._db.add(
            ThemeProfile(
                business_id=business.id,
                preset_code=preset.code,
                overrides={},
                audit_passed_at=now,
            )
        )
        self._db.flush()

        identity = IdentityService(self._db, clock=self._clock)
        administrator = identity.create_user(
            actor,
            username=username,
            full_name=request.administrator_full_name.strip() or username,
            password=request.administrator_password,
            role_codes=["administrator"],
            email=request.administrator_email,
            branch_id=branch.id,
            must_change_password=False,
        )

        business.setup_completed_at = now

        self._audit.record(
            actor,
            AuditAction.BUSINESS_CREATED,
            entity_type="business",
            entity_id=business.id,
            entity_label=business.name,
            reason="First-run provisioning",
            after={
                "name": business.name,
                "base_currency": business.base_currency,
                "timezone": business.timezone,
                "theme_preset": preset.code,
            },
        )
        self._audit.record(
            actor,
            AuditAction.SETUP_COMPLETED,
            entity_type="business",
            entity_id=business.id,
            entity_label=business.name,
            context={
                "roles": roles,
                "sequences": sequences,
                "units": units,
                "account_groups": groups,
                "accounts": accounts,
                "periods": periods,
                "document_types": document_types,
                "branches": 1 + len(request.extra_branches),
            },
        )
        logger.info("Provisioned business %r with %d roles", business.name, roles)

        return SetupResult(
            business_id=business.id,
            branch_id=branch.id,
            warehouse_id=warehouse.id,
            administrator_id=administrator.id,
            roles_created=roles,
            sequences_created=sequences,
            units_created=units,
            account_groups_created=groups,
            accounts_created=accounts,
            periods_created=periods,
            document_types_created=document_types,
        )

    def _seed_ledger(self, business: Business, on: date) -> tuple[int, int, int]:
        """Create the chart of accounts and open the first financial year.

        A business cannot record anything before it has a chart, and a posting cannot
        be dated into a year that was never opened, so both belong to provisioning
        rather than to a later setup step the user might not reach.
        """
        from nickland.application.services.accounts import AccountService

        service = AccountService(self._db, clock=self._clock)
        groups, accounts = service.seed(business.id)
        start_month = business.financial_year_start_month
        year = on.year if on.month >= start_month else on.year - 1
        periods = service.open_financial_year(business.id, year, start_month)
        return groups, accounts, periods

    def _seed_documents(self, business_id: uuid.UUID) -> int:
        """Create the twelve default document types, per PRD 6.6.

        A business must be able to print from its first day, so the defaults are
        provisioned rather than left to a later setup step.
        """
        from nickland.application.services.documents import DocumentRecordService

        return DocumentRecordService(self._db, clock=self._clock).seed(business_id)

    def _seed_roles(self, business_id: uuid.UUID) -> int:
        """Create the shipped roles. Each is editable, and further roles may be added.

        PRD 3 describes eight job functions. Two are seeded — the administrator and
        the sales person — because those are the two this business has. The other six
        are role definitions, not capabilities: every permission they would need is
        in the registry, so an administrator creates them under Settings without a
        code change.
        """
        for definition in SEEDED_ROLES:
            role = Role(
                business_id=business_id,
                code=definition.code,
                name=definition.name,
                description=definition.description,
                is_administrator=definition.is_administrator,
                is_read_only=definition.is_read_only,
                is_system=True,
            )
            self._db.add(role)
            self._db.flush()
            for code in sorted(definition.permissions):
                self._db.add(
                    RolePermission(business_id=business_id, role_fk=role.id, permission_code=code)
                )
        return len(SEEDED_ROLES)

    def _seed_units(self, business_id: uuid.UUID) -> int:
        """Create the conventional units of measure, per PRD 6.1."""
        from nickland.application.services.units import UnitService

        return UnitService(self._db, clock=self._clock).seed(business_id)

    def _seed_sequences(self, business_id: uuid.UUID) -> int:
        """Create one numbering sequence per document kind, per PRD 6.7."""
        for kind in DocumentKind:
            _, prefix = DOCUMENT_KIND_LABELS[kind]
            self._db.add(
                NumberSequence(
                    business_id=business_id,
                    branch_fk=None,
                    document_kind=kind.value,
                    prefix=prefix,
                    pattern=DEFAULT_PATTERN.template,
                    reset_cadence=ResetCadence.ANNUALLY.value,
                    period_key="all",
                    next_value=1,
                )
            )
        return len(DocumentKind)


def seeded_administrator_exists(session: DbSession) -> bool:
    """Whether at least one active administrator exists."""
    from nickland.persistence.models import Role, UserRole

    return (
        session.scalar(
            select(User.id)
            .join(UserRole, UserRole.user_fk == User.id)
            .join(Role, Role.id == UserRole.role_fk)
            .where(User.is_active.is_(True), Role.is_administrator.is_(True))
            .limit(1)
        )
        is not None
    )
