"""The register against a real database. PRD §6.10, AC-09, AC-10, ADR-0009.

The exit criteria this file exists to hold:

1. A cash sale completes quickly from scan to receipt and posts revenue, tax, tender
   movement and cost of goods sold through the ledger.
2. Cash sales complete with no network interface available.
3. An initiated but unconfirmed electronic payment leaves the sale incomplete and the
   tender pending.
4. A duplicated payment callback posts once only.
5. Shift close reconciles expected to counted tender, and a variance requires a reason
   and sign-off.
6. A refund is impossible without authorisation and is fully traceable to the sale.
"""

from __future__ import annotations

import socket
import time
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.catalogue import CatalogueService, ProductDraft
from nickland.application.services.inventory import (
    InventoryService,
    OpeningDraft,
    StockLineDraft,
)
from nickland.application.services.parties import PartyDraft, PartyService
from nickland.application.services.pos import (
    PosService,
    ProviderStatus,
    RefundDraft,
    RefundLineDraft,
)
from nickland.application.services.units import UnitService
from nickland.documents.service import DocumentRenderService
from nickland.domain.errors import (
    ConflictError,
    PermissionDeniedError,
    StateTransitionError,
    ValidationError,
)
from nickland.domain.inventory import StockState
from nickland.domain.pos import SaleState, ShiftState, TenderState
from nickland.persistence.inventory_models import StockBalance
from nickland.persistence.ledger_models import Account, JournalEntry, JournalLine
from nickland.persistence.pos_models import PosTender
from nickland.persistence.tax_models import TaxAssessment
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

D = Decimal


@pytest.fixture
def world(db: Session, clock: FixedClock, admin: Actor, provisioned: dict[str, uuid.UUID]) -> dict:
    """Two priced, barcoded items on the shelf and a registered customer."""
    warehouse = provisioned["warehouse_id"]
    unit = UnitService(db, clock=clock).units(admin)[0]
    catalogue = CatalogueService(db, clock=clock)
    gloves = catalogue.create_product(
        admin,
        ProductDraft(
            sku="GLV-100",
            name="Nitrile gloves, box of 100",
            stock_unit_id=unit.id,
            list_price=D("100"),
            barcodes=[("6001234567899", "ean_13")],
        ),
    )
    swabs = catalogue.create_product(
        admin,
        ProductDraft(
            sku="SWB-020",
            name="Alcohol swabs",
            stock_unit_id=unit.id,
            list_price=D("12.50"),
        ),
    )
    db.flush()
    InventoryService(db, clock=clock).open_stock(
        admin,
        OpeningDraft(
            warehouse_id=warehouse,
            lines=[
                StockLineDraft(product_id=gloves.id, quantity=D("20"), unit_cost=D("60")),
                StockLineDraft(product_id=swabs.id, quantity=D("50"), unit_cost=D("5")),
            ],
        ),
    )
    customer = PartyService(db, clock=clock).create(
        admin, PartyDraft(name="Ridge Community Pharmacy", is_customer=True)
    )
    return {"gloves": gloves, "swabs": swabs, "customer": customer, "warehouse": warehouse}


@pytest.fixture
def pos(db: Session, clock: FixedClock) -> PosService:
    return PosService(db, clock=clock)


@pytest.fixture
def shift(pos: PosService, admin: Actor):  # type: ignore[no-untyped-def]
    return pos.open_shift(admin, opening_float=D("200"))


