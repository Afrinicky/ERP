"""The stock engine: every change to what is held goes through here. ADR-0004.

An *operation* is one action — a receipt, a transfer dispatch, a count posting. It
writes movement rows, keeps the balances, positions and cost layers they sum to, and
posts one journal entry for the value that moved. Nothing else writes those tables, so
the ledger, the derived records and the general ledger cannot disagree.

Each pool a movement touches — a product in a warehouse — is locked first, by
incrementing its sequence in one statement, and read after. Two operations on the same
pool therefore take turns, and the second sees what the first left.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.orm import Session as DbSession

from nickland.application.context import AnyActor
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.domain.catalogue import ItemClass, TrackingPolicy, ValuationPolicy
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.inventory import (
    COST_OF_GOODS_SOLD,
    SEEDED_LOCATIONS,
    SERIAL_STATE_FOR,
    STATE_ACCOUNT,
    LayerView,
    LocationKind,
    MovementType,
    SerialState,
    StockState,
    consume,
    reaverage,
    split_revaluation,
)
from nickland.domain.ledger import EntryLine, JournalSource, Side, SubledgerKind
from nickland.domain.money import WORKING_PRECISION, ZERO, allocate, money
from nickland.persistence.catalogue_models import Product
from nickland.persistence.inventory_models import (
    Batch,
    CostLayer,
    InventorySettings,
    LayerConsumption,
    StockBalance,
    StockLocation,
    StockMovement,
    StockPosition,
    StockSerial,
)
from nickland.persistence.ledger_models import JournalEntry
from nickland.persistence.models import Business, Warehouse
from nickland.platform.clock import Clock, SystemClock
from nickland.platform.ids import new_id

ACCOUNT_NAMES = {
    "12000": "Inventory",
    "12100": "Goods in quarantine",
    "12200": "Goods in transit",
    "20100": "Other payables",
    "20150": "Goods received not invoiced",
    "30000": "Owner's capital",
    "50000": "Cost of goods sold",
    "51000": "Inventory write-off and shrinkage",
}

POSITION_FIELD = {
    StockState.USABLE: "usable",
    StockState.QUARANTINE: "quarantine",
    StockState.REJECTED: "rejected",
    StockState.IN_TRANSIT: "in_transit",
    StockState.RESERVED: "reserved",
}

SERIAL_AFTER = {
    MovementType.ISSUE: SerialState.ISSUED,
    MovementType.RETURN_TO_SUPPLIER: SerialState.RETURNED_TO_SUPPLIER,
}
"""Where a serial goes when it leaves stock. Anything else that removes it writes it off;
a move between states places it again at once."""

REVERSIBLE = frozenset(
    {
        MovementType.OPENING,
        MovementType.ADJUSTMENT_GAIN,
        MovementType.ADJUSTMENT_LOSS,
        MovementType.DAMAGE,
        MovementType.EXPIRY,
        MovementType.RELOCATION,
        MovementType.CUSTOMER_RETURN,
    }
)
"""Operations a person may reverse. A receipt, a transfer, a count or an issue is
corrected by the document that answers it — a return, a further count — because each is
tied to a document the other party also holds."""


def _is(column: Any, value: object | None) -> ColumnElement[bool]:
    """Equality that treats two absent values as the same place."""
    clause: ColumnElement[bool] = column.is_(None) if value is None else column == value
    return clause


@dataclass(slots=True)
class Portion:
    """Part of a draw: one balance row, or one serial."""

    balance: StockBalance
    quantity: Decimal
    serial: StockSerial | None = None


@dataclass(slots=True)
class Drawn:
    """What a draw took from one place, and from which layers."""

    movement: StockMovement
    quantity: Decimal
    value: Decimal
    batch_id: uuid.UUID | None
    takes: list[tuple[CostLayer, Decimal, Decimal]] = field(default_factory=list)


class StockEngine:
    """Reads the pools, and begins operations on them."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()

    @property
    def session(self) -> DbSession:
        return self._db

    @property
    def clock(self) -> Clock:
        return self._clock

    # -- configuration ----------------------------------------------------------------

    def settings(self, business_id: uuid.UUID) -> InventorySettings:
        found = self._db.scalar(
            select(InventorySettings).where(InventorySettings.business_id == business_id)
        )
        if found is None:
            found = InventorySettings(
                business_id=business_id,
                valuation_policy=ValuationPolicy.WEIGHTED_AVERAGE.value,
                expiry_warning_days=90,
            )
            self._db.add(found)
            self._db.flush()
        return found

    def policy(self, product: Product) -> ValuationPolicy:
        chosen = ValuationPolicy(product.valuation_policy)
        if chosen is ValuationPolicy.BUSINESS_DEFAULT:
            return ValuationPolicy(self.settings(product.business_id).valuation_policy)
        return chosen

    def locations(self, warehouse: Warehouse) -> dict[str, StockLocation]:
        """The warehouse's locations by code, seeding the three every warehouse has."""
        existing = {
            location.code: location
            for location in self._db.scalars(
                select(StockLocation).where(StockLocation.warehouse_fk == warehouse.id)
            ).all()
        }
        for code, name, kind in SEEDED_LOCATIONS:
            if code not in existing:
                location = StockLocation(
                    business_id=warehouse.business_id,
                    warehouse_fk=warehouse.id,
                    code=code,
                    name=name,
                    kind=kind.value,
                )
                self._db.add(location)
                existing[code] = location
        self._db.flush()
        return existing

    def location_of_kind(self, warehouse_id: uuid.UUID, kind: LocationKind) -> StockLocation:
        warehouse = self.warehouse(warehouse_id)
        places = self.locations(warehouse)
        for location in places.values():
            if location.kind == kind.value and location.is_active:
                return location
        raise NotFoundError(f"Warehouse {warehouse.code} has no {kind.value} location.")

    def default_location(self, warehouse_id: uuid.UUID, state: StockState) -> uuid.UUID | None:
        if state is StockState.IN_TRANSIT or state is StockState.RESERVED:
            return None
        kind = LocationKind.STORAGE if state is StockState.USABLE else LocationKind.QUARANTINE
        return self.location_of_kind(warehouse_id, kind).id

    def warehouse(self, warehouse_id: uuid.UUID) -> Warehouse:
        found = self._db.get(Warehouse, warehouse_id)
        if found is None:
            raise NotFoundError("Warehouse not found.")
        return found

    def batch_for(
        self,
        product: Product,
        code: str | None,
        *,
        expires_on: date | None = None,
        manufactured_on: date | None = None,
        manufacturer: str | None = None,
    ) -> Batch | None:
        """The batch a receipt names, created on first sight. ``None`` where the product
        is not batch-tracked and no batch was named."""
        cleaned = (code or "").strip()
        tracking = TrackingPolicy(product.tracking_policy)
        if not cleaned:
            if tracking is TrackingPolicy.BATCH:
                raise ValidationError(
                    f"{product.sku} is batch-tracked; name the batch.", field="batch"
                )
            if product.requires_expiry and expires_on is not None:
                cleaned = f"EXP-{expires_on.isoformat()}"
            else:
                return None
        found = self._db.scalar(
            select(Batch).where(
                Batch.business_id == product.business_id,
                Batch.product_fk == product.id,
                Batch.code == cleaned,
            )
        )
        if found is None:
            if product.requires_expiry and expires_on is None:
                raise ValidationError(
                    f"{product.sku} requires an expiry date on every batch.", field="expiry"
                )
            found = Batch(
                business_id=product.business_id,
                product_fk=product.id,
                code=cleaned,
                expires_on=expires_on,
                manufactured_on=manufactured_on,
                manufacturer=(manufacturer or "").strip() or product.manufacturer,
            )
            self._db.add(found)
            self._db.flush()
        elif expires_on is not None and found.expires_on not in (None, expires_on):
            raise ConflictError(
                f"Batch {cleaned} of {product.sku} is recorded as expiring "
                f"{found.expires_on.isoformat() if found.expires_on else ''}; "
                f"{expires_on.isoformat()} was given.",
                batch=cleaned,
            )
        return found

    # -- reading ----------------------------------------------------------------------

    def position(
        self, business_id: uuid.UUID, product_id: uuid.UUID, warehouse_id: uuid.UUID
    ) -> StockPosition | None:
        return self._db.scalar(
            select(StockPosition).where(
                StockPosition.business_id == business_id,
                StockPosition.product_fk == product_id,
                StockPosition.warehouse_fk == warehouse_id,
            )
        )

    def usable_unexpired(
        self,
        business_id: uuid.UUID,
        product_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        on: date,
        *,
        batch_id: uuid.UUID | None = None,
    ) -> Decimal:
        """Usable stock in batches that have not passed their expiry date."""
        query = (
            select(func.coalesce(func.sum(StockBalance.quantity), 0))
            .select_from(StockBalance)
            .outerjoin(Batch, Batch.id == StockBalance.batch_fk)
            .where(
                StockBalance.business_id == business_id,
                StockBalance.product_fk == product_id,
                StockBalance.warehouse_fk == warehouse_id,
                StockBalance.state == StockState.USABLE.value,
                or_(Batch.id.is_(None), Batch.expires_on.is_(None), Batch.expires_on >= on),
            )
        )
        if batch_id is not None:
            query = query.where(StockBalance.batch_fk == batch_id)
        return money(Decimal(str(self._db.scalar(query) or 0)))

    def available(
        self,
        business_id: uuid.UUID,
        product_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        on: date,
    ) -> Decimal:
        position = self.position(business_id, product_id, warehouse_id)
        reserved = position.reserved if position is not None else ZERO
        usable = self.usable_unexpired(business_id, product_id, warehouse_id, on)
        return money(max(ZERO, usable - reserved))

    # -- operations -------------------------------------------------------------------

    def begin(
        self,
        actor: AnyActor,
        movement_type: MovementType,
        *,
        occurred_on: date,
        source_type: str | None = None,
        source_id: uuid.UUID | None = None,
        reference: str | None = None,
        reason: str | None = None,
        branch_id: uuid.UUID | None = None,
    ) -> Operation:
        if actor.business_id is None:
            raise ConflictError("The business has not been provisioned.")
        return Operation(
            self,
            actor,
            movement_type,
            occurred_on=occurred_on,
            source_type=source_type,
            source_id=source_id,
            reference=reference,
            reason=(reason or "").strip() or None,
            branch_id=branch_id if branch_id is not None else actor.branch_id,
        )

    def movements_of(self, operation_id: uuid.UUID) -> list[StockMovement]:
        return list(
            self._db.scalars(
                select(StockMovement)
                .where(StockMovement.operation_id == operation_id)
                .order_by(StockMovement.created_at, StockMovement.id)
            ).all()
        )

    def reverse(
        self, actor: AnyActor, operation_id: uuid.UUID, *, reason: str, on: date
    ) -> Operation:
        """Write the negation of an operation, while it is still the latest in its pools."""
        if not reason.strip():
            raise ValidationError("A reversal records why.", field="reason")
        originals = [
            movement
            for movement in self.movements_of(operation_id)
            if movement.business_id == actor.business_id
        ]
        if not originals:
            raise NotFoundError("Stock operation not found.")
        kind = MovementType(originals[0].movement_type)
        if kind not in REVERSIBLE:
            raise ConflictError(
                f"A {kind.value.replace('_', ' ')} is corrected by the document that "
                "answers it, not reversed.",
                movement_type=kind.value,
            )
        already = self._db.scalar(
            select(StockMovement.id).where(StockMovement.reverses_operation_id == operation_id)
        )
        if already is not None:
            raise ConflictError("This operation has already been reversed.")
        for movement in originals:
            position = self.position(
                movement.business_id, movement.product_fk, movement.warehouse_fk
            )
            if position is None or position.sequence != movement.sequence:
                raise ConflictError(
                    "Stock has moved through the same place since, so this operation can "
                    "no longer be reversed. Record an adjustment instead.",
                    operation_id=str(operation_id),
                )

        operation = self.begin(
            actor,
            MovementType.REVERSAL,
            occurred_on=on,
            source_type=originals[0].source_type,
            source_id=originals[0].source_id,
            reference=originals[0].reference,
            reason=reason,
        )
        operation.reverses = operation_id
        operation.replay_negated(originals)
        journals = {movement.journal_entry_fk for movement in originals} - {None}
        for journal_id in journals:
            entry = self._db.get(JournalEntry, journal_id)
            if entry is None:
                continue
            for line in entry.lines:
                debit, credit = line.base_debit, line.base_credit
                operation.extra.append(
                    EntryLine(
                        account_code=line.account.code,
                        side=Side.CREDIT if debit > ZERO else Side.DEBIT,
                        amount=debit if debit > ZERO else credit,
                        narration=f"Reversal: {line.narration or ''}".strip(),
                        party_id=line.party_fk,
                        subledger=SubledgerKind(line.subledger) if line.subledger else None,
                    )
                )
        operation.finish(f"Reversal of stock operation: {reason.strip()}")
        return operation


