"""Price lists and price resolution.

The quotation engine of Phase 5 computes a selling price from cost. A price list is a
different thing: a published figure for a product, which the pricing engine may take as
its starting point and which the register uses directly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.money import ZERO
from nickland.domain.organisation import validate_code
from nickland.persistence.catalogue_models import (
    CustomerPrice,
    Party,
    PriceList,
    PriceListLine,
    Product,
)
from nickland.platform.clock import Clock, SystemClock


@dataclass(frozen=True, slots=True)
class ResolvedPrice:
    """A price and the reason it was chosen.

    The reason is carried because a user asked what a figure is must be able to be told,
    and because the quotation engine records its inputs.
    """

    price: Decimal
    source: str
    source_label: str
    currency_code: str | None
    tax_inclusive: bool
    minimum_quantity: Decimal = ZERO

    def describe(self) -> str:
        return f"{self.source_label} ({self.source})"


class PriceListService:
    """Price lists, their lines, and the resolution of a price for a sale."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    def price_lists(self, actor: AnyActor, *, include_inactive: bool = False) -> list[PriceList]:
        actor.require("product.view")
        statement = select(PriceList).where(PriceList.business_id == actor.business_id)
        if not include_inactive:
            statement = statement.where(PriceList.is_active.is_(True))
        return list(
            self._db.scalars(statement.order_by(PriceList.is_default.desc(), PriceList.name))
        )

    def create(
        self,
        actor: AnyActor,
        *,
        code: str,
        name: str,
        currency_code: str,
        is_default: bool = False,
        tax_inclusive: bool = False,
        effective_from: date | None = None,
        effective_to: date | None = None,
    ) -> PriceList:
        actor.require("price_list.manage")
        list_code = validate_code(code, field="code")[:40]
        if not name.strip():
            raise ValidationError("A price list name is required.", field="name")
        if len(currency_code) != 3 or not currency_code.isalpha():
            raise ValidationError("A currency code is three letters.", field="currency_code")
        if effective_from and effective_to and effective_to < effective_from:
            raise ValidationError(
                "The end of a price list cannot precede its beginning.",
                field="effective_to",
            )

        existing = self._db.scalar(
            select(PriceList).where(
                PriceList.business_id == actor.business_id, PriceList.code == list_code
            )
        )
        if existing is not None:
            raise ConflictError(f"A price list with code {list_code!r} already exists.")

        if is_default:
            for other in self.price_lists(actor, include_inactive=True):
                other.is_default = False

        price_list = PriceList(
            business_id=actor.business_id,
            code=list_code,
            name=name.strip(),
            currency_code=currency_code.upper(),
            is_default=is_default,
            tax_inclusive=tax_inclusive,
            effective_from=effective_from,
            effective_to=effective_to,
        )
        self._db.add(price_list)
        self._db.flush()

        self._audit.record(
            actor,
            "price_list_created",
            entity_type="price_list",
            entity_id=price_list.id,
            entity_label=price_list.name,
            after={"code": price_list.code, "currency": price_list.currency_code},
        )
        return price_list

    def set_price(
        self,
        actor: AnyActor,
        *,
        price_list_id: uuid.UUID,
        product_id: uuid.UUID,
        price: Decimal,
        minimum_quantity: Decimal = ZERO,
        unit_id: uuid.UUID | None = None,
    ) -> PriceListLine:
        """Set a product's price on a list, optionally at a quantity break."""
        actor.require("price_list.manage")
        if price < ZERO:
            raise ValidationError("A price cannot be negative.", field="price")
        if minimum_quantity < ZERO:
            raise ValidationError("A quantity break cannot be negative.", field="minimum_quantity")

        price_list = self._require_list(actor, price_list_id)
        product = self._db.scalar(
            select(Product).where(
                Product.business_id == actor.business_id, Product.id == product_id
            )
        )
        if product is None:
            raise NotFoundError("Product not found.")

        line = self._db.scalar(
            select(PriceListLine).where(
                PriceListLine.price_list_fk == price_list.id,
                PriceListLine.product_fk == product_id,
                PriceListLine.minimum_quantity == minimum_quantity,
            )
        )
        if line is None:
            line = PriceListLine(
                business_id=actor.business_id,
                price_list_fk=price_list.id,
                product_fk=product_id,
                minimum_quantity=minimum_quantity,
            )
            self._db.add(line)
        line.price = price
        line.unit_fk = unit_id
        self._db.flush()
        return line

    def lines(self, actor: AnyActor, price_list_id: uuid.UUID) -> list[PriceListLine]:
        actor.require("product.view")
        self._require_list(actor, price_list_id)
        return list(
            self._db.scalars(
                select(PriceListLine)
                .where(PriceListLine.price_list_fk == price_list_id)
                .order_by(PriceListLine.product_fk, PriceListLine.minimum_quantity)
            )
        )

    def resolve(
        self,
        actor: AnyActor,
        *,
        product_id: uuid.UUID,
        customer_id: uuid.UUID | None = None,
        quantity: Decimal = Decimal("1"),
        on: date | None = None,
    ) -> ResolvedPrice | None:
        """Resolve the price for a product, for a customer, at a quantity.

        The order of precedence is fixed, so two people pricing the same line reach the
        same figure: an agreement with this customer, then the customer's assigned
        price list, then the default list, then the product's indicative list price.
        """
        actor.require("product.view")
        effective = on or self._clock.now().date()

        if customer_id is not None:
            agreed = self._customer_price(customer_id, product_id, quantity, effective)
            if agreed is not None:
                return agreed

            customer = self._db.scalar(
                select(Party).where(Party.business_id == actor.business_id, Party.id == customer_id)
            )
            if customer is not None and customer.price_list_fk is not None:
                assigned = self._list_price(customer.price_list_fk, product_id, quantity, effective)
                if assigned is not None:
                    return assigned

        default = self._db.scalar(
            select(PriceList).where(
                PriceList.business_id == actor.business_id,
                PriceList.is_default.is_(True),
                PriceList.is_active.is_(True),
            )
        )
        if default is not None:
            from_default = self._list_price(default.id, product_id, quantity, effective)
            if from_default is not None:
                return from_default

        product = self._db.get(Product, product_id)
        if product is not None and product.list_price is not None:
            return ResolvedPrice(
                price=product.list_price,
                source="product",
                source_label="The product's indicative price",
                currency_code=product.currency_code,
                tax_inclusive=False,
            )
        return None

    def _customer_price(
        self,
        customer_id: uuid.UUID,
        product_id: uuid.UUID,
        quantity: Decimal,
        effective: date,
    ) -> ResolvedPrice | None:
        rows = self._db.scalars(
            select(CustomerPrice)
            .where(
                CustomerPrice.customer_fk == customer_id,
                CustomerPrice.product_fk == product_id,
                CustomerPrice.minimum_quantity <= quantity,
            )
            .order_by(CustomerPrice.minimum_quantity.desc())
        ).all()
        for row in rows:
            if row.effective_from and row.effective_from > effective:
                continue
            if row.effective_to and row.effective_to < effective:
                continue
            return ResolvedPrice(
                price=row.price,
                source="customer_agreement",
                source_label="An agreement with this customer",
                currency_code=row.currency_code,
                tax_inclusive=False,
                minimum_quantity=row.minimum_quantity,
            )
        return None

    def _list_price(
        self,
        price_list_id: uuid.UUID,
        product_id: uuid.UUID,
        quantity: Decimal,
        effective: date,
    ) -> ResolvedPrice | None:
        price_list = self._db.get(PriceList, price_list_id)
        if price_list is None or not price_list.is_active:
            return None
        if price_list.effective_from and price_list.effective_from > effective:
            return None
        if price_list.effective_to and price_list.effective_to < effective:
            return None

        line = self._db.scalar(
            select(PriceListLine)
            .where(
                PriceListLine.price_list_fk == price_list_id,
                PriceListLine.product_fk == product_id,
                PriceListLine.minimum_quantity <= quantity,
            )
            .order_by(PriceListLine.minimum_quantity.desc())
            .limit(1)
        )
        if line is None:
            return None
        return ResolvedPrice(
            price=line.price,
            source="price_list",
            source_label=price_list.name,
            currency_code=price_list.currency_code,
            tax_inclusive=price_list.tax_inclusive,
            minimum_quantity=line.minimum_quantity,
        )

    def _require_list(self, actor: AnyActor, price_list_id: uuid.UUID) -> PriceList:
        price_list = self._db.scalar(
            select(PriceList).where(
                PriceList.business_id == actor.business_id, PriceList.id == price_list_id
            )
        )
        if price_list is None:
            raise NotFoundError("Price list not found.")
        return price_list
