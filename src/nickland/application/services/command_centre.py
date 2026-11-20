"""The command centre: headline measures from the ledger, each to its transactions.
PRD §20.1, §20.2, AC-19.

A measure is a sum of journal lines on named accounts, so drilling into it lists exactly
the lines it is made of and their total is the measure. Nothing here keeps a figure of
its own; a measure that disagreed with the ledger would be a second set of books.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.finance import (
    LAYOUT_PRESETS,
    MEASURES,
    MEASURES_BY_CODE,
    Component,
    InvoiceOrigin,
    MeasureBasis,
    MeasureDefinition,
    validate_widgets,
)
from nickland.domain.money import ZERO, money
from nickland.persistence.catalogue_models import Party, Product, ProductCategory
from nickland.persistence.finance_models import DashboardLayout, SalesInvoice, SalesInvoiceLine
from nickland.persistence.ledger_models import Account, AccountGroup, JournalEntry, JournalLine
from nickland.persistence.models import Branch, Business, User
from nickland.persistence.pos_models import PosSale, PosSaleLine
from nickland.platform.clock import Clock, SystemClock, business_date

DIMENSIONS = ("channel", "product", "category", "customer", "branch", "salesperson")


@dataclass(slots=True)
class MeasureValue:
    definition: MeasureDefinition
    value: Decimal
    starts_on: date | None
    ends_on: date


@dataclass(slots=True)
class DrillLine:
    entry_id: uuid.UUID
    number: str | None
    posting_date: date
    narration: str
    account_code: str
    account_name: str
    debit: Decimal
    credit: Decimal
    contribution: Decimal


@dataclass(slots=True)
class Drill:
    measure: MeasureValue
    lines: list[DrillLine]
    total: Decimal
    """The sum of every contribution: always the measure."""
    truncated: bool


@dataclass(slots=True)
class BreakdownRow:
    key: str
    label: str
    amount: Decimal


@dataclass(slots=True)
class Layout:
    preset: str
    widgets: list[dict[str, str]]
    source: str
    """``personal``, ``role`` or ``preset``: where this layout came from."""


class CommandCentreService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # -- measures -------------------------------------------------------------------------

    def measures(
        self,
        actor: AnyActor,
        *,
        start: date,
        end: date,
        codes: list[str] | None = None,
    ) -> list[MeasureValue]:
        actor.require("report.view")
        business_id = _business_of(actor)
        if end < start:
            raise ValidationError("A period ends after it begins.", field="end")
        chosen = [MEASURES_BY_CODE[code] for code in codes] if codes else list(MEASURES)
        return [self._value(business_id, definition, start, end) for definition in chosen]

    def drill(
        self,
        actor: AnyActor,
        code: str,
        *,
        start: date,
        end: date,
        limit: int = 2000,
    ) -> Drill:
        """Every journal line a measure is made of, with what each contributes."""
        actor.require("report.view")
        business_id = _business_of(actor)
        definition = MEASURES_BY_CODE.get(code)
        if definition is None:
            raise NotFoundError(f"{code!r} is not a measure.")
        value = self._value(business_id, definition, start, end)
        lines: list[DrillLine] = []
        total = ZERO
        for component in definition.components:
            accounts = self._accounts(business_id, component)
            if not accounts:
                continue
            query = self._lines(business_id, definition, accounts, start, end, component)
            rows = self._db.execute(
                query.add_columns(JournalEntry).join(
                    JournalEntry, JournalEntry.id == JournalLine.entry_fk
                )
            ).all()
            for line, entry in rows:
                account = accounts[line.account_fk]
                contribution = _contribution(component, line.base_debit, line.base_credit)
                total = money(total + contribution)
                lines.append(
                    DrillLine(
                        entry_id=entry.id,
                        number=entry.number,
                        posting_date=line.posting_date,
                        narration=line.narration or entry.narration,
                        account_code=account.code,
                        account_name=account.name,
                        debit=line.base_debit,
                        credit=line.base_credit,
                        contribution=contribution,
                    )
                )
        lines.sort(key=lambda item: (item.posting_date, item.number or "", item.account_code))
        return Drill(
            measure=value,
            lines=lines[: max(min(limit, 5000), 1)],
            total=total,
            truncated=len(lines) > limit,
        )

    # -- revenue by dimension ----------------------------------------------------------------

    def revenue_by(
        self, actor: AnyActor, dimension: str, *, start: date, end: date
    ) -> list[BreakdownRow]:
        """Net sales by channel, product, category, customer, branch or sales person, from
        the invoice and register lines."""
        actor.require("report.view")
        business_id = _business_of(actor)
        if dimension not in DIMENSIONS:
            raise ValidationError(
                f"Break revenue down by {', '.join(DIMENSIONS)}.", field="dimension"
            )
        rows: dict[str, BreakdownRow] = {}

        def add(key: str, label: str, amount: Decimal) -> None:
            row = rows.get(key) or BreakdownRow(key=key, label=label, amount=ZERO)
            row.amount = money(row.amount + amount)
            rows[key] = row

        invoice_lines = self._db.execute(
            select(SalesInvoiceLine, SalesInvoice)
            .join(SalesInvoice, SalesInvoice.id == SalesInvoiceLine.invoice_fk)
            .where(
                SalesInvoice.business_id == business_id,
                SalesInvoice.origin == InvoiceOrigin.SALES_ORDER.value,
                SalesInvoice.issued_on.between(start, end),
            )
        ).all()
        for line, invoice in invoice_lines:
            key, label = self._key(
                dimension,
                channel=("quotation", "Quotation-led sales"),
                product_id=line.product_fk,
                customer_id=invoice.customer_fk,
                branch_id=invoice.branch_id,
                person_id=invoice.issued_by_id,
            )
            add(key, label, line.net)
        register_lines = self._db.execute(
            select(PosSaleLine, PosSale)
            .join(PosSale, PosSale.id == PosSaleLine.sale_fk)
            .where(
                PosSale.business_id == business_id,
                PosSale.completed_on.between(start, end),
            )
        ).all()
        for line, sale in register_lines:
            key, label = self._key(
                dimension,
                channel=("register", "Point of sale"),
                product_id=line.product_fk,
                customer_id=sale.customer_fk,
                branch_id=sale.branch_id,
                person_id=sale.cashier_id,
            )
            add(key, label, line.net)
        return sorted(rows.values(), key=lambda row: (-row.amount, row.label))

    # -- layouts --------------------------------------------------------------------------

    def layout(self, actor: AnyActor) -> Layout:
        """The person's own layout, else their role's, else the preset for their role."""
        actor.require("report.view")
        business_id = _business_of(actor)
        own = (
            self._db.scalar(
                select(DashboardLayout).where(
                    DashboardLayout.business_id == business_id,
                    DashboardLayout.user_fk == actor.user_id,
                )
            )
            if actor.user_id
            else None
        )
        if own is not None:
            return Layout(preset=own.preset, widgets=list(own.widgets), source="personal")
        for role in sorted(actor.role_codes):
            shared = self._db.scalar(
                select(DashboardLayout).where(
                    DashboardLayout.business_id == business_id,
                    DashboardLayout.role_code == role,
                )
            )
            if shared is not None:
                return Layout(preset=shared.preset, widgets=list(shared.widgets), source="role")
        preset = "owner" if actor.is_administrator else "branch_manager"
        return self.preset(preset)

    def preset(self, name: str) -> Layout:
        codes = LAYOUT_PRESETS.get(name)
        if codes is None:
            raise NotFoundError(f"{name!r} is not a layout.")
        return Layout(
            preset=name,
            widgets=[
                {"measure": code, "size": "large" if index < 2 else "medium"}
                for index, code in enumerate(codes)
            ],
            source="preset",
        )

    def save_layout(
        self,
        actor: AnyActor,
        widgets: list[dict[str, str]],
        *,
        preset: str = "owner",
        role_code: str | None = None,
    ) -> Layout:
        """Keep a layout: one's own, or with ``dashboard.configure`` a role's."""
        actor.require("report.view")
        business_id = _business_of(actor)
        clean = validate_widgets(widgets)
        if preset not in LAYOUT_PRESETS:
            raise ValidationError(f"{preset!r} is not a layout.", field="preset")
        if role_code is not None:
            actor.require("dashboard.configure")
            row = self._db.scalar(
                select(DashboardLayout).where(
                    DashboardLayout.business_id == business_id,
                    DashboardLayout.role_code == role_code,
                )
            )
        else:
            if actor.user_id is None:
                raise ConflictError("A layout is kept for a signed-in person.")
            row = self._db.scalar(
                select(DashboardLayout).where(
                    DashboardLayout.business_id == business_id,
                    DashboardLayout.user_fk == actor.user_id,
                )
            )
        if row is None:
            row = DashboardLayout(
                business_id=business_id,
                user_fk=None if role_code else actor.user_id,
                role_code=role_code,
                preset=preset,
                widgets=clean,
            )
            self._db.add(row)
        else:
            row.preset = preset
            row.widgets = clean
        self._db.flush()
        if role_code is not None:
            self._audit.record(
                actor,
                "dashboard_layout_saved",
                entity_type="dashboard_layout",
                entity_id=row.id,
                entity_label=role_code,
                after={"measures": [item["measure"] for item in clean]},
            )
        return Layout(preset=preset, widgets=clean, source="role" if role_code else "personal")

    def reset_layout(self, actor: AnyActor) -> Layout:
        actor.require("report.view")
        row = self._db.scalar(
            select(DashboardLayout).where(
                DashboardLayout.business_id == actor.business_id,
                DashboardLayout.user_fk == actor.user_id,
            )
        )
        if row is not None:
            self._db.delete(row)
            self._db.flush()
        return self.layout(actor)

    # -- internals ------------------------------------------------------------------------

    def _value(
        self, business_id: uuid.UUID, definition: MeasureDefinition, start: date, end: date
    ) -> MeasureValue:
        total = ZERO
        for component in definition.components:
            accounts = self._accounts(business_id, component)
            if not accounts:
                continue
            query = self._lines(business_id, definition, accounts, start, end, component)
            debit, credit = self._db.execute(
                query.with_only_columns(
                    func.coalesce(func.sum(JournalLine.base_debit), 0),
                    func.coalesce(func.sum(JournalLine.base_credit), 0),
                )
            ).one()
            total = money(
                total + _contribution(component, Decimal(str(debit)), Decimal(str(credit)))
            )
        return MeasureValue(
            definition=definition,
            value=total,
            starts_on=start if definition.basis is MeasureBasis.FLOW else None,
            ends_on=end,
        )

    def _lines(
        self,
        business_id: uuid.UUID,
        definition: MeasureDefinition,
        accounts: dict[uuid.UUID, Account],
        start: date,
        end: date,
        component: Component,
    ) -> Select[tuple[JournalLine]]:
        query = select(JournalLine).where(
            JournalLine.business_id == business_id,
            JournalLine.account_fk.in_(list(accounts)),
            JournalLine.state == "posted",
            JournalLine.posting_date <= end,
        )
        if definition.basis is MeasureBasis.FLOW:
            query = query.where(JournalLine.posting_date >= start)
        if component.credits_only:
            query = query.where(JournalLine.base_credit > 0)
        return query

    def _accounts(self, business_id: uuid.UUID, component: Component) -> dict[uuid.UUID, Account]:
        conditions = []
        if component.codes:
            conditions.append(Account.code.in_(sorted(component.codes)))
        for prefix in sorted(component.prefixes):
            conditions.append(Account.code.like(f"{prefix}%"))
        if component.groups:
            groups = select(AccountGroup.id).where(
                AccountGroup.business_id == business_id,
                AccountGroup.code.in_(sorted(component.groups)),
            )
            conditions.append(Account.group_fk.in_(groups))
        if not conditions:
            return {}
        return {
            account.id: account
            for account in self._db.scalars(
                select(Account).where(Account.business_id == business_id, or_(*conditions))
            ).all()
        }

    def _key(
        self,
        dimension: str,
        *,
        channel: tuple[str, str],
        product_id: uuid.UUID | None,
        customer_id: uuid.UUID | None,
        branch_id: uuid.UUID | None,
        person_id: uuid.UUID | None,
    ) -> tuple[str, str]:
        if dimension == "channel":
            return channel
        if dimension in ("product", "category"):
            product = self._db.get(Product, product_id) if product_id else None
            if dimension == "product":
                return (
                    (str(product_id), product.name) if product else ("none", "Not a catalogue item")
                )
            category = (
                self._db.get(ProductCategory, product.category_fk)
                if product and product.category_fk
                else None
            )
            return (str(category.id), category.name) if category else ("none", "Uncategorised")
        if dimension == "customer":
            party = self._db.get(Party, customer_id) if customer_id else None
            return (str(customer_id), party.name) if party else ("walk_in", "Walk-in customers")
        if dimension == "branch":
            branch = self._db.get(Branch, branch_id) if branch_id else None
            return (str(branch_id), branch.name) if branch else ("main", "Main business")
        user = self._db.get(User, person_id) if person_id else None
        return (str(person_id), user.full_name) if user else ("none", "Not recorded")

    def today(self, actor: AnyActor) -> date:
        business = self._db.get(Business, actor.business_id)
        return business_date(self._clock.now(), business.timezone if business else "UTC")


def _contribution(component: Component, debit: Decimal, credit: Decimal) -> Decimal:
    if component.credits_only:
        signed = credit
    elif component.credit_normal:
        signed = credit - debit
    else:
        signed = debit - credit
    return money(signed * component.multiplier)


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
