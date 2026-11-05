"""Business configuration: identity, branches, warehouses, settings and numbering."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditAction, AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.organisation import (
    DOCUMENT_KIND_LABELS,
    DocumentKind,
    NumberPattern,
    ResetCadence,
    period_key,
    validate_code,
)
from nickland.persistence.base import as_dict
from nickland.persistence.models import (
    Address,
    Branch,
    Business,
    NumberSequence,
    Setting,
    Warehouse,
)
from nickland.platform.clock import Clock, SystemClock, business_date


@dataclass(frozen=True, slots=True)
class AllocatedNumber:
    """An allocated document number."""

    number: str
    counter: int
    sequence_id: uuid.UUID


class OrganisationService:
    """Business configuration and document numbering."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # -- business ---------------------------------------------------------------------

    def get_business(self, actor: AnyActor) -> Business:
        actor.require("business.view")
        business = self._db.get(Business, actor.business_id)
        if business is None:
            raise NotFoundError("Business not found.")
        return business

    def amend_business(self, actor: AnyActor, changes: dict[str, Any]) -> Business:
        """Amend business identity and configuration."""
        actor.require("business.manage")
        business = self._db.get(Business, actor.business_id)
        if business is None:
            raise NotFoundError("Business not found.")

        permitted = {
            "name", "legal_name", "trading_name", "tax_identifier", "registration_number",
            "email", "telephone", "website", "base_currency", "currency_symbol",
            "currency_precision", "timezone", "locale", "financial_year_start_month",
            "logo_attachment_id",
        }
        unknown = set(changes) - permitted
        if unknown:
            raise ValidationError(f"Unknown field(s): {sorted(unknown)}")

        before = as_dict(business)

        if "name" in changes and not str(changes["name"]).strip():
            raise ValidationError("Business name is required.", field="name")
        if "base_currency" in changes and business.base_currency != changes["base_currency"]:
            raise ConflictError(
                "The base currency cannot be changed once the business is in use. "
                "All accounting is conducted in the base currency."
            )
        if "currency_precision" in changes and not 0 <= int(changes["currency_precision"]) <= 6:
            raise ValidationError(
                "Currency precision must be between 0 and 6.", field="currency_precision"
            )
        if "financial_year_start_month" in changes and not (
            1 <= int(changes["financial_year_start_month"]) <= 12
        ):
            raise ValidationError(
                "Financial year start month must be between 1 and 12.",
                field="financial_year_start_month",
            )

        for key, value in changes.items():
            setattr(business, key, value)

        self._audit.record_change(
            actor,
            AuditAction.BUSINESS_AMENDED,
            entity_type="business",
            entity_id=business.id,
            entity_label=business.name,
            before=before,
            after=as_dict(business),
        )
        return business

    # -- branches and warehouses ------------------------------------------------------

    def list_branches(self, actor: AnyActor, *, include_inactive: bool = False) -> list[Branch]:
        actor.require("business.view")
        statement = select(Branch).where(Branch.business_id == actor.business_id)
        if not include_inactive:
            statement = statement.where(Branch.is_active.is_(True))
        return list(self._db.scalars(statement.order_by(Branch.is_primary.desc(), Branch.name)))

    def create_branch(
        self,
        actor: AnyActor,
        *,
        code: str,
        name: str,
        email: str | None = None,
        telephone: str | None = None,
        is_primary: bool = False,
    ) -> Branch:
        actor.require("business.manage")
        branch_code = validate_code(code, field="code")
        if not name.strip():
            raise ValidationError("Branch name is required.", field="name")

        existing = self._db.scalar(
            select(Branch).where(
                Branch.business_id == actor.business_id, Branch.code == branch_code
            )
        )
        if existing is not None:
            raise ConflictError(f"Branch code {branch_code!r} is already in use.")

        if is_primary:
            self._db.execute(
                update(Branch)
                .where(Branch.business_id == actor.business_id)
                .values(is_primary=False)
            )

        branch = Branch(
            business_id=actor.business_id,
            business_fk=actor.business_id,
            code=branch_code,
            name=name.strip(),
            email=email or None,
            telephone=telephone or None,
            is_primary=is_primary,
        )
        self._db.add(branch)
        self._db.flush()

        self._audit.record(
            actor,
            AuditAction.BRANCH_CREATED,
            entity_type="branch",
            entity_id=branch.id,
            entity_label=branch.name,
            after={"code": branch.code, "name": branch.name, "is_primary": is_primary},
        )
        return branch

    def create_warehouse(
        self,
        actor: AnyActor,
        *,
        branch_id: uuid.UUID,
        code: str,
        name: str,
        is_default: bool = False,
        allows_negative_stock: bool = False,
    ) -> Warehouse:
        actor.require("business.manage")
        warehouse_code = validate_code(code, field="code")
        if not name.strip():
            raise ValidationError("Warehouse name is required.", field="name")

        branch = self._db.scalar(
            select(Branch).where(
                Branch.business_id == actor.business_id, Branch.id == branch_id
            )
        )
        if branch is None:
            raise NotFoundError("Branch not found.")

        existing = self._db.scalar(
            select(Warehouse).where(
                Warehouse.business_id == actor.business_id, Warehouse.code == warehouse_code
            )
        )
        if existing is not None:
            raise ConflictError(f"Warehouse code {warehouse_code!r} is already in use.")

        if is_default:
            self._db.execute(
                update(Warehouse)
                .where(Warehouse.business_id == actor.business_id)
                .values(is_default=False)
            )

        warehouse = Warehouse(
            business_id=actor.business_id,
            branch_fk=branch_id,
            code=warehouse_code,
            name=name.strip(),
            is_default=is_default,
            allows_negative_stock=allows_negative_stock,
        )
        self._db.add(warehouse)
        self._db.flush()

        self._audit.record(
            actor,
            AuditAction.WAREHOUSE_CREATED,
            entity_type="warehouse",
            entity_id=warehouse.id,
            entity_label=warehouse.name,
            after={"code": warehouse.code, "name": warehouse.name},
        )
        return warehouse

    def list_warehouses(self, actor: AnyActor) -> list[Warehouse]:
        actor.require("business.view")
        return list(
            self._db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.business_id == actor.business_id,
                    Warehouse.is_active.is_(True),
                )
                .order_by(Warehouse.is_default.desc(), Warehouse.name)
            )
        )

    # -- addresses --------------------------------------------------------------------

    def set_address(
        self,
        actor: AnyActor,
        *,
        owner_type: str,
        owner_id: uuid.UUID,
        purpose: str,
        values: dict[str, Any],
        is_primary: bool = False,
    ) -> Address:
        """Create or replace an address on an owning record."""
        actor.require("business.manage")
        permitted = {
            "line_one", "line_two", "city", "region", "postal_code", "country",
            "digital_address",
        }
        unknown = set(values) - permitted
        if unknown:
            raise ValidationError(f"Unknown address field(s): {sorted(unknown)}")

        address = self._db.scalar(
            select(Address).where(
                Address.business_id == actor.business_id,
                Address.owner_type == owner_type,
                Address.owner_id == owner_id,
                Address.purpose == purpose,
            )
        )
        if address is None:
            address = Address(
                business_id=actor.business_id,
                owner_type=owner_type,
                owner_id=owner_id,
                purpose=purpose,
                is_primary=is_primary,
            )
            self._db.add(address)

        for key, value in values.items():
            setattr(address, key, value or None)
        address.is_primary = is_primary
        self._db.flush()
        return address

    # -- settings ---------------------------------------------------------------------

    def get_setting(self, actor: AnyActor, key: str, default: Any = None) -> Any:
        actor.require("business.view")
        setting = self._db.scalar(
            select(Setting).where(Setting.business_id == actor.business_id, Setting.key == key)
        )
        return default if setting is None else setting.value

    def set_setting(
        self, actor: AnyActor, key: str, value: Any, *, description: str | None = None
    ) -> Setting:
        actor.require("business.manage")
        setting = self._db.scalar(
            select(Setting).where(Setting.business_id == actor.business_id, Setting.key == key)
        )
        before = {"value": None if setting is None else setting.value}
        if setting is None:
            setting = Setting(
                business_id=actor.business_id, key=key, value=value, description=description
            )
            self._db.add(setting)
        else:
            setting.value = value
            if description is not None:
                setting.description = description
        self._db.flush()

        self._audit.record(
            actor,
            AuditAction.SETTING_AMENDED,
            entity_type="setting",
            entity_id=setting.id,
            entity_label=key,
            before=before,
            after={"value": value},
        )
        return setting

    def all_settings(self, actor: AnyActor) -> dict[str, Any]:
        actor.require("business.view")
        rows = self._db.scalars(
            select(Setting).where(Setting.business_id == actor.business_id)
        ).all()
        return {row.key: row.value for row in rows}

    # -- numbering --------------------------------------------------------------------

    def list_sequences(self, actor: AnyActor) -> list[NumberSequence]:
        actor.require("business.view")
        return list(
            self._db.scalars(
                select(NumberSequence)
                .where(NumberSequence.business_id == actor.business_id)
                .order_by(NumberSequence.document_kind)
            )
        )

    def amend_sequence(
        self,
        actor: AnyActor,
        document_kind: DocumentKind | str,
        *,
        prefix: str | None = None,
        pattern: str | None = None,
        reset_cadence: ResetCadence | str | None = None,
        next_value: int | None = None,
        branch_id: uuid.UUID | None = None,
    ) -> NumberSequence:
        """Amend a numbering sequence.

        ``next_value`` may only be raised. Lowering it would reissue a number that has
        already been used, which PRD 6.7 forbids.
        """
        actor.require("numbering.manage")
        sequence = self._require_sequence(actor, str(document_kind), branch_id)
        before = as_dict(sequence)

        if pattern is not None:
            NumberPattern(pattern)  # validates, raising on an unknown placeholder
            sequence.pattern = pattern
        if prefix is not None:
            if not prefix.strip():
                raise ValidationError("Prefix is required.", field="prefix")
            sequence.prefix = prefix.strip().upper()
        if reset_cadence is not None:
            sequence.reset_cadence = str(reset_cadence)
        if next_value is not None:
            if next_value < sequence.next_value:
                raise ConflictError(
                    f"The next number cannot be lowered below {sequence.next_value}. "
                    "A document number is never reused."
                )
            sequence.next_value = next_value

        self._audit.record_change(
            actor,
            AuditAction.NUMBERING_AMENDED,
            entity_type="number_sequence",
            entity_id=sequence.id,
            entity_label=sequence.document_kind,
            before=before,
            after=as_dict(sequence),
        )
        return sequence

    def allocate_number(
        self,
        actor: AnyActor,
        document_kind: DocumentKind | str,
        *,
        branch_id: uuid.UUID | None = None,
        on: date | None = None,
    ) -> AllocatedNumber:
        """Allocate the next document number.

        Allocation occurs inside the caller's transaction, so a rolled-back document
        leaves no gap. The row is locked for update where the engine supports it, which
        keeps numbering unique under concurrent issue in LAN mode, per PRD 9.5.
        """
        business = self._db.get(Business, actor.business_id)
        if business is None:
            raise NotFoundError("Business not found.")

        issue_date = on or business_date(self._clock.now(), business.timezone)
        sequence = self._require_sequence(
            actor, str(document_kind), branch_id, for_update=True
        )

        bucket = period_key(ResetCadence(sequence.reset_cadence), issue_date)
        if bucket != sequence.period_key:
            sequence.period_key = bucket
            sequence.next_value = 1

        counter = sequence.next_value
        sequence.next_value = counter + 1
        self._db.flush()

        branch_code = ""
        if branch_id is not None:
            branch = self._db.get(Branch, branch_id)
            branch_code = branch.code.upper() if branch is not None else ""

        number = NumberPattern(sequence.pattern).render(
            counter=counter,
            prefix=sequence.prefix,
            branch_code=branch_code,
            on=issue_date,
        )
        return AllocatedNumber(number=number, counter=counter, sequence_id=sequence.id)

    def preview_number(
        self,
        actor: AnyActor,
        document_kind: DocumentKind | str,
        *,
        pattern: str | None = None,
        prefix: str | None = None,
        branch_id: uuid.UUID | None = None,
    ) -> str:
        """Render what the next number would look like, without allocating it."""
        actor.require("business.view")
        business = self._db.get(Business, actor.business_id)
        if business is None:
            raise NotFoundError("Business not found.")
        sequence = self._require_sequence(actor, str(document_kind), branch_id)
        branch_code = ""
        if branch_id is not None:
            branch = self._db.get(Branch, branch_id)
            branch_code = branch.code.upper() if branch is not None else ""
        return NumberPattern(pattern or sequence.pattern).render(
            counter=sequence.next_value,
            prefix=(prefix or sequence.prefix).upper(),
            branch_code=branch_code,
            on=business_date(self._clock.now(), business.timezone),
        )

    def _require_sequence(
        self,
        actor: AnyActor,
        document_kind: str,
        branch_id: uuid.UUID | None,
        *,
        for_update: bool = False,
    ) -> NumberSequence:
        statement = select(NumberSequence).where(
            NumberSequence.business_id == actor.business_id,
            NumberSequence.document_kind == document_kind,
            NumberSequence.branch_fk == branch_id,
        )
        if for_update and self._db.bind is not None and self._db.bind.dialect.name != "sqlite":
            statement = statement.with_for_update()
        sequence = self._db.scalar(statement)
        if sequence is None and branch_id is not None:
            # Fall back to the business-wide sequence where no branch sequence exists.
            return self._require_sequence(actor, document_kind, None, for_update=for_update)
        if sequence is None:
            label = DOCUMENT_KIND_LABELS.get(DocumentKind(document_kind), (document_kind, ""))[0]
            raise NotFoundError(f"No numbering sequence configured for {label}.")
        return sequence
