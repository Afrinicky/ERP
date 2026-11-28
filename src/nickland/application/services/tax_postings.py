"""Tax on a sale, evaluated line by line and posted rule by rule.

Shared by the register and by invoicing, so a sale taxes the same way whichever door it
comes through: each line's net is evaluated by the tax engine for the customer's
treatment, each charged rule is rounded to the currency, and each rule posts to its own
liability account on its authority's subledger.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.context import SystemActor
from nickland.application.services.taxation import TaxableLine, TaxContext, TaxService
from nickland.domain.errors import ConflictError
from nickland.domain.ledger import EntryLine, Side, SubledgerKind
from nickland.domain.money import ZERO, money, present
from nickland.persistence.catalogue_models import Party, Product
from nickland.persistence.tax_models import TaxRule
from nickland.platform.clock import Clock


@dataclass(frozen=True, slots=True)
class SaleLine:
    reference: str
    net: Decimal
    quantity: Decimal
    product_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class LineTax:
    total: Decimal
    breakdown: dict[str, str]


def tax_sale(
    db: DbSession,
    clock: Clock,
    business_id: uuid.UUID,
    lines: list[SaleLine],
    *,
    customer_id: uuid.UUID | None,
    transaction_kind: str,
    on: date,
    currency_code: str,
    precision: int,
    record_as: tuple[str, uuid.UUID] | None = None,
) -> dict[str, LineTax]:
    """Each line's charged tax, by line reference. With ``record_as``, the evaluation is
    also recorded against that source."""
    actor = SystemActor(business_id=business_id, reason="Sales tax evaluation")
    party = db.get(Party, customer_id) if customer_id else None
    taxable = []
    for line in lines:
        product = db.get(Product, line.product_id) if line.product_id else None
        taxable.append(
            TaxableLine(
                reference=line.reference,
                net=line.net,
                quantity=line.quantity,
                product_tax_code=product.tax_code if product else None,
                product_category=str(product.category_fk)
                if product and product.category_fk
                else None,
                item_class=product.item_class if product else None,
                product_id=line.product_id,
            )
        )
    context = TaxContext(
        transaction_kind=transaction_kind,
        on=on,
        direction="sales",
        party_id=customer_id,
        party_tax_treatment=party.tax_treatment if party else None,
        party_tax_identifier=party.tax_identifier if party else None,
        currency_code=currency_code,
    )
    service = TaxService(db, clock=clock)
    evaluation = service.evaluate(actor, taxable, context)
    if record_as is not None:
        service.record(actor, evaluation, source_type=record_as[0], source_id=record_as[1])
    result: dict[str, LineTax] = {}
    for assessed in evaluation.lines:
        breakdown: dict[str, str] = {}
        total = ZERO
        for item in assessed.assessments:
            if item.is_charged:
                amount = present(item.amount, precision)
                breakdown[item.rule_code] = str(amount)
                total = money(total + amount)
        result[assessed.line_reference] = LineTax(total=total, breakdown=breakdown)
    return result


def tax_entry_lines(
    db: DbSession, business_id: uuid.UUID, totals: dict[str, Decimal], side: Side
) -> list[EntryLine]:
    """One line per rule, to the rule's liability account on its authority's subledger."""
    lines: list[EntryLine] = []
    for code, amount in sorted(totals.items()):
        if amount <= ZERO:
            continue
        rule = db.scalar(
            select(TaxRule).where(TaxRule.business_id == business_id, TaxRule.code == code)
        )
        if rule is None or not rule.liability_account_code:
            raise ConflictError(
                f"Tax rule {code} names no liability account to post the tax to. Configure "
                "it under Taxation."
            )
        lines.append(
            EntryLine(
                account_code=rule.liability_account_code,
                side=side,
                amount=amount,
                narration=rule.name,
                party_id=rule.authority_fk,
                subledger=SubledgerKind.AUTHORITY if rule.authority_fk else None,
            )
        )
    return lines


def by_rule(breakdowns: list[dict[str, str] | None]) -> dict[str, Decimal]:
    """Sum per-line breakdowns into totals per rule."""
    totals: dict[str, Decimal] = {}
    for breakdown in breakdowns:
        for code, amount in (breakdown or {}).items():
            totals[code] = money(totals.get(code, ZERO) + Decimal(str(amount)))
    return totals