def postings(db: Session, entry_id: uuid.UUID | None) -> dict[str, tuple[Decimal, Decimal]]:
    """Debits and credits of one entry, by account code."""
    assert entry_id is not None
    rows = db.execute(
        select(Account.code, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
        .join(Account, Account.id == JournalLine.account_fk)
        .where(JournalLine.entry_fk == entry_id)
        .group_by(Account.code)
    ).all()
    return {code: (D(str(debit)), D(str(credit))) for code, debit, credit in rows}


def on_hand(db: Session, product_id: uuid.UUID, state: StockState = StockState.USABLE) -> Decimal:
    value = db.scalar(
        select(func.coalesce(func.sum(StockBalance.quantity), 0)).where(
            StockBalance.product_fk == product_id, StockBalance.state == state.value
        )
    )
    return D(str(value))


def ring_up(pos: PosService, admin: Actor, world: dict):  # type: ignore[no-untyped-def]
    """Two boxes of gloves by barcode and four swabs by code: 250.00 net."""
    sale = pos.current_sale(admin)
    pos.scan(admin, sale.id, "6001234567899")
    pos.scan(admin, sale.id, "6001234567899")
    pos.scan(admin, sale.id, "SWB-020", quantity=D("4"))
    return sale


class TestCashSale:
    def test_it_completes_quickly_and_posts_everything_in_one_entry(
        self, db: Session, pos: PosService, admin: Actor, world: dict, shift, clock: FixedClock
    ) -> None:
        started = time.perf_counter()
        sale = ring_up(pos, admin, world)
        assert sale.net_total == D("250")
        assert sale.tax_total == D("54.75")  # levies 6% on the net, VAT 15% on net + levies
        assert sale.total == D("304.75")
        pos.tender(admin, sale.id, method="cash", cash_received=D("310"))
        pos.complete(admin, sale.id)
        receipt = DocumentRenderService(db, clock=clock).issue_for_pos_sale(admin, sale.id)
        elapsed = time.perf_counter() - started
        assert elapsed < 5.0

        assert sale.state == SaleState.COMPLETED
        assert sale.number is not None
        assert receipt.number == sale.number
        tender = sale.tenders[0]
        assert tender.amount == D("304.75") and tender.change_given == D("5.25")

        posted = postings(db, sale.journal_entry_fk)
        assert posted["10100"] == (D("304.75"), D("0"))  # the cash kept, net of change
        assert posted["40100"] == (D("0"), D("250"))
        assert posted["21000"][1] == D("54.75")
        assert posted["50000"] == (D("140"), D("0"))  # 2 x 60 + 4 x 5
        assert posted["12000"] == (D("0"), D("140"))
        assert on_hand(db, world["gloves"].id) == D("18")
        assert on_hand(db, world["swabs"].id) == D("46")

        recorded = db.scalars(
            select(TaxAssessment).where(TaxAssessment.source_id == sale.id)
        ).all()
        assert recorded

    def test_the_receipt_prints_the_tender_and_the_change(
        self, db: Session, pos: PosService, admin: Actor, world: dict, shift, clock: FixedClock
    ) -> None:
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="cash", cash_received=D("310"))
        pos.complete(admin, sale.id)
        text = DocumentRenderService(db, clock=clock).pos_receipt_text(admin, sale.id)
        assert sale.number in text
        assert "304.75" in text and "5.25" in text and "Change" in text
        assert "Cash" in text

    def test_it_completes_with_no_network(
        self,
        db: Session,
        pos: PosService,
        admin: Actor,
        world: dict,
        shift,
        clock: FixedClock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bind = db.get_bind()
        if bind.dialect.name != "sqlite":
            pytest.skip("The PostgreSQL engine is itself reached over the network.")

        def refused(*_args: object, **_kwargs: object) -> None:
            raise OSError("Network is unreachable")

        monkeypatch.setattr(socket.socket, "connect", refused)
        monkeypatch.setattr(socket, "create_connection", refused)
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="cash", cash_received=D("400"))
        pos.complete(admin, sale.id)
        DocumentRenderService(db, clock=clock).issue_for_pos_sale(admin, sale.id)
        assert sale.state == SaleState.COMPLETED

    def test_completing_twice_posts_once(
        self, db: Session, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="cash", cash_received=D("304.75"))
        pos.complete(admin, sale.id)
        entry = sale.journal_entry_fk
        pos.complete(admin, sale.id)
        assert sale.journal_entry_fk == entry
        assert on_hand(db, world["gloves"].id) == D("18")

    def test_a_short_tender_does_not_complete(
        self, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="cash", cash_received=D("300"))
        assert pos.due(sale) == D("4.75")
        with pytest.raises(ConflictError):
            pos.complete(admin, sale.id)
        pos.tender(admin, sale.id, method="cash", cash_received=D("5"))
        pos.complete(admin, sale.id)
        assert sale.tenders[1].change_given == D("0.25")

    def test_quick_cash_amounts_come_from_the_service(
        self, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        assert pos.suggested_cash(admin, sale) == [D("304.75"), D("400")]


class TestTheCart:
    def test_an_unknown_code_is_refused(self, pos: PosService, admin: Actor, shift) -> None:
        sale = pos.current_sale(admin)
        with pytest.raises(Exception, match="No item carries"):
            pos.scan(admin, sale.id, "0000000")

    def test_a_quantity_of_zero_removes_the_line(
        self, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        swabs = next(line for line in sale.lines if line.product_fk == world["swabs"].id)
        pos.set_quantity(admin, sale.id, swabs.id, D("0"))
        assert len(sale.lines) == 1
        assert sale.net_total == D("200")

    def test_overrides_and_discounts_need_their_permission(
        self, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        line = sale.lines[0]
        cashier = make_actor(admin.business_id, permissions={"pos.operate"})
        with pytest.raises(PermissionDeniedError):
            pos.discount(cashier, sale.id, line.id, rate=D("0.10"))
        with pytest.raises(PermissionDeniedError):
            pos.override_price(cashier, sale.id, line.id, unit_price=D("1"), reason="Friend")
        pos.discount(admin, sale.id, line.id, rate=D("0.10"))
        assert line.discount == D("20") and line.net == D("180")
        with pytest.raises(ValidationError):
            pos.override_price(admin, sale.id, line.id, unit_price=D("90"), reason=" ")
        pos.override_price(admin, sale.id, line.id, unit_price=D("90"), reason="Price match")
        assert line.net == D("162")

    def test_a_parked_sale_resumes_where_it_was(
        self, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        pos.park(admin, sale.id, label="Pharmacy runner")
        assert pos.current_sale(admin).id != sale.id
        assert [item.id for item in pos.parked(admin)] == [sale.id]
        pos.resume(admin, sale.id)
        assert pos.current_sale(admin).id == sale.id

    def test_an_account_sale_names_the_customer(
        self, db: Session, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        with pytest.raises(ConflictError):
            pos.tender(admin, sale.id, method="on_account")
        pos.set_customer(admin, sale.id, world["customer"].id)
        pos.tender(admin, sale.id, method="on_account")
        pos.complete(admin, sale.id)
        line = db.scalar(
            select(JournalLine)
            .join(Account, Account.id == JournalLine.account_fk)
            .where(JournalLine.entry_fk == sale.journal_entry_fk, Account.code == "11000")
        )
        assert line is not None and line.party_fk == world["customer"].id

    def test_the_register_needs_an_open_shift(self, pos: PosService, admin: Actor) -> None:
        with pytest.raises(ConflictError, match="Open a shift"):
            pos.current_sale(admin)


class TestElectronicTenders:
    def test_a_pending_payment_leaves_the_sale_incomplete(
        self, db: Session, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="mobile_money")
        tender = sale.tenders[0]
        assert tender.state == TenderState.PENDING
        with pytest.raises(ConflictError, match="awaiting confirmation"):
            pos.complete(admin, sale.id)
        assert sale.state == SaleState.OPEN
        assert sale.journal_entry_fk is None and sale.number is None
        assert on_hand(db, world["gloves"].id) == D("20")

    def test_a_confirmed_payment_completes_the_sale_into_the_wallet(
        self, db: Session, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="mobile_money")
        with pytest.raises(ValidationError):
            pos.confirm_tender(admin, sale.tenders[0].id, provider_reference=" ")
        pos.confirm_tender(admin, sale.tenders[0].id, provider_reference="MP261105.0930.C4411")
        pos.complete(admin, sale.id)
        assert postings(db, sale.journal_entry_fk)["10300"] == (D("304.75"), D("0"))

    def test_a_failed_payment_frees_the_balance_for_another_tender(
        self, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="card")
        pos.fail_tender(admin, sale.tenders[0].id, reason="Declined by issuer")
        assert pos.due(sale) == D("304.75")
        with pytest.raises(StateTransitionError):
            pos.confirm_tender(admin, sale.tenders[0].id, provider_reference="X")
        pos.tender(admin, sale.id, method="cash", cash_received=D("310"))
        pos.complete(admin, sale.id)
        assert sale.state == SaleState.COMPLETED

    def test_a_duplicated_callback_posts_once(
        self, db: Session, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="mobile_money")
        key = sale.tenders[0].idempotency_key
        confirmed = ProviderStatus(
            idempotency_key=key, state=TenderState.CONFIRMED, provider_reference="MP-1"
        )
        pos.provider_update(admin, confirmed)
        pos.complete(admin, sale.id)
        entries = db.scalar(select(func.count(JournalEntry.id)))
        pos.provider_update(admin, confirmed)
        pos.provider_update(admin, confirmed)
        pos.complete(admin, sale.id)
        assert db.scalar(select(func.count(JournalEntry.id))) == entries
        tender = db.get(PosTender, sale.tenders[0].id)
        assert tender is not None and tender.confirmation_source == "callback"

    def test_a_timeout_reported_as_pending_changes_nothing(
        self, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="mobile_money")
        pos.provider_update(
            admin,
            ProviderStatus(idempotency_key=sale.tenders[0].idempotency_key, state=TenderState.PENDING),
        )
        assert sale.tenders[0].state == TenderState.PENDING


class TestShiftClose:
    def test_a_balanced_drawer_closes_on_the_count(
        self, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="cash", cash_received=D("310"))
        pos.complete(admin, sale.id)
        assert pos.drawer(shift).expected == D("504.75")
        # 504.75 = 2 x 200 + 100 + 2 x 2 + 0.50 + 0.20 + 0.05
        closed = pos.submit_count(
            admin,
            shift.id,
            denominations={"200": 2, "100": 1, "2": 2, "0.50": 1, "0.20": 1, "0.05": 1},
        )
        assert closed.state == ShiftState.CLOSED
        assert closed.variance == D("0") and closed.variance_journal_fk is None

    def test_a_variance_needs_a_reason_and_a_sign_off(
        self, db: Session, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="cash", cash_received=D("310"))
        pos.complete(admin, sale.id)
        count = {"200": 2, "100": 1}
        with pytest.raises(ValidationError, match="Record why"):
            pos.submit_count(admin, shift.id, denominations=count)
        submitted = pos.submit_count(
            admin, shift.id, denominations=count, variance_reason="Change given short"
        )
        assert submitted.state == ShiftState.SUBMITTED
        assert submitted.variance == D("-4.75")
        cashier = make_actor(admin.business_id, permissions={"pos.operate", "pos.shift_manage"})
        with pytest.raises(PermissionDeniedError):
            pos.approve_shift(cashier, shift.id)
        closed = pos.approve_shift(admin, shift.id)
        assert closed.state == ShiftState.CLOSED and closed.approved_by_id == admin.user_id
        posted = postings(db, closed.variance_journal_fk)
        assert posted["60900"] == (D("4.75"), D("0"))
        assert posted["10100"] == (D("0"), D("4.75"))

    def test_a_pending_payment_is_abandoned_only_with_a_reason(
        self, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="mobile_money")
        pos.void_open(admin, sale.id, reason="Customer left before the prompt")
        with pytest.raises(ValidationError, match="awaiting confirmation"):
            pos.submit_count(admin, shift.id, denominations={"200": 1})
        submitted = pos.submit_count(
            admin, shift.id, denominations={"200": 1}, variance_reason="Wallet never prompted"
        )
        assert submitted.state == ShiftState.SUBMITTED
        assert sale.tenders[0].state == TenderState.ABANDONED

    def test_cash_out_is_posted_and_reduces_the_drawer(
        self, db: Session, pos: PosService, admin: Actor, shift
    ) -> None:
        movement = pos.cash_movement(
            admin, kind="cash_out", amount=D("150"), reason="Banked at noon", counter="bank"
        )
        assert pos.drawer(shift).expected == D("50")
        posted = postings(db, movement.journal_entry_fk)
        assert posted["10200"] == (D("150"), D("0"))
        assert posted["10100"] == (D("0"), D("150"))
        with pytest.raises(ConflictError):
            pos.cash_movement(admin, kind="cash_out", amount=D("51"), reason="Too much")


class TestRefunds:
    def completed(self, pos: PosService, admin: Actor, world: dict):  # type: ignore[no-untyped-def]
        sale = ring_up(pos, admin, world)
        pos.tender(admin, sale.id, method="cash", cash_received=D("310"))
        return pos.complete(admin, sale.id)

    def test_a_refund_needs_authorisation(
        self, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = self.completed(pos, admin, world)
        cashier = make_actor(admin.business_id, permissions={"pos.operate", "pos.shift_manage"})
        draft = RefundDraft(
            sale_id=sale.id,
            lines=[RefundLineDraft(sale_line_id=sale.lines[0].id, quantity=D("1"))],
            reason="Box damaged",
        )
        with pytest.raises(PermissionDeniedError):
            pos.refund(cashier, draft)
        with pytest.raises(PermissionDeniedError):
            pos.refund(cashier, draft, authoriser=cashier)
        assert sale.refunded_total == D("0")

    def test_a_partial_refund_reverses_its_share_and_is_traceable(
        self, db: Session, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = self.completed(pos, admin, world)
        gloves = next(line for line in sale.lines if line.product_fk == world["gloves"].id)
        refund = pos.refund(
            admin,
            RefundDraft(
                sale_id=sale.id,
                lines=[RefundLineDraft(sale_line_id=gloves.id, quantity=D("1"))],
                reason="Wrong size",
            ),
        )
        assert refund.sale_fk == sale.id
        assert refund.lines[0].sale_line_fk == gloves.id
        assert refund.authorised_by_id == admin.user_id
        assert refund.net_total == D("100") and refund.tax_total == D("21.90")
        assert sale.state == SaleState.PARTIALLY_REFUNDED
        posted = postings(db, refund.journal_entry_fk)
        assert posted["40950"] == (D("100"), D("0"))
        assert posted["21000"][0] == D("21.90")
        assert posted["10100"] == (D("0"), D("121.90"))
        assert posted["12000"] == (D("60"), D("0"))
        assert posted["50000"] == (D("0"), D("60"))
        assert on_hand(db, world["gloves"].id) == D("19")
        assert pos.refunds_of(admin, sale.id) == [refund]

    def test_no_more_comes_back_than_was_sold(
        self, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = self.completed(pos, admin, world)
        gloves = next(line for line in sale.lines if line.product_fk == world["gloves"].id)
        draft = RefundDraft(
            sale_id=sale.id,
            lines=[RefundLineDraft(sale_line_id=gloves.id, quantity=D("2"))],
            reason="Not needed",
        )
        pos.refund(admin, draft)
        with pytest.raises(ConflictError, match="remains returnable"):
            pos.refund(admin, draft)

    def test_unfit_goods_go_to_quarantine(
        self, db: Session, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = self.completed(pos, admin, world)
        gloves = next(line for line in sale.lines if line.product_fk == world["gloves"].id)
        refund = pos.refund(
            admin,
            RefundDraft(
                sale_id=sale.id,
                lines=[RefundLineDraft(sale_line_id=gloves.id, quantity=D("1"))],
                reason="Seal broken",
                restock=False,
            ),
        )
        assert on_hand(db, world["gloves"].id, StockState.QUARANTINE) == D("1")
        assert postings(db, refund.journal_entry_fk)["12100"] == (D("60"), D("0"))

    def test_a_void_reverses_the_whole_sale_in_its_shift(
        self, db: Session, pos: PosService, admin: Actor, world: dict, shift
    ) -> None:
        sale = self.completed(pos, admin, world)
        refund = pos.void(admin, sale.id, reason="Rung up in error")
        assert sale.state == SaleState.VOIDED
        assert refund.total == sale.total
        assert on_hand(db, world["gloves"].id) == D("20")
        assert pos.drawer(shift).expected == D("200")