class Operation:
    """One action's movements, and the journal entry that values them."""

    def __init__(
        self,
        engine: StockEngine,
        actor: AnyActor,
        movement_type: MovementType,
        *,
        occurred_on: date,
        source_type: str | None,
        source_id: uuid.UUID | None,
        reference: str | None,
        reason: str | None,
        branch_id: uuid.UUID | None,
    ) -> None:
        self.id = new_id()
        self.engine = engine
        self.actor = actor
        assert actor.business_id is not None
        self.business_id: uuid.UUID = actor.business_id
        self.movement_type = movement_type
        self.occurred_on = occurred_on
        self.source_type = source_type
        self.source_id = source_id
        self.reference = reference
        self.reason = reason
        self.branch_id = branch_id
        self.reverses: uuid.UUID | None = None
        self.movements: list[StockMovement] = []
        self.extra: list[EntryLine] = []
        self.journal: JournalEntry | None = None
        self._db = engine.session
        self._positions: dict[tuple[uuid.UUID, uuid.UUID], StockPosition] = {}
        self._net: dict[str, Decimal] = {}
        self._finished = False

    # -- the public verbs -------------------------------------------------------------

    def add(
        self,
        product: Product,
        warehouse_id: uuid.UUID,
        state: StockState,
        quantity: Decimal,
        value: Decimal,
        *,
        location_id: uuid.UUID | None = None,
        bin_id: uuid.UUID | None = None,
        batch: Batch | None = None,
        serials: Sequence[str] = (),
        source_line_id: uuid.UUID | None = None,
        counter: str | None = None,
        movement_type: MovementType | None = None,
        aged_on: date | None = None,
    ) -> list[StockMovement]:
        """Stock arrives in a state, at a value: a new layer, posted to the state's account.

        ``counter`` names the account credited for the value, where the operation's
        other side is a single account.
        """
        _assert_holds_stock(product)
        if not state.is_physical:
            raise ValidationError("Stock arrives in a physical state.")
        quantity = _positive(quantity)
        value = money(value)
        if value < ZERO:
            raise ValidationError("Stock arrives at a value of zero or more.", field="value")
        if TrackingPolicy(product.tracking_policy) is TrackingPolicy.BATCH and batch is None:
            raise ValidationError(f"{product.sku} is batch-tracked; name the batch.")
        location_id = location_id or self.engine.default_location(warehouse_id, state)
        units = self._serials_in(product, warehouse_id, quantity, serials, batch)

        movements: list[StockMovement] = []
        if units:
            parts = allocate(value, [Decimal(1)] * len(units), WORKING_PRECISION)
            for unit, part in zip(units, parts, strict=True):
                movements.append(
                    self._put(
                        product,
                        warehouse_id,
                        state,
                        Decimal(1),
                        part,
                        location_id=location_id,
                        bin_id=bin_id,
                        batch_id=batch.id if batch else None,
                        serial=unit,
                        source_line_id=source_line_id,
                        movement_type=movement_type,
                    )
                )
                _place_serial(unit, warehouse_id, location_id, bin_id, state)
        else:
            movements.append(
                self._put(
                    product,
                    warehouse_id,
                    state,
                    quantity,
                    value,
                    location_id=location_id,
                    bin_id=bin_id,
                    batch_id=batch.id if batch else None,
                    source_line_id=source_line_id,
                    movement_type=movement_type,
                )
            )
        layer = CostLayer(
            business_id=self.business_id,
            product_fk=product.id,
            warehouse_fk=warehouse_id,
            state=state.value,
            batch_fk=batch.id if batch else None,
            movement_fk=movements[0].id,
            source_type=self.source_type,
            source_line_id=source_line_id,
            aged_on=aged_on or self.occurred_on,
            quantity_received=quantity,
            quantity_remaining=quantity,
            value_received=value,
            value_remaining=value,
        )
        self._db.add(layer)
        self._db.flush()
        self._value(state, value)
        if counter is not None:
            self._value_account(counter, -value)
        self._spread(product, warehouse_id, state, movements[0])
        return movements

    def take(
        self,
        product: Product,
        warehouse_id: uuid.UUID,
        state: StockState,
        quantity: Decimal,
        *,
        location_id: uuid.UUID | None = None,
        bin_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
        serials: Sequence[str] = (),
        exclude_expired: bool = False,
        override_reason: str | None = None,
        counter: str | None = None,
        movement_type: MovementType | None = None,
    ) -> list[Drawn]:
        """Stock leaves a state: drawn from places and layers, posted out of its account.

        With ``exclude_expired`` an expired batch is passed over, or refused where it is
        named, unless an override is recorded. ``counter`` names the account debited.
        """
        _assert_holds_stock(product)
        quantity = _positive(quantity)
        self._lock(product.id, warehouse_id)
        portions = self._portions(
            product,
            warehouse_id,
            state,
            quantity,
            location_id=location_id,
            bin_id=bin_id,
            batch_id=batch_id,
            serials=serials,
            exclude_expired=exclude_expired and not override_reason,
        )
        expired_override = None
        if exclude_expired and override_reason:
            expired_override = override_reason.strip()

        drawn: list[Drawn] = []
        for portion in portions:
            balance = portion.balance
            layers = self._layers(product.id, warehouse_id, state, balance.batch_fk)
            views = [_view(layer) for layer in layers]
            takes = consume(views, portion.quantity)
            by_id = {layer.id: layer for layer in layers}
            value = money(sum((take.value for take in takes), ZERO))
            is_expired = self._expired(balance.batch_fk)
            movement = self._put(
                product,
                warehouse_id,
                state,
                -portion.quantity,
                -value,
                location_id=balance.location_fk,
                bin_id=balance.bin_fk,
                batch_id=balance.batch_fk,
                serial=portion.serial,
                movement_type=movement_type,
                override_reason=expired_override if is_expired else None,
            )
            if portion.serial is not None:
                portion.serial.state = SERIAL_AFTER.get(
                    movement_type or self.movement_type, SerialState.WRITTEN_OFF
                ).value
                portion.serial.warehouse_fk = None
                portion.serial.location_fk = None
                portion.serial.bin_fk = None
            record = Drawn(
                movement=movement,
                quantity=portion.quantity,
                value=value,
                batch_id=balance.batch_fk,
            )
            for take in takes:
                layer = by_id[take.key]  # type: ignore[index]
                layer.quantity_remaining = money(layer.quantity_remaining - take.quantity)
                layer.value_remaining = money(layer.value_remaining - take.value)
                self._db.add(
                    LayerConsumption(
                        business_id=self.business_id,
                        movement_fk=movement.id,
                        layer_fk=layer.id,
                        quantity=take.quantity,
                        value=take.value,
                    )
                )
                record.takes.append((layer, take.quantity, take.value))
            drawn.append(record)
            self._value(state, -value)
            if counter is not None:
                self._value_account(counter, value)
        self._db.flush()
        return drawn

    def move(
        self,
        product: Product,
        quantity: Decimal,
        *,
        from_warehouse: uuid.UUID,
        from_state: StockState,
        to_warehouse: uuid.UUID,
        to_state: StockState,
        from_location: uuid.UUID | None = None,
        from_bin: uuid.UUID | None = None,
        to_location: uuid.UUID | None = None,
        to_bin: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
        serials: Sequence[str] = (),
        exclude_expired: bool = False,
        movement_type: MovementType | None = None,
        arriving_type: MovementType | None = None,
    ) -> Decimal:
        """Stock changes state or warehouse, carrying its layers and their age with it."""
        drawn = self.take(
            product,
            from_warehouse,
            from_state,
            quantity,
            location_id=from_location,
            bin_id=from_bin,
            batch_id=batch_id,
            serials=serials,
            exclude_expired=exclude_expired,
            movement_type=movement_type,
        )
        destination = to_location or self.engine.default_location(to_warehouse, to_state)
        moved = ZERO
        for part in drawn:
            serial = (
                self._db.get(StockSerial, part.movement.serial_fk)
                if part.movement.serial_fk
                else None
            )
            arriving = self._put(
                product,
                to_warehouse,
                to_state,
                part.quantity,
                part.value,
                location_id=destination,
                bin_id=to_bin,
                batch_id=part.batch_id,
                serial=serial,
                movement_type=arriving_type or movement_type,
            )
            if serial is not None:
                _place_serial(serial, to_warehouse, destination, to_bin, to_state)
            for layer, taken, value in part.takes:
                self._db.add(
                    CostLayer(
                        business_id=self.business_id,
                        product_fk=product.id,
                        warehouse_fk=to_warehouse,
                        state=to_state.value,
                        batch_fk=layer.batch_fk,
                        movement_fk=arriving.id,
                        origin_layer_fk=layer.id,
                        source_type=layer.source_type,
                        source_line_id=layer.source_line_id,
                        aged_on=layer.aged_on,
                        quantity_received=taken,
                        quantity_remaining=taken,
                        value_received=value,
                        value_remaining=value,
                    )
                )
            self._value(to_state, part.value)
            moved = money(moved + part.value)
            self._db.flush()
            self._spread(product, to_warehouse, to_state, arriving)
        return moved

    def relocate(
        self,
        product: Product,
        warehouse_id: uuid.UUID,
        quantity: Decimal,
        *,
        to_location: uuid.UUID,
        to_bin: uuid.UUID | None = None,
        from_location: uuid.UUID | None = None,
        from_bin: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
        serials: Sequence[str] = (),
        state: StockState = StockState.USABLE,
    ) -> None:
        """Stock changes place within a warehouse. No value moves; the layers stay."""
        _assert_holds_stock(product)
        quantity = _positive(quantity)
        self._lock(product.id, warehouse_id)
        portions = self._portions(
            product,
            warehouse_id,
            state,
            quantity,
            location_id=from_location,
            bin_id=from_bin,
            batch_id=batch_id,
            serials=serials,
            exclude_expired=False,
        )
        for portion in portions:
            balance = portion.balance
            if balance.location_fk == to_location and balance.bin_fk == to_bin:
                raise ValidationError("Stock is relocated to a different place.")
            self._put(
                product,
                warehouse_id,
                state,
                -portion.quantity,
                ZERO,
                location_id=balance.location_fk,
                bin_id=balance.bin_fk,
                batch_id=balance.batch_fk,
                serial=portion.serial,
            )
            self._put(
                product,
                warehouse_id,
                state,
                portion.quantity,
                ZERO,
                location_id=to_location,
                bin_id=to_bin,
                batch_id=balance.batch_fk,
                serial=portion.serial,
            )
            if portion.serial is not None:
                _place_serial(portion.serial, warehouse_id, to_location, to_bin, state)

    def reserve(
        self,
        product: Product,
        warehouse_id: uuid.UUID,
        quantity: Decimal,
        *,
        batch_id: uuid.UUID | None = None,
    ) -> StockMovement:
        """Hold usable stock for an order. Never expired stock, and never twice."""
        _assert_holds_stock(product)
        quantity = _positive(quantity)
        position = self._lock(product.id, warehouse_id)
        usable = self.engine.usable_unexpired(
            self.business_id, product.id, warehouse_id, self.occurred_on
        )
        free = money(usable - position.reserved)
        if batch_id is not None:
            in_batch = self.engine.usable_unexpired(
                self.business_id, product.id, warehouse_id, self.occurred_on, batch_id=batch_id
            )
            free = min(free, in_batch)
        if quantity > free:
            raise ConflictError(
                f"{product.sku}: {max(free, ZERO).normalize()} is available to reserve; "
                f"{quantity.normalize()} was asked for.",
                available=str(max(free, ZERO)),
            )
        return self._put(
            product,
            warehouse_id,
            StockState.RESERVED,
            quantity,
            ZERO,
            batch_id=batch_id,
            movement_type=MovementType.RESERVATION,
        )

    def release(
        self,
        product: Product,
        warehouse_id: uuid.UUID,
        quantity: Decimal,
        *,
        batch_id: uuid.UUID | None = None,
        movement_type: MovementType = MovementType.RELEASE,
    ) -> StockMovement:
        """Give a hold back, or consume it as the stock it held leaves."""
        quantity = _positive(quantity)
        position = self._lock(product.id, warehouse_id)
        if quantity > position.reserved:
            raise ConflictError("More cannot be released than is reserved.")
        return self._put(
            product,
            warehouse_id,
            StockState.RESERVED,
            -quantity,
            ZERO,
            batch_id=batch_id,
            movement_type=movement_type,
        )

    def revalue(
        self,
        source_line_ids: Iterable[uuid.UUID],
        amount: Decimal,
        *,
        issued_to: str = COST_OF_GOODS_SOLD,
    ) -> tuple[Decimal, Decimal]:
        """A change in what received stock cost, carried to wherever that stock now is.

        The share for units still held goes into their layers, wherever they have moved;
        the share for units already gone goes to ``issued_to``. Returns
        ``(to_layers, to_issued)``. Where the receipt carried no stock at all — a non-stock
        line — the whole goes to ``issued_to``.
        """
        amount = money(amount)
        ids = list(source_line_ids)
        if amount == ZERO:
            return ZERO, ZERO
        layers = (
            list(
                self._db.scalars(
                    select(CostLayer)
                    .where(
                        CostLayer.business_id == self.business_id,
                        CostLayer.source_line_id.in_(ids),
                    )
                    .order_by(CostLayer.aged_on, CostLayer.id)
                ).all()
            )
            if ids
            else []
        )
        received = money(
            sum(
                (layer.quantity_received for layer in layers if layer.origin_layer_fk is None),
                ZERO,
            )
        )
        if received <= ZERO:
            self._value_account(issued_to, amount)
            return ZERO, amount
        live = [layer for layer in layers if layer.quantity_remaining > ZERO]
        remaining = money(sum((layer.quantity_remaining for layer in live), ZERO))
        to_layers, to_issued = split_revaluation(amount, received, min(remaining, received))
        if live and to_layers != ZERO:
            shares = allocate(
                to_layers, [layer.quantity_remaining for layer in live], WORKING_PRECISION
            )
            touched: dict[tuple[uuid.UUID, uuid.UUID, str], StockMovement] = {}
            for layer, share in zip(live, shares, strict=True):
                if share == ZERO:
                    continue
                if layer.value_remaining + share < ZERO:
                    raise ConflictError(
                        "The reduction is more than the stock it relates to is carried at."
                    )
                product = self._db.get(Product, layer.product_fk)
                assert product is not None
                state = StockState(layer.state)
                movement = self._record(
                    product,
                    layer.warehouse_fk,
                    state,
                    ZERO,
                    share,
                    location_id=None,
                    bin_id=None,
                    batch_id=layer.batch_fk,
                    serial=None,
                    source_line_id=layer.source_line_id,
                    movement_type=MovementType.REVALUATION,
                )
                layer.value_remaining = money(layer.value_remaining + share)
                self._db.add(
                    LayerConsumption(
                        business_id=self.business_id,
                        movement_fk=movement.id,
                        layer_fk=layer.id,
                        quantity=ZERO,
                        value=-share,
                    )
                )
                self._value(state, share)
                touched[(layer.product_fk, layer.warehouse_fk, layer.state)] = movement
            self._db.flush()
            for (product_id, warehouse_id, state_value), movement in touched.items():
                product = self._db.get(Product, product_id)
                assert product is not None
                self._spread(product, warehouse_id, StockState(state_value), movement)
        elif to_layers != ZERO:
            to_issued = amount
            to_layers = ZERO
        if to_issued != ZERO:
            self._value_account(issued_to, to_issued)
        return to_layers, to_issued

    def debit(self, account: str, amount: Decimal) -> None:
        self._value_account(account, money(amount))

    def credit(self, account: str, amount: Decimal) -> None:
        self._value_account(account, -money(amount))

    def finish(
        self,
        narration: str,
        *,
        source: JournalSource = JournalSource.INVENTORY,
        idempotency_key: str | None = None,
        attachment_ids: list[str] | None = None,
        posting_date: date | None = None,
    ) -> JournalEntry | None:
        """Post the value that moved, and link every movement to the entry."""
        if self._finished:
            raise ConflictError("This stock operation is already complete.")
        self._finished = True
        lines: list[EntryLine] = []
        for account, net in sorted(self._net.items()):
            net = money(net)
            if net == ZERO:
                continue
            lines.append(
                EntryLine(
                    account_code=account,
                    side=Side.DEBIT if net > ZERO else Side.CREDIT,
                    amount=abs(net),
                    narration=ACCOUNT_NAMES.get(account),
                )
            )
        lines.extend(self.extra)
        self._db.flush()
        if not lines:
            return None
        business = self._db.get(Business, self.business_id)
        outcome = JournalService(self._db, clock=self.engine.clock).post(
            self.actor,
            JournalDraft(
                posting_date=posting_date or self.occurred_on,
                narration=narration,
                lines=lines,
                source=source,
                source_reference=self.reference,
                source_id=self.source_id or self.id,
                idempotency_key=idempotency_key or f"stock:{self.id}",
                currency_code=business.base_currency if business else None,
                attachment_ids=attachment_ids or [],
                branch_id=self.branch_id,
            ),
        )
        self.journal = outcome.entry
        for movement in self.movements:
            movement.journal_entry_fk = outcome.entry.id
        self._db.flush()
        return outcome.entry

    # -- reversal ---------------------------------------------------------------------

    def replay_negated(self, originals: list[StockMovement]) -> None:
        """Write each original row negated, and give back exactly what each layer gave."""
        counterpart: dict[uuid.UUID, StockMovement] = {}
        for original in originals:
            product = self._db.get(Product, original.product_fk)
            assert product is not None
            self._lock(product.id, original.warehouse_fk)
            serial = self._db.get(StockSerial, original.serial_fk) if original.serial_fk else None
            counterpart[original.id] = self._put(
                product,
                original.warehouse_fk,
                StockState(original.state),
                -original.quantity,
                -original.value,
                location_id=original.location_fk,
                bin_id=original.bin_fk,
                batch_id=original.batch_fk,
                serial=serial,
                movement_type=MovementType.REVERSAL,
                source_line_id=original.source_line_id,
            )
        ids = [original.id for original in originals]
        consumptions = list(
            self._db.scalars(
                select(LayerConsumption).where(LayerConsumption.movement_fk.in_(ids))
            ).all()
        )
        for used in consumptions:
            layer = self._db.get(CostLayer, used.layer_fk)
            assert layer is not None
            layer.quantity_remaining = money(layer.quantity_remaining + used.quantity)
            layer.value_remaining = money(layer.value_remaining + used.value)
            self._db.add(
                LayerConsumption(
                    business_id=self.business_id,
                    movement_fk=counterpart[used.movement_fk].id,
                    layer_fk=layer.id,
                    quantity=-used.quantity,
                    value=-used.value,
                )
            )
        created = list(
            self._db.scalars(select(CostLayer).where(CostLayer.movement_fk.in_(ids))).all()
        )
        for layer in created:
            if (
                layer.quantity_remaining != layer.quantity_received
                or layer.value_remaining != layer.value_received
            ):
                raise ConflictError("The stock this operation brought in has since moved.")
            self._db.add(
                LayerConsumption(
                    business_id=self.business_id,
                    movement_fk=counterpart[layer.movement_fk].id,
                    layer_fk=layer.id,
                    quantity=layer.quantity_remaining,
                    value=layer.value_remaining,
                )
            )
            layer.quantity_remaining = ZERO
            layer.value_remaining = ZERO
        # Serials: arrivals undone first, then departures, so a unit that moved within
        # the operation ends where it began.
        for original in sorted(originals, key=lambda row: row.quantity < ZERO):
            if original.serial_fk is None:
                continue
            serial = self._db.get(StockSerial, original.serial_fk)
            assert serial is not None
            if original.quantity > ZERO:
                serial.state = SerialState.WRITTEN_OFF.value
                serial.warehouse_fk = None
                serial.location_fk = None
                serial.bin_fk = None
            else:
                _place_serial(
                    serial,
                    original.warehouse_fk,
                    original.location_fk,
                    original.bin_fk,
                    StockState(original.state),
                )
        self._db.flush()

    # -- internals --------------------------------------------------------------------

    def _lock(self, product_id: uuid.UUID, warehouse_id: uuid.UUID) -> StockPosition:
        """Take the pool's lock by advancing its sequence, then read it as it stands."""
        key = (product_id, warehouse_id)
        held = self._positions.get(key)
        if held is not None:
            return held
        self._db.flush()
        position = self.engine.position(self.business_id, product_id, warehouse_id)
        if position is None:
            position = StockPosition(
                business_id=self.business_id,
                product_fk=product_id,
                warehouse_fk=warehouse_id,
                usable=ZERO,
                quarantine=ZERO,
                rejected=ZERO,
                in_transit=ZERO,
                reserved=ZERO,
                sequence=0,
            )
            self._db.add(position)
            self._db.flush()
        self._db.execute(
            update(StockPosition)
            .where(StockPosition.id == position.id)
            .values(sequence=StockPosition.sequence + 1)
        )
        self._db.refresh(position)
        self._positions[key] = position
        return position

    def _put(
        self,
        product: Product,
        warehouse_id: uuid.UUID,
        state: StockState,
        quantity: Decimal,
        value: Decimal,
        *,
        location_id: uuid.UUID | None = None,
        bin_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
        serial: StockSerial | None = None,
        source_line_id: uuid.UUID | None = None,
        movement_type: MovementType | None = None,
        override_reason: str | None = None,
    ) -> StockMovement:
        """One movement row, with the position and the balance it changes.

        Value is recorded on the row but posted by the verb that called this, so a
        reversal, which mirrors the original journal instead, posts nothing twice."""
        position = self._lock(product.id, warehouse_id)
        attribute = POSITION_FIELD[state]
        after = money(getattr(position, attribute) + quantity)
        if after < ZERO:
            raise ConflictError(
                f"{product.sku}: not enough {state.value.replace('_', ' ')} stock. Stock is "
                "never allowed below zero.",
                state=state.value,
            )
        if state is StockState.USABLE and quantity < ZERO and after < position.reserved:
            raise ConflictError(
                f"{product.sku}: what remains would not cover what is reserved for orders. "
                "Release a reservation first, or issue against it.",
                reserved=str(position.reserved),
            )
        setattr(position, attribute, after)
        if state.is_physical:
            self._balance(product.id, warehouse_id, location_id, bin_id, batch_id, state, quantity)
        movement = self._record(
            product,
            warehouse_id,
            state,
            quantity,
            value,
            location_id=location_id,
            bin_id=bin_id,
            batch_id=batch_id,
            serial=serial,
            source_line_id=source_line_id,
            movement_type=movement_type,
            override_reason=override_reason,
        )
        return movement

    def _record(
        self,
        product: Product,
        warehouse_id: uuid.UUID,
        state: StockState,
        quantity: Decimal,
        value: Decimal,
        *,
        location_id: uuid.UUID | None,
        bin_id: uuid.UUID | None,
        batch_id: uuid.UUID | None,
        serial: StockSerial | None,
        source_line_id: uuid.UUID | None = None,
        movement_type: MovementType | None = None,
        override_reason: str | None = None,
    ) -> StockMovement:
        position = self._lock(product.id, warehouse_id)
        movement = StockMovement(
            id=new_id(),
            business_id=self.business_id,
            branch_id=self.branch_id,
            operation_id=self.id,
            sequence=position.sequence,
            movement_type=(movement_type or self.movement_type).value,
            state=state.value,
            product_fk=product.id,
            warehouse_fk=warehouse_id,
            location_fk=location_id,
            bin_fk=bin_id,
            batch_fk=batch_id,
            serial_fk=serial.id if serial is not None else None,
            quantity=money(quantity),
            value=money(value),
            unit_cost=money(abs(value) / abs(quantity)) if quantity != ZERO else ZERO,
            source_type=self.source_type,
            source_id=self.source_id,
            source_line_id=source_line_id,
            reference=self.reference,
            reason=self.reason,
            reverses_operation_id=self.reverses,
            override_reason=override_reason,
            override_by_id=self.actor.user_id if override_reason else None,
            occurred_on=self.occurred_on,
            recorded_by_id=self.actor.user_id,
            created_at=self.engine.clock.now(),
        )
        self._db.add(movement)
        # Written at once: layers and consumptions refer to it, and nothing relates
        # them for the unit of work to order the inserts by.
        self._db.flush([movement])
        self.movements.append(movement)
        return movement

    def _balance(
        self,
        product_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        location_id: uuid.UUID | None,
        bin_id: uuid.UUID | None,
        batch_id: uuid.UUID | None,
        state: StockState,
        quantity: Decimal,
    ) -> None:
        self._db.flush()
        balance = self._db.scalar(
            select(StockBalance).where(
                StockBalance.business_id == self.business_id,
                StockBalance.product_fk == product_id,
                StockBalance.warehouse_fk == warehouse_id,
                StockBalance.state == state.value,
                _is(StockBalance.location_fk, location_id),
                _is(StockBalance.bin_fk, bin_id),
                _is(StockBalance.batch_fk, batch_id),
            )
        )
        if balance is None:
            balance = StockBalance(
                business_id=self.business_id,
                product_fk=product_id,
                warehouse_fk=warehouse_id,
                location_fk=location_id,
                bin_fk=bin_id,
                batch_fk=batch_id,
                state=state.value,
                quantity=ZERO,
            )
            self._db.add(balance)
        after = money(balance.quantity + quantity)
        if after < ZERO:
            raise ConflictError("Not enough stock is held in that place.", state=state.value)
        balance.quantity = after

    def _portions(
        self,
        product: Product,
        warehouse_id: uuid.UUID,
        state: StockState,
        quantity: Decimal,
        *,
        location_id: uuid.UUID | None,
        bin_id: uuid.UUID | None,
        batch_id: uuid.UUID | None,
        serials: Sequence[str],
        exclude_expired: bool,
    ) -> list[Portion]:
        """Which balances, or which serials, a draw comes from: earliest expiry first."""
        if TrackingPolicy(product.tracking_policy) is TrackingPolicy.SERIAL or serials:
            return self._serial_portions(product, warehouse_id, state, quantity, serials)
        self._db.flush()
        query = (
            select(StockBalance)
            .outerjoin(Batch, Batch.id == StockBalance.batch_fk)
            .where(
                StockBalance.business_id == self.business_id,
                StockBalance.product_fk == product.id,
                StockBalance.warehouse_fk == warehouse_id,
                StockBalance.state == state.value,
                StockBalance.quantity > 0,
            )
        )
        if location_id is not None:
            query = query.where(StockBalance.location_fk == location_id)
        if bin_id is not None:
            query = query.where(StockBalance.bin_fk == bin_id)
        if batch_id is not None:
            query = query.where(StockBalance.batch_fk == batch_id)
            if exclude_expired and self._expired(batch_id):
                batch = self._db.get(Batch, batch_id)
                raise ConflictError(
                    f"Batch {batch.code if batch else ''} of {product.sku} expired on "
                    f"{batch.expires_on.isoformat() if batch and batch.expires_on else ''}. "
                    "Issuing it needs an authorised override.",
                    batch=batch.code if batch else None,
                )
        if exclude_expired:
            query = query.where(
                or_(
                    Batch.id.is_(None),
                    Batch.expires_on.is_(None),
                    Batch.expires_on >= self.occurred_on,
                )
            )
        balances = list(
            self._db.scalars(
                query.order_by(
                    Batch.expires_on.is_(None),
                    Batch.expires_on,
                    Batch.code,
                    StockBalance.location_fk,
                    StockBalance.id,
                )
            ).all()
        )
        portions: list[Portion] = []
        remaining = quantity
        for balance in balances:
            if remaining <= ZERO:
                break
            portion = min(balance.quantity, remaining)
            portions.append(Portion(balance=balance, quantity=portion))
            remaining = money(remaining - portion)
        if remaining > ZERO:
            held = money(quantity - remaining)
            where = "there" if location_id or bin_id or batch_id else "in that warehouse"
            raise ConflictError(
                f"{product.sku}: {held.normalize()} {state.value.replace('_', ' ')} is held "
                f"{where}; {quantity.normalize()} was asked for. Stock is never allowed "
                "below zero.",
                held=str(held),
            )
        return portions

    def _serial_portions(
        self,
        product: Product,
        warehouse_id: uuid.UUID,
        state: StockState,
        quantity: Decimal,
        serials: Sequence[str],
    ) -> list[Portion]:
        numbers = _clean_serials(serials)
        if Decimal(len(numbers)) != quantity:
            raise ValidationError(
                f"{product.sku} is serial-tracked; name one serial number per unit "
                f"({quantity.normalize()} needed, {len(numbers)} given).",
                field="serials",
            )
        portions: list[Portion] = []
        expected = SERIAL_STATE_FOR[state].value
        for number in numbers:
            serial = self._db.scalar(
                select(StockSerial).where(
                    StockSerial.business_id == self.business_id,
                    StockSerial.product_fk == product.id,
                    StockSerial.serial_number == number,
                )
            )
            if serial is None or serial.warehouse_fk != warehouse_id or serial.state != expected:
                raise ConflictError(
                    f"Serial {number} of {product.sku} is not held here as "
                    f"{state.value.replace('_', ' ')} stock.",
                    serial=number,
                )
            self._db.flush()
            balance = self._db.scalar(
                select(StockBalance).where(
                    StockBalance.business_id == self.business_id,
                    StockBalance.product_fk == product.id,
                    StockBalance.warehouse_fk == warehouse_id,
                    StockBalance.state == state.value,
                    _is(StockBalance.location_fk, serial.location_fk),
                    _is(StockBalance.bin_fk, serial.bin_fk),
                    _is(StockBalance.batch_fk, serial.batch_fk),
                )
            )
            if balance is None or balance.quantity < 1:
                raise ConflictError(f"Serial {number} has no balance behind it.")
            portions.append(Portion(balance=balance, quantity=Decimal(1), serial=serial))
        return portions

    def _serials_in(
        self,
        product: Product,
        warehouse_id: uuid.UUID,
        quantity: Decimal,
        serials: Sequence[str],
        batch: Batch | None,
    ) -> list[StockSerial]:
        tracked = TrackingPolicy(product.tracking_policy) is TrackingPolicy.SERIAL
        numbers = _clean_serials(serials)
        if not tracked and not numbers:
            return []
        if Decimal(len(numbers)) != quantity:
            raise ValidationError(
                f"{product.sku} is serial-tracked; record one serial number per unit "
                f"({quantity.normalize()} received, {len(numbers)} given).",
                field="serials",
            )
        if len(set(numbers)) != len(numbers):
            raise ValidationError("A serial number appears twice.", field="serials")
        units: list[StockSerial] = []
        held = {
            SerialState.IN_STOCK.value,
            SerialState.QUARANTINE.value,
            SerialState.REJECTED.value,
            SerialState.IN_TRANSIT.value,
        }
        for number in numbers:
            serial = self._db.scalar(
                select(StockSerial).where(
                    StockSerial.business_id == self.business_id,
                    StockSerial.product_fk == product.id,
                    StockSerial.serial_number == number,
                )
            )
            if serial is not None and serial.state in held:
                raise ConflictError(
                    f"Serial {number} of {product.sku} is already held.", serial=number
                )
            if serial is None:
                serial = StockSerial(
                    id=new_id(),
                    business_id=self.business_id,
                    product_fk=product.id,
                    serial_number=number,
                    batch_fk=batch.id if batch else None,
                )
                self._db.add(serial)
            units.append(serial)
        self._db.flush()
        return units

    def _layers(
        self,
        product_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        state: StockState,
        batch_id: object = ...,
    ) -> list[CostLayer]:
        self._db.flush()
        query = select(CostLayer).where(
            CostLayer.business_id == self.business_id,
            CostLayer.product_fk == product_id,
            CostLayer.warehouse_fk == warehouse_id,
            CostLayer.state == state.value,
            CostLayer.quantity_remaining > 0,
        )
        if batch_id is not ...:
            query = query.where(_is(CostLayer.batch_fk, batch_id))
        return list(self._db.scalars(query.order_by(CostLayer.aged_on, CostLayer.id)).all())

    def _spread(
        self,
        product: Product,
        warehouse_id: uuid.UUID,
        state: StockState,
        movement: StockMovement,
    ) -> None:
        """In a moving-average pool, re-spread the pool's value after value enters it."""
        if self.engine.policy(product) is not ValuationPolicy.WEIGHTED_AVERAGE:
            return
        layers = self._layers(product.id, warehouse_id, state)
        if len(layers) < 2:
            return
        by_id = {layer.id: layer for layer in layers}
        for share in reaverage([_view(layer) for layer in layers]):
            layer = by_id[share.key]  # type: ignore[index]
            delta = money(layer.value_remaining - share.value)
            if delta == ZERO:
                continue
            layer.value_remaining = share.value
            self._db.add(
                LayerConsumption(
                    business_id=self.business_id,
                    movement_fk=movement.id,
                    layer_fk=layer.id,
                    quantity=ZERO,
                    value=delta,
                )
            )
        self._db.flush()

    def _expired(self, batch_id: uuid.UUID | None) -> bool:
        if batch_id is None:
            return False
        batch = self._db.get(Batch, batch_id)
        return bool(batch and batch.expires_on and batch.expires_on < self.occurred_on)

    def _value(self, state: StockState, amount: Decimal) -> None:
        if state.is_valued:
            self._value_account(STATE_ACCOUNT[state], amount)

    def _value_account(self, account: str, amount: Decimal) -> None:
        self._net[account] = money(self._net.get(account, ZERO) + amount)


