"""What is held, where, what it is worth, and whether every record agrees. PRD §6.11.

The reconciliation here is the proof behind two exit criteria: that the balances,
positions and layers are the ledger summed, and that the layers are worth what the
inventory control accounts say.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.context import AnyActor
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.application.services.stock import POSITION_FIELD, StockEngine
from nickland.domain.errors import ConflictError
from nickland.domain.inventory import (
    INVENTORY_ACCOUNTS,
    STATE_ACCOUNT,
    ExpiryStatus,
    StockState,
    expiry_status,
    unit_cost,
)
from nickland.domain.money import ZERO, money
from nickland.domain.orders import RECEIVABLE_STATES
from nickland.persistence.catalogue_models import Product
from nickland.persistence.inventory_models import (
    Batch,
    CostLayer,
    StockBalance,
    StockMovement,
    StockPosition,
)
from nickland.persistence.ledger_models import Account
from nickland.persistence.models import Business, Warehouse
from nickland.persistence.order_models import PurchaseOrder, PurchaseOrderLine
from nickland.platform.clock import Clock, SystemClock, business_date


@dataclass(slots=True)
class PositionRow:
    product_id: uuid.UUID
    sku: str
    name: str
    warehouse_id: uuid.UUID
    warehouse: str
    usable: Decimal
    quarantine: Decimal
    rejected: Decimal
    in_transit: Decimal
    reserved: Decimal
    available: Decimal
    incoming: Decimal
    value: Decimal
    unit_cost: Decimal
    reorder_level: Decimal | None
    tracking: str


@dataclass(slots=True)
class ExpiryRow:
    batch_id: uuid.UUID
    batch: str
    product_id: uuid.UUID
    sku: str
    name: str
    warehouse: str
    expires_on: date | None
    quantity: Decimal
    status: ExpiryStatus
    days: int | None


@dataclass(slots=True)
class ValuationRow:
    product_id: uuid.UUID
    sku: str
    name: str
    warehouse: str
    state: str
    account: str
    quantity: Decimal
    value: Decimal
    unit_cost: Decimal


@dataclass(slots=True)
class AccountAgreement:
    account: str
    name: str
    layers: Decimal
    ledger: Decimal

    @property
    def difference(self) -> Decimal:
        return money(self.ledger - self.layers)


@dataclass(slots=True)
class Valuation:
    rows: list[ValuationRow]
    accounts: list[AccountAgreement]

    @property
    def total(self) -> Decimal:
        return money(sum((row.value for row in self.rows), ZERO))

    @property
    def agrees(self) -> bool:
        return all(account.difference == ZERO for account in self.accounts)


@dataclass(slots=True)
class Discrepancy:
    record: str
    key: str
    expected: Decimal
    found: Decimal


@dataclass(slots=True)
class Reconciliation:
    movements: int
    discrepancies: list[Discrepancy] = field(default_factory=list)

    @property
    def agrees(self) -> bool:
        return not self.discrepancies


class InventoryReportService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._engine = StockEngine(session, clock=self._clock)

    # -- positions --------------------------------------------------------------------

    def positions(
        self,
        actor: AnyActor,
        *,
        warehouse_id: uuid.UUID | None = None,
        query: str | None = None,
        include_empty: bool = False,
    ) -> list[PositionRow]:
        actor.require("stock.view")
        business_id = _business_of(actor)
        today = self._today(business_id)
        statement = (
            select(StockPosition, Product, Warehouse)
            .join(Product, Product.id == StockPosition.product_fk)
            .join(Warehouse, Warehouse.id == StockPosition.warehouse_fk)
            .where(StockPosition.business_id == business_id)
        )
        if warehouse_id is not None:
            statement = statement.where(StockPosition.warehouse_fk == warehouse_id)
        if query and query.strip():
            needle = f"%{query.strip().lower()}%"
            statement = statement.where(Product.search_text.like(needle))
        values = self._layer_totals(business_id)
        incoming = self._incoming(business_id)
        rows: list[PositionRow] = []
        for position, product, warehouse in self._db.execute(
            statement.order_by(Product.sku, Warehouse.code)
        ).all():
            held = position.usable + position.quarantine + position.rejected + position.in_transit
            if not include_empty and held == ZERO and position.reserved == ZERO:
                continue
            usable_value = values.get((product.id, warehouse.id, StockState.USABLE.value), ZERO)
            total_value = sum(
                (
                    values.get((product.id, warehouse.id, state.value), ZERO)
                    for state in STATE_ACCOUNT
                ),
                ZERO,
            )
            usable_unexpired = self._engine.usable_unexpired(
                business_id, product.id, warehouse.id, today
            )
            rows.append(
                PositionRow(
                    product_id=product.id,
                    sku=product.sku,
                    name=product.name,
                    warehouse_id=warehouse.id,
                    warehouse=warehouse.code,
                    usable=position.usable,
                    quarantine=position.quarantine,
                    rejected=position.rejected,
                    in_transit=position.in_transit,
                    reserved=position.reserved,
                    available=money(max(ZERO, usable_unexpired - position.reserved)),
                    incoming=incoming.get((product.id, warehouse.id), ZERO),
                    value=money(total_value),
                    unit_cost=unit_cost(position.usable, usable_value),
                    reorder_level=product.reorder_level,
                    tracking=product.tracking_policy,
                )
            )
        return rows

    def low_stock(self, actor: AnyActor) -> list[PositionRow]:
        """Products at or below their reorder level, counting what is already on order."""
        actor.require("stock.view")
        business_id = _business_of(actor)
        rows = {
            (row.product_id, row.warehouse_id): row
            for row in self.positions(actor, include_empty=True)
        }
        products = self._db.scalars(
            select(Product).where(
                Product.business_id == business_id,
                Product.reorder_level.is_not(None),
                Product.is_active,
                Product.item_class == "stocked",
            )
        ).all()
        found: list[PositionRow] = []
        for product in products:
            mine = [row for key, row in rows.items() if key[0] == product.id]
            available = sum((row.available + row.incoming for row in mine), ZERO)
            assert product.reorder_level is not None
            if available <= product.reorder_level:
                if mine:
                    found.extend(mine)
                else:
                    found.append(
                        PositionRow(
                            product_id=product.id,
                            sku=product.sku,
                            name=product.name,
                            warehouse_id=uuid.UUID(int=0),
                            warehouse="—",
                            usable=ZERO,
                            quarantine=ZERO,
                            rejected=ZERO,
                            in_transit=ZERO,
                            reserved=ZERO,
                            available=ZERO,
                            incoming=ZERO,
                            value=ZERO,
                            unit_cost=ZERO,
                            reorder_level=product.reorder_level,
                            tracking=product.tracking_policy,
                        )
                    )
        return found

    # -- expiry -----------------------------------------------------------------------

    def expiry(self, actor: AnyActor, *, within_days: int | None = None) -> list[ExpiryRow]:
        actor.require("stock.view")
        business_id = _business_of(actor)
        today = self._today(business_id)
        warning = self._engine.settings(business_id).expiry_warning_days
        horizon = within_days if within_days is not None else warning
        rows: list[ExpiryRow] = []
        for row in self._batch_rows(
            business_id,
            today,
            warning,
            states=[StockState.USABLE.value, StockState.QUARANTINE.value],
            expiring_by=today + timedelta(days=horizon),
        ):
            rows.append(row)
        rows.sort(key=lambda row: (row.expires_on or date.max, row.sku))
        return rows

    def batches(self, actor: AnyActor, product_id: uuid.UUID) -> list[ExpiryRow]:
        """Every batch of a product still held, earliest expiry first: what to issue from."""
        actor.require("stock.view")
        business_id = _business_of(actor)
        today = self._today(business_id)
        warning = self._engine.settings(business_id).expiry_warning_days
        rows = self._batch_rows(
            business_id,
            today,
            warning,
            states=[StockState.USABLE.value],
            product_id=product_id,
        )
        return sorted(
            (row for row in rows if row.quantity > ZERO),
            key=lambda row: (row.expires_on or date.max, row.batch),
        )

    # -- movements --------------------------------------------------------------------

    def movements(
        self,
        actor: AnyActor,
        *,
        product_id: uuid.UUID | None = None,
        warehouse_id: uuid.UUID | None = None,
        operation_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> list[StockMovement]:
        actor.require("stock.view")
        statement = select(StockMovement).where(StockMovement.business_id == _business_of(actor))
        if product_id is not None:
            statement = statement.where(StockMovement.product_fk == product_id)
        if warehouse_id is not None:
            statement = statement.where(StockMovement.warehouse_fk == warehouse_id)
        if operation_id is not None:
            statement = statement.where(StockMovement.operation_id == operation_id)
        return list(
            self._db.scalars(
                statement.order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(
                    max(min(limit, 1000), 1)
                )
            ).all()
        )

    # -- valuation --------------------------------------------------------------------

    def valuation(self, actor: AnyActor) -> Valuation:
        """The layers summed by product, warehouse and state, against the accounts."""
        actor.require("stock.view")
        business_id = _business_of(actor)
        statement = (
            select(
                CostLayer.product_fk,
                CostLayer.warehouse_fk,
                CostLayer.state,
                func.sum(CostLayer.quantity_remaining),
                func.sum(CostLayer.value_remaining),
            )
            .where(CostLayer.business_id == business_id)
            .group_by(CostLayer.product_fk, CostLayer.warehouse_fk, CostLayer.state)
        )
        products = {
            product.id: product
            for product in self._db.scalars(
                select(Product).where(Product.business_id == business_id)
            ).all()
        }
        warehouses = {
            warehouse.id: warehouse
            for warehouse in self._db.scalars(
                select(Warehouse).where(Warehouse.business_id == business_id)
            ).all()
        }
        rows: list[ValuationRow] = []
        by_account: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for product_id, warehouse_id, state, quantity, value in self._db.execute(statement):
            held = _decimal(quantity)
            worth = _decimal(value)
            account = STATE_ACCOUNT[StockState(state)]
            by_account[account] = money(by_account[account] + worth)
            if held == ZERO and worth == ZERO:
                continue
            product = products[product_id]
            rows.append(
                ValuationRow(
                    product_id=product_id,
                    sku=product.sku,
                    name=product.name,
                    warehouse=warehouses[warehouse_id].code,
                    state=state,
                    account=account,
                    quantity=held,
                    value=worth,
                    unit_cost=unit_cost(held, worth),
                )
            )
        rows.sort(key=lambda row: (row.sku, row.warehouse, row.state))
        ledger = LedgerReportService(self._db, clock=self._clock)
        accounts: list[AccountAgreement] = []
        for code in sorted(INVENTORY_ACCOUNTS):
            account = self._db.scalar(
                select(Account).where(Account.business_id == business_id, Account.code == code)
            )
            if account is None:
                continue
            accounts.append(
                AccountAgreement(
                    account=code,
                    name=account.name,
                    layers=money(by_account.get(code, ZERO)),
                    ledger=money(
                        # Every posting, whatever its date: the layers hold them all.
                        ledger.account_balance(business_id, account.id, as_at=date.max)
                    ),
                )
            )
        return Valuation(rows=rows, accounts=accounts)

    # -- the ledger, summed -----------------------------------------------------------

    def reconcile(self, actor: AnyActor) -> Reconciliation:
        """Recompute balances, positions and layer totals from the movements alone."""
        actor.require("stock.view")
        business_id = _business_of(actor)
        movements = self._db.scalars(
            select(StockMovement).where(StockMovement.business_id == business_id)
        ).all()
        balance_sum: dict[tuple[object, ...], Decimal] = defaultdict(lambda: ZERO)
        position_sum: dict[tuple[object, ...], Decimal] = defaultdict(lambda: ZERO)
        value_sum: dict[tuple[object, ...], Decimal] = defaultdict(lambda: ZERO)
        layer_quantity_sum: dict[tuple[object, ...], Decimal] = defaultdict(lambda: ZERO)
        for movement in movements:
            state = StockState(movement.state)
            pool = (movement.product_fk, movement.warehouse_fk)
            position_sum[(*pool, state.value)] += movement.quantity
            if state.is_physical:
                balance_sum[
                    (
                        *pool,
                        movement.location_fk,
                        movement.bin_fk,
                        movement.batch_fk,
                        state.value,
                    )
                ] += movement.quantity
                layer_quantity_sum[(*pool, state.value, movement.batch_fk)] += movement.quantity
                value_sum[(*pool, state.value)] += movement.value
        result = Reconciliation(movements=len(movements))

        found_balances: dict[tuple[object, ...], Decimal] = defaultdict(lambda: ZERO)
        for balance in self._db.scalars(
            select(StockBalance).where(StockBalance.business_id == business_id)
        ).all():
            found_balances[
                (
                    balance.product_fk,
                    balance.warehouse_fk,
                    balance.location_fk,
                    balance.bin_fk,
                    balance.batch_fk,
                    balance.state,
                )
            ] += balance.quantity
        _compare(result, "balance", balance_sum, found_balances)

        found_positions: dict[tuple[object, ...], Decimal] = defaultdict(lambda: ZERO)
        for position in self._db.scalars(
            select(StockPosition).where(StockPosition.business_id == business_id)
        ).all():
            for state, attribute in POSITION_FIELD.items():
                found_positions[(position.product_fk, position.warehouse_fk, state.value)] += (
                    getattr(position, attribute)
                )
        _compare(result, "position", position_sum, found_positions)

        found_values: dict[tuple[object, ...], Decimal] = defaultdict(lambda: ZERO)
        found_layer_quantity: dict[tuple[object, ...], Decimal] = defaultdict(lambda: ZERO)
        for layer in self._db.scalars(
            select(CostLayer).where(CostLayer.business_id == business_id)
        ).all():
            found_values[(layer.product_fk, layer.warehouse_fk, layer.state)] += (
                layer.value_remaining
            )
            found_layer_quantity[
                (layer.product_fk, layer.warehouse_fk, layer.state, layer.batch_fk)
            ] += layer.quantity_remaining
        _compare(result, "layer value", value_sum, found_values)
        _compare(result, "layer quantity", layer_quantity_sum, found_layer_quantity)
        return result

    # -- helpers ----------------------------------------------------------------------

    def _batch_rows(
        self,
        business_id: uuid.UUID,
        today: date,
        warning: int,
        *,
        states: list[str],
        product_id: uuid.UUID | None = None,
        expiring_by: date | None = None,
    ) -> list[ExpiryRow]:
        statement = (
            select(
                StockBalance.batch_fk, StockBalance.warehouse_fk, func.sum(StockBalance.quantity)
            )
            .join(Batch, Batch.id == StockBalance.batch_fk)
            .where(StockBalance.business_id == business_id, StockBalance.state.in_(states))
            .group_by(StockBalance.batch_fk, StockBalance.warehouse_fk)
        )
        if product_id is not None:
            statement = statement.where(Batch.product_fk == product_id)
        if expiring_by is not None:
            statement = statement.where(
                Batch.expires_on.is_not(None), Batch.expires_on <= expiring_by
            )
        rows: list[ExpiryRow] = []
        for batch_id, warehouse_id, quantity in self._db.execute(statement).all():
            held = _decimal(quantity)
            if held <= ZERO:
                continue
            batch = self._db.get(Batch, batch_id)
            warehouse = self._db.get(Warehouse, warehouse_id)
            assert batch is not None and warehouse is not None
            product = self._db.get(Product, batch.product_fk)
            assert product is not None
            rows.append(
                ExpiryRow(
                    batch_id=batch.id,
                    batch=batch.code,
                    product_id=product.id,
                    sku=product.sku,
                    name=product.name,
                    warehouse=warehouse.code,
                    expires_on=batch.expires_on,
                    quantity=held,
                    status=expiry_status(batch.expires_on, today, warning),
                    days=(batch.expires_on - today).days if batch.expires_on else None,
                )
            )
        return rows

    def _layer_totals(self, business_id: uuid.UUID) -> dict[tuple[object, ...], Decimal]:
        statement = (
            select(
                CostLayer.product_fk,
                CostLayer.warehouse_fk,
                CostLayer.state,
                func.sum(CostLayer.value_remaining),
            )
            .where(CostLayer.business_id == business_id)
            .group_by(CostLayer.product_fk, CostLayer.warehouse_fk, CostLayer.state)
        )
        return {
            (product, warehouse, state): _decimal(value)
            for product, warehouse, state, value in self._db.execute(statement)
        }

    def _incoming(self, business_id: uuid.UUID) -> dict[tuple[uuid.UUID, uuid.UUID], Decimal]:
        """Issued purchase orders yet to deliver: incoming is read from procurement."""
        statement = (
            select(PurchaseOrderLine, PurchaseOrder.warehouse_fk)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.order_fk)
            .where(
                PurchaseOrder.business_id == business_id,
                PurchaseOrder.state.in_([state.value for state in RECEIVABLE_STATES]),
                PurchaseOrderLine.product_fk.is_not(None),
            )
        )
        totals: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = defaultdict(lambda: ZERO)
        for line, warehouse_id in self._db.execute(statement).all():
            outstanding = money(line.quantity_ordered - line.quantity_received)
            if outstanding > ZERO and line.product_fk is not None and warehouse_id is not None:
                totals[(line.product_fk, warehouse_id)] += outstanding
        return totals

    def _today(self, business_id: uuid.UUID) -> date:
        business = self._db.get(Business, business_id)
        timezone = business.timezone if business is not None else "UTC"
        return business_date(self._clock.now(), timezone)


def _compare(
    result: Reconciliation,
    record: str,
    expected: dict[tuple[object, ...], Decimal],
    found: dict[tuple[object, ...], Decimal],
) -> None:
    for key in set(expected) | set(found):
        want = money(expected.get(key, ZERO))
        have = money(found.get(key, ZERO))
        if want != have:
            result.discrepancies.append(
                Discrepancy(
                    record=record,
                    key="/".join(str(part) for part in key),
                    expected=want,
                    found=have,
                )
            )


def _decimal(value: object) -> Decimal:
    return money(Decimal(str(value or 0)))


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
