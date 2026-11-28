"""Units of measure and their conversions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.catalogue import UnitKind, convert
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.organisation import validate_code
from nickland.persistence.catalogue_models import Product, UnitConversionRow, UnitOfMeasure
from nickland.platform.clock import Clock, SystemClock


@dataclass(frozen=True, slots=True)
class SeededUnit:
    """A unit supplied at first run."""

    code: str
    name: str
    plural: str
    kind: UnitKind
    base: str | None
    factor: str
    decimals: int


SEEDED_UNITS: tuple[SeededUnit, ...] = (
    SeededUnit("ea", "Each", "Each", UnitKind.COUNT, None, "1", 0),
    SeededUnit("pack", "Pack", "Packs", UnitKind.COUNT, "ea", "1", 0),
    SeededUnit("box", "Box", "Boxes", UnitKind.COUNT, "ea", "1", 0),
    SeededUnit("carton", "Carton", "Cartons", UnitKind.COUNT, "ea", "1", 0),
    SeededUnit("dozen", "Dozen", "Dozens", UnitKind.COUNT, "ea", "12", 0),
    SeededUnit("pair", "Pair", "Pairs", UnitKind.COUNT, "ea", "2", 0),
    SeededUnit("g", "Gram", "Grams", UnitKind.MASS, None, "1", 3),
    SeededUnit("kg", "Kilogram", "Kilograms", UnitKind.MASS, "g", "1000", 3),
    SeededUnit("ml", "Millilitre", "Millilitres", UnitKind.VOLUME, None, "1", 2),
    SeededUnit("l", "Litre", "Litres", UnitKind.VOLUME, "ml", "1000", 3),
    SeededUnit("mm", "Millimetre", "Millimetres", UnitKind.LENGTH, None, "1", 1),
    SeededUnit("m", "Metre", "Metres", UnitKind.LENGTH, "mm", "1000", 3),
    SeededUnit("hour", "Hour", "Hours", UnitKind.TIME, None, "1", 2),
    SeededUnit("day", "Day", "Days", UnitKind.TIME, "hour", "24", 2),
)
"""A conventional starting set. Pack, box and carton default to a factor of one,
because how many a box holds is a property of the product, not of the word."""


class UnitService:
    """Units of measure, and conversion between them."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    def seed(self, business_id: uuid.UUID) -> int:
        """Create the conventional units. Run once, at provisioning."""
        existing = self._db.scalar(
            select(UnitOfMeasure.id).where(UnitOfMeasure.business_id == business_id).limit(1)
        )
        if existing is not None:
            return 0

        by_code: dict[str, UnitOfMeasure] = {}
        for seeded in SEEDED_UNITS:
            unit = UnitOfMeasure(
                business_id=business_id,
                code=seeded.code,
                name=seeded.name,
                plural_name=seeded.plural,
                kind=seeded.kind.value,
                factor=Decimal(seeded.factor),
                decimal_places=seeded.decimals,
                is_system=True,
            )
            self._db.add(unit)
            self._db.flush()
            by_code[seeded.code] = unit

        for seeded in SEEDED_UNITS:
            unit = by_code[seeded.code]
            unit.base_fk = by_code[seeded.base].id if seeded.base else unit.id
        self._db.flush()
        return len(SEEDED_UNITS)

    def units(self, actor: AnyActor, *, include_inactive: bool = False) -> list[UnitOfMeasure]:
        actor.require("product.view")
        statement = select(UnitOfMeasure).where(UnitOfMeasure.business_id == actor.business_id)
        if not include_inactive:
            statement = statement.where(UnitOfMeasure.is_active.is_(True))
        return list(self._db.scalars(statement.order_by(UnitOfMeasure.kind, UnitOfMeasure.name)))

    def create(
        self,
        actor: AnyActor,
        *,
        code: str,
        name: str,
        kind: UnitKind | str,
        base_code: str | None = None,
        factor: Decimal = Decimal("1"),
        plural_name: str | None = None,
        decimal_places: int = 0,
    ) -> UnitOfMeasure:
        """Create a unit, optionally derived from a base unit of the same dimension."""
        actor.require("product.manage")
        unit_code = validate_code(code, field="code")[:16]
        if not name.strip():
            raise ValidationError("A unit name is required.", field="name")
        if factor <= 0:
            raise ValidationError("A conversion factor must be greater than zero.", field="factor")

        if self._by_code(actor.business_id, unit_code) is not None:
            raise ConflictError(f"A unit with code {unit_code!r} already exists.")

        dimension = UnitKind(str(kind))
        base: UnitOfMeasure | None = None
        if base_code:
            base = self._require_by_code(actor.business_id, base_code)
            if base.kind != dimension.value:
                raise ValidationError(
                    f"{base.name} measures {base.kind}, so a {dimension.value} unit "
                    "cannot derive from it. A conversion never crosses a dimension.",
                    field="base_code",
                )

        unit = UnitOfMeasure(
            business_id=actor.business_id,
            code=unit_code,
            name=name.strip(),
            plural_name=(plural_name or name).strip(),
            kind=dimension.value,
            factor=factor,
            decimal_places=decimal_places,
        )
        self._db.add(unit)
        self._db.flush()
        # A base unit points at itself, so the identifier must exist first.
        unit.base_fk = base.id if base else unit.id
        self._db.flush()

        self._audit.record(
            actor,
            "unit_created",
            entity_type="unit_of_measure",
            entity_id=unit.id,
            entity_label=unit.code,
            after={"code": unit.code, "name": unit.name, "kind": unit.kind},
        )
        return unit

    def convert_quantity(
        self,
        actor: AnyActor,
        *,
        amount: Decimal,
        from_unit_id: uuid.UUID,
        to_unit_id: uuid.UUID,
        product_id: uuid.UUID | None = None,
    ) -> Decimal:
        """Convert between units, preferring a product-specific factor where one exists.

        A box holds 24 of one product and 12 of another, so the product's own factor
        governs where it is recorded.
        """
        if from_unit_id == to_unit_id:
            return amount

        source = self._require(actor, from_unit_id)
        target = self._require(actor, to_unit_id)
        if source.kind != target.kind:
            raise ValidationError(
                f"{source.name} measures {source.kind} and {target.name} measures "
                f"{target.kind}. A conversion never crosses a dimension.",
                field="unit",
            )

        from_factor = self._factor_for(source, product_id)
        to_factor = self._factor_for(target, product_id)
        return convert(amount, from_factor=from_factor, to_factor=to_factor)

    def set_product_factor(
        self,
        actor: AnyActor,
        *,
        product_id: uuid.UUID,
        unit_id: uuid.UUID,
        factor: Decimal,
    ) -> UnitConversionRow:
        """Record how many stock units one of a unit contains, for one product."""
        actor.require("product.manage")
        if factor <= 0:
            raise ValidationError("A conversion factor must be greater than zero.", field="factor")

        product = self._db.scalar(
            select(Product).where(
                Product.business_id == actor.business_id, Product.id == product_id
            )
        )
        if product is None:
            raise NotFoundError("Product not found.")
        if unit_id == product.stock_unit_fk and factor != Decimal("1"):
            raise ValidationError(
                "The stock unit converts to itself at a factor of one.", field="factor"
            )

        row = self._db.scalar(
            select(UnitConversionRow).where(
                UnitConversionRow.product_fk == product_id,
                UnitConversionRow.unit_fk == unit_id,
            )
        )
        if row is None:
            row = UnitConversionRow(
                business_id=actor.business_id,
                product_fk=product_id,
                unit_fk=unit_id,
                factor=factor,
            )
            self._db.add(row)
        else:
            row.factor = factor
        self._db.flush()
        return row

    def product_factors(self, product_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
        rows = self._db.scalars(
            select(UnitConversionRow).where(UnitConversionRow.product_fk == product_id)
        ).all()
        return {row.unit_fk: row.factor for row in rows}

    def _factor_for(self, unit: UnitOfMeasure, product_id: uuid.UUID | None) -> Decimal:
        if product_id is not None:
            row = self._db.scalar(
                select(UnitConversionRow).where(
                    UnitConversionRow.product_fk == product_id,
                    UnitConversionRow.unit_fk == unit.id,
                )
            )
            if row is not None:
                return row.factor
        return unit.factor

    def _require(self, actor: AnyActor, unit_id: uuid.UUID) -> UnitOfMeasure:
        unit = self._db.scalar(
            select(UnitOfMeasure).where(
                UnitOfMeasure.business_id == actor.business_id, UnitOfMeasure.id == unit_id
            )
        )
        if unit is None:
            raise NotFoundError("Unit of measure not found.")
        return unit

    def _by_code(self, business_id: uuid.UUID, code: str) -> UnitOfMeasure | None:
        return self._db.scalar(
            select(UnitOfMeasure).where(
                UnitOfMeasure.business_id == business_id, UnitOfMeasure.code == code
            )
        )

    def _require_by_code(self, business_id: uuid.UUID, code: str) -> UnitOfMeasure:
        unit = self._by_code(business_id, code)
        if unit is None:
            raise NotFoundError(f"Unit of measure not found: {code!r}")
        return unit