# --------------------------------------------------------------------------------------


def _view(layer: CostLayer) -> LayerView:
    return LayerView(
        key=layer.id,
        quantity=layer.quantity_remaining,
        value=layer.value_remaining,
        aged_on=layer.aged_on,
        order=str(layer.id),
    )


def _positive(quantity: Decimal) -> Decimal:
    value = money(quantity)
    if value <= ZERO:
        raise ValidationError("A quantity is above zero.", field="quantity")
    return value


def _assert_holds_stock(product: Product) -> None:
    if not ItemClass(product.item_class).holds_stock:
        raise ConflictError(
            f"{product.sku} is a {product.item_class.replace('_', '-')} item and carries no stock.",
            sku=product.sku,
        )


def _clean_serials(serials: Sequence[str]) -> list[str]:
    numbers: list[str] = []
    for entry in serials:
        for part in str(entry).replace(",", "\n").splitlines():
            cleaned = part.strip()
            if cleaned:
                numbers.append(cleaned)
    return numbers


def _place_serial(
    serial: StockSerial,
    warehouse_id: uuid.UUID | None,
    location_id: uuid.UUID | None,
    bin_id: uuid.UUID | None,
    state: StockState,
) -> None:
    serial.warehouse_fk = warehouse_id
    serial.location_fk = location_id
    serial.bin_fk = bin_id
    serial.state = SERIAL_STATE_FOR[state].value


def holds_stock(product: Product | None) -> bool:
    return product is not None and ItemClass(product.item_class).holds_stock


__all__ = ["REVERSIBLE", "Drawn", "Operation", "StockEngine", "holds_stock"]
