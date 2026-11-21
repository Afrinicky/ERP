"""The debt registers against a real database. PRD §25.2, §25.3, §25.6, AC-23, AC-28 to AC-30.

Each debt is read from the one record that creates it, so nothing is counted twice; what is
owed to the business and what it owes are shown apart and never netted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.debts import DebtService, OtherDebtDraft
from nickland.application.services.expenses import ExpenseDraft, ExpenseService
from nickland.application.services.loans import LoanDraft, LoanService
from nickland.application.services.receivables import ReceiptDraft, ReceivablesService
from nickland.application.services.treasury import MovementDraft, TreasuryService
from nickland.domain.errors import ConflictError
from nickland.domain.finance import FinancialAccountKind
from nickland.domain.financing import DebtKind
from nickland.platform.clock import FixedClock
from tests.integration.test_receivables import bill_ordered, world  # noqa: F401

D = Decimal


@pytest.fixture
def debts(db: Session, clock: FixedClock) -> DebtService:
    return DebtService(db, clock=clock)


@pytest.fixture
def bank(db: Session, clock: FixedClock, admin: Actor):  # type: ignore[no-untyped-def]
    business = admin.business_id
    assert business is not None
    treasury = TreasuryService(db, clock=clock)
    account = treasury.default_for(business, FinancialAccountKind.BANK)
    treasury.record(
        admin,
        MovementDraft(
            kind="adjustment_in", amount=D("50000"), to_account_id=account.id, reason="Float"
        ),
    )
    return account


def agreement(db: Session, clock: FixedClock, admin: Actor, bank, **changes: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "direction": "borrowing",
        "loan_type": "term_loan",
        "counterparty_name": "GCB Bank",
        "principal": D("12000"),
        "agreement_date": date(2026, 6, 1),
        "model": "reducing_balance",
        "annual_rate": D("24"),
        "frequency": "monthly",
        "instalments": 24,
        "first_due_on": date(2026, 7, 1),
        "financial_account_id": bank.id,
    }
    values.update(changes)
    loans = LoanService(db, clock=clock)
    row = loans.create(admin, LoanDraft(**values))  # type: ignore[arg-type]
    loans.submit(admin, row.id)
    loans.drawdown(admin, row.id, amount=values["principal"], on=date(2026, 6, 1))  # type: ignore[arg-type]
    return row


class TestRegisters:
    def test_a_customer_debt_shows_its_original_collections_and_balance(
        self,
        db: Session,
        clock: FixedClock,
        admin: Actor,
        debts: DebtService,
        world: dict,  # noqa: F811
    ) -> None:
        receivables = ReceivablesService(db, clock=clock)
        invoice = bill_ordered(receivables, admin, world)
        receivables.receive(admin, ReceiptDraft(customer_id=world["customer"].id, amount=D("1000")))
        [line] = [
            line for line in debts.register(admin, owed_to_us=True) if line.source_id == invoice.id
        ]
        assert line.kind is DebtKind.TRADE_RECEIVABLE
        assert line.original == invoice.total
        assert line.settled == D("1000")
        assert line.outstanding == invoice.total - D("1000")
        assert line.reference == invoice.number

    def test_receivables_and_payables_are_never_netted(
        self,
        db: Session,
        clock: FixedClock,
        admin: Actor,
        debts: DebtService,
        bank,
        world: dict,  # noqa: F811
    ) -> None:
        receivables = ReceivablesService(db, clock=clock)
        invoice = bill_ordered(receivables, admin, world)
        agreement(db, clock, admin, bank)
        summary = debts.summary(admin)
        assert summary.receivables == invoice.total
        assert summary.loans_payable.principal == D("12000")
        assert summary.payables >= D("12000")
        # Two totals, side by side; neither reduced by the other.
        assert summary.receivables + summary.payables > summary.payables

    def test_a_loan_issued_is_a_non_trade_receivable(
        self, db: Session, clock: FixedClock, admin: Actor, debts: DebtService, bank
    ) -> None:
        agreement(
            db,
            clock,
            admin,
            bank,
            direction="lending",
            loan_type="staff_advance",
            counterparty_name="Ama Owusu",
            principal=D("1500"),
            model="none",
            annual_rate=D("0"),
            instalments=3,
        )
        summary = debts.summary(admin)
        kinds = {split.code: split.amount for split in summary.receivables_by_kind}
        assert kinds == {"loan_issued": D("1500")}
        assert summary.loans_receivable.principal == D("1500")

    def test_each_source_is_counted_once(
        self,
        db: Session,
        clock: FixedClock,
        admin: Actor,
        debts: DebtService,
        bank,
        world: dict,  # noqa: F811
    ) -> None:
        receivables = ReceivablesService(db, clock=clock)
        bill_ordered(receivables, admin, world)
        agreement(db, clock, admin, bank)
        ExpenseService(db, clock=clock).record(
            admin,
            ExpenseDraft(
                payee_name="Osu Properties Ltd",
                category_code="60000",
                description="Shop rent",
                net=D("1500"),
                settlement="on_credit",
            ),
        )
        for owed_to_us in (True, False):
            lines = debts.register(admin, owed_to_us=owed_to_us)
            keys = [(line.source_type, line.source_id) for line in lines]
            assert len(keys) == len(set(keys))
        payable_kinds = {line.kind for line in debts.register(admin, owed_to_us=False)}
        assert {DebtKind.BORROWING, DebtKind.EXPENSE} <= payable_kinds


class TestOtherDebts:
    def test_a_refundable_deposit_paid_is_an_asset_and_its_return_settles_it(
        self, db: Session, clock: FixedClock, admin: Actor, debts: DebtService, bank
    ) -> None:
        debt = debts.record_other(
            admin,
            OtherDebtDraft(
                owed_to_us=True,
                counterparty_name="Osu Properties Ltd",
                description="Refundable deposit on the Osu shop",
                amount=D("3000"),
                financial_account_id=bank.id,
                reference="LEASE-OSU-2026",
            ),
        )
        assert debt.number.startswith("DBT-")
        debts.settle_other(admin, debt.id, amount=D("1000"), financial_account_id=bank.id)
        [line] = [
            line for line in debts.register(admin, owed_to_us=True) if line.source_id == debt.id
        ]
        assert line.outstanding == D("2000")

    def test_a_debt_a_document_already_records_is_refused(
        self,
        db: Session,
        clock: FixedClock,
        admin: Actor,
        debts: DebtService,
        bank,
        world: dict,  # noqa: F811
    ) -> None:
        invoice = bill_ordered(ReceivablesService(db, clock=clock), admin, world)
        with pytest.raises(ConflictError, match="already in the register"):
            debts.record_other(
                admin,
                OtherDebtDraft(
                    owed_to_us=True,
                    counterparty_name="Korle Bu Teaching Hospital",
                    description="Balance on the gloves order",
                    amount=invoice.total,
                    financial_account_id=bank.id,
                    reference=invoice.number,
                ),
            )

    def test_the_same_debt_twice_is_refused(self, admin: Actor, debts: DebtService, bank) -> None:
        draft = OtherDebtDraft(
            owed_to_us=False,
            counterparty_name="Tema Clinic",
            description="Bottle deposit held",
            amount=D("200"),
            financial_account_id=bank.id,
        )
        debts.record_other(admin, draft)
        with pytest.raises(ConflictError):
            debts.record_other(admin, draft)


class TestAnalysis:
    def test_the_calendar_shows_instalments_in_and_out(
        self, db: Session, clock: FixedClock, admin: Actor, debts: DebtService, bank
    ) -> None:
        agreement(db, clock, admin, bank)
        agreement(
            db,
            clock,
            admin,
            bank,
            direction="lending",
            loan_type="related_party",
            counterparty_name="Ridge Community Clinic",
            principal=D("2400"),
            model="flat",
            annual_rate=D("12"),
            instalments=12,
        )
        lines = debts.calendar(admin, days=60)
        assert any(line.inward for line in lines)
        assert any(not line.inward for line in lines)

    def test_the_maturity_profile_sums_to_the_principal_left(
        self, db: Session, clock: FixedClock, admin: Actor, debts: DebtService, bank
    ) -> None:
        agreement(db, clock, admin, bank)
        rows = debts.maturity(admin)
        assert sum((row.borrowed for row in rows), D(0)) == D("12000")
        assert {row.code for row in rows if row.borrowed} == {"within_1", "1_2"}

    def test_cash_flow_keeps_principal_interest_and_transfers_apart(
        self, db: Session, clock: FixedClock, admin: Actor, debts: DebtService, bank
    ) -> None:
        loan = agreement(db, clock, admin, bank)
        loans = LoanService(db, clock=clock)
        first = loans.current(loan).instalments[0]
        loans.repay(admin, loan.id, amount=first.owed, on=first.due_on)
        treasury = TreasuryService(db, clock=clock)
        business = admin.business_id
        assert business is not None
        cash = treasury.default_for(business, FinancialAccountKind.CASH_DRAWER)
        treasury.record(
            admin,
            MovementDraft(
                kind="transfer",
                amount=D("500"),
                from_account_id=bank.id,
                to_account_id=cash.id,
                reason="Float for the counter",
            ),
        )
        classes = {
            row.code: row
            for row in debts.cash_flow(admin, start=date(2026, 1, 1), end=date(2026, 12, 31))
        }
        assert classes["loan_principal_borrowed"].inflow == D("12000")
        assert classes["loan_principal_borrowed"].outflow == first.principal
        assert classes["interest_paid"].outflow == first.interest
        assert classes["transfers"].inflow == D("500")
