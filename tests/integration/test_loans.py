"""Loans and financing against a real database. PRD §25.4 to §25.8, AC-24 to AC-27.

The rule held throughout: a loan is an agreement, not income or expense. Drawing it down
and repaying its principal move cash and the balance together; only interest, fees and
penalties reach profit, and each is recognised separately.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.application.services.loans import LoanDraft, LoanService, RevisionDraft
from nickland.application.services.treasury import TreasuryService
from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.finance import FinancialAccountKind
from nickland.domain.financing import AgreementState, ScheduleState
from nickland.persistence.financing_models import LoanInstalment
from nickland.persistence.ledger_models import Account
from nickland.persistence.models import AuditEvent
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

D = Decimal
LATER = date(2028, 12, 31)


@pytest.fixture
def loans(db: Session, clock: FixedClock) -> LoanService:
    return LoanService(db, clock=clock)


@pytest.fixture
def bank(db: Session, clock: FixedClock, admin: Actor):  # type: ignore[no-untyped-def]
    business = admin.business_id
    assert business is not None
    return TreasuryService(db, clock=clock).default_for(business, FinancialAccountKind.BANK)


def balance(db: Session, clock: FixedClock, admin: Actor, code: str) -> Decimal:
    account = db.scalar(
        select(Account).where(Account.business_id == admin.business_id, Account.code == code)
    )
    assert account is not None
    return LedgerReportService(db, clock=clock).account_balance(
        admin.business_id,  # type: ignore[arg-type]
        account.id,
        as_at=LATER,
    )


def result(db: Session, clock: FixedClock, admin: Actor):  # type: ignore[no-untyped-def]
    return LedgerReportService(db, clock=clock).profit_and_loss(
        admin, start=date(2026, 1, 1), end=LATER
    )


def borrowing(bank_id, **changes: object) -> LoanDraft:  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "direction": "borrowing",
        "loan_type": "term_loan",
        "counterparty_name": "GCB Bank",
        "principal": D("12000"),
        "agreement_date": date(2026, 1, 10),
        "model": "reducing_balance",
        "annual_rate": D("24"),
        "frequency": "monthly",
        "instalments": 12,
        "first_due_on": date(2026, 2, 10),
        "financial_account_id": bank_id,
        "agreement_reference": "GCB/TL/2026/118",
        "purpose": "Working capital for the Q1 tender",
    }
    values.update(changes)
    return LoanDraft(**values)  # type: ignore[arg-type]


def active(loans: LoanService, admin: Actor, draft: LoanDraft):  # type: ignore[no-untyped-def]
    agreement = loans.create(admin, draft)
    loans.submit(admin, agreement.id)
    assert agreement.state == AgreementState.ACTIVE.value
    return agreement


class TestBorrowing:
    def test_a_drawdown_raises_cash_and_liability_and_neither_revenue_nor_profit(
        self, db: Session, clock: FixedClock, admin: Actor, loans: LoanService, bank
    ) -> None:
        before = result(db, clock, admin)
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))

        assert balance(db, clock, admin, "10200") == D("12000")
        assert balance(db, clock, admin, "23000") == D("12000")
        after = result(db, clock, admin)
        assert after.total_revenue == before.total_revenue
        assert after.net_result == before.net_result

    def test_principal_repaid_is_not_an_expense_and_interest_is_recognised_apart(
        self, db: Session, clock: FixedClock, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        first = loans.current(agreement).instalments[0]
        loans.repay(admin, agreement.id, amount=first.owed, on=first.due_on)

        assert balance(db, clock, admin, "23000") == D("12000") - first.principal
        assert balance(db, clock, admin, "10200") == D("12000") - first.owed
        # The only expense is the interest, on its own account.
        assert balance(db, clock, admin, "71000") == first.interest
        assert result(db, clock, admin).total_expenses == first.interest
        assert balance(db, clock, admin, "23100") == D("0")

    def test_the_schedule_repays_exactly_the_principal(
        self, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        rows = loans.current(agreement).instalments
        assert sum((row.principal for row in rows), D(0)) == D("12000")
        assert rows[-1].closing == D("0")
        assert agreement.maturity_date == date(2027, 1, 10)

    def test_the_ledger_and_the_agreement_agree(
        self, db: Session, clock: FixedClock, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        loans.accrue(admin)
        loans.repay(admin, agreement.id, amount=D("3000"), on=date(2026, 4, 10))
        position = loans.position(agreement)
        assert position.ledger_principal == position.principal_outstanding
        assert position.ledger_accrued == (
            position.interest_due + position.fees_due + position.penalties_due
        )

    def test_drawing_more_than_agreed_is_refused(
        self, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("8000"), on=date(2026, 1, 10))
        loans.drawdown(admin, agreement.id, amount=D("4000"), on=date(2026, 1, 20))
        with pytest.raises(ConflictError):
            loans.drawdown(admin, agreement.id, amount=D("1"), on=date(2026, 1, 21))

    def test_an_arrangement_fee_is_a_finance_charge_not_principal(
        self, db: Session, clock: FixedClock, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("12000"), fee=D("240"), on=date(2026, 1, 10))
        assert balance(db, clock, admin, "10200") == D("11760")
        assert balance(db, clock, admin, "23000") == D("12000")
        assert balance(db, clock, admin, "71300") == D("240")


class TestLending:
    def test_money_lent_is_an_asset_and_never_expensed(
        self, db: Session, clock: FixedClock, admin: Actor, loans: LoanService, bank
    ) -> None:
        loans_bank_float(db, clock, admin, bank)
        before = result(db, clock, admin).total_expenses
        agreement = active(
            loans,
            admin,
            borrowing(
                bank.id,
                direction="lending",
                loan_type="staff_advance",
                counterparty_name="Kofi Mensah",
                principal=D("1200"),
                model="none",
                annual_rate=D("0"),
                instalments=6,
            ),
        )
        loans.drawdown(admin, agreement.id, amount=D("1200"), on=date(2026, 1, 10))
        assert balance(db, clock, admin, "11200") == D("1200")
        assert result(db, clock, admin).total_expenses == before

    def test_interest_on_a_loan_issued_is_income_when_due(
        self, db: Session, clock: FixedClock, admin: Actor, loans: LoanService, bank
    ) -> None:
        loans_bank_float(db, clock, admin, bank)
        agreement = active(
            loans,
            admin,
            borrowing(
                bank.id,
                direction="lending",
                loan_type="related_party",
                counterparty_name="Ridge Community Clinic",
                principal=D("6000"),
                model="flat",
                annual_rate=D("12"),
                instalments=6,
            ),
        )
        loans.drawdown(admin, agreement.id, amount=D("6000"), on=date(2026, 1, 10))
        loans.accrue(admin, as_of=date(2026, 2, 10))
        first = loans.current(agreement).instalments[0]
        # Interest income sits in "other income and expense", read on the expense side.
        assert balance(db, clock, admin, "70000") == -first.interest
        assert balance(db, clock, admin, "11210") == first.interest
        loans.repay(admin, agreement.id, amount=first.owed, on=date(2026, 2, 10))
        assert balance(db, clock, admin, "11210") == D("0")
        assert balance(db, clock, admin, "11200") == D("6000") - first.principal

    def test_a_write_off_clears_the_asset_into_bad_debt(
        self, db: Session, clock: FixedClock, admin: Actor, loans: LoanService, bank
    ) -> None:
        loans_bank_float(db, clock, admin, bank)
        agreement = active(
            loans,
            admin,
            borrowing(
                bank.id,
                direction="lending",
                loan_type="other",
                counterparty_name="Departed supplier",
                principal=D("2000"),
                model="none",
                annual_rate=D("0"),
                instalments=2,
            ),
        )
        loans.drawdown(admin, agreement.id, amount=D("2000"), on=date(2026, 1, 10))
        loans.write_off(admin, agreement.id, reason="Supplier ceased trading")
        assert balance(db, clock, admin, "11200") == D("0")
        assert balance(db, clock, admin, "72000") == D("2000")
        assert agreement.state == AgreementState.WRITTEN_OFF.value

    def test_a_borrowing_cannot_be_written_off(
        self, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        with pytest.raises(ConflictError):
            loans.write_off(admin, agreement.id, reason="Would rather not pay")


def loans_bank_float(db: Session, clock: FixedClock, admin: Actor, bank) -> None:  # type: ignore[no-untyped-def]
    from nickland.application.services.treasury import MovementDraft

    TreasuryService(db, clock=clock).record(
        admin,
        MovementDraft(
            kind="adjustment_in", amount=D("20000"), to_account_id=bank.id, reason="Float"
        ),
    )


class TestAllocation:
    def test_a_partial_repayment_follows_the_agreed_order(
        self, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(
            loans,
            admin,
            borrowing(
                bank.id,
                periodic_fee=D("50"),
                allocation_order=["principal", "interest", "fees", "penalty"],
            ),
        )
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        first = loans.current(agreement).instalments[0]
        transaction = loans.repay(admin, agreement.id, amount=D("800"), on=first.due_on)
        split = {row.component: row.amount for row in transaction.allocations}
        assert split == {"principal": D("800")}
        assert first.paid_principal == D("800")
        assert first.paid_interest == D("0")

    def test_the_default_order_settles_charges_before_principal(
        self, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id, periodic_fee=D("50")))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        first = loans.current(agreement).instalments[0]
        transaction = loans.repay(admin, agreement.id, amount=D("300"), on=first.due_on)
        split = {row.component: row.amount for row in transaction.allocations}
        assert split == {"fees": D("50"), "interest": D("240"), "principal": D("10")}

    def test_a_partial_repayment_leaves_the_agreement_untouched(
        self, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        terms_before = dict(loans.current(agreement).terms)
        loans.repay(admin, agreement.id, amount=D("500"), on=date(2026, 2, 10))
        assert agreement.principal == D("12000")
        assert agreement.annual_rate == D("24")
        assert len(agreement.schedules) == 1
        assert dict(loans.current(agreement).terms) == terms_before

    def test_a_prepayment_is_a_new_version_beside_the_original(
        self, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        first = loans.current(agreement).instalments[0]
        loans.repay(admin, agreement.id, amount=first.owed + D("3000"), on=first.due_on)
        versions = loans.versions(admin, agreement.id)
        assert [version.state for version in versions] == [
            ScheduleState.SUPERSEDED.value,
            ScheduleState.CURRENT.value,
        ]
        assert versions[1].kind == "prepayment"
        assert len(versions[0].instalments) == 12
        position = loans.position(agreement, first.due_on)
        assert position.principal_outstanding == D("12000") - first.principal - D("3000")
        assert sum((row.principal for row in versions[1].instalments), D(0)) == (
            position.principal_outstanding
        )

    def test_overpaying_beyond_the_balance_is_refused(
        self, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        with pytest.raises(ConflictError):
            loans.repay(admin, agreement.id, amount=D("20000"), on=date(2026, 2, 10))


class TestRevision:
    def test_a_reschedule_is_versioned_approved_and_audited(
        self, db: Session, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        original = [
            (row.due_on, row.interest, row.principal)
            for row in loans.current(agreement).instalments
        ]
        loans.accrue(admin, as_of=date(2026, 3, 10))
        proposal = loans.propose_revision(
            admin,
            agreement.id,
            RevisionDraft(
                kind="payment_holiday",
                reason="Tender payment delayed by the Ministry",
                effective_on=date(2026, 3, 11),
                first_due_on=date(2026, 4, 10),
                instalments=12,
                grace_periods=2,
            ),
        )
        assert proposal.state == ScheduleState.PROPOSED.value
        # Nothing changes until it is approved.
        assert len(loans.open_instalments(agreement)) == 12

        loans.decide_revision(admin, agreement.id, approve=True, note="Approved by the proprietor")
        versions = loans.versions(admin, agreement.id)
        assert [version.state for version in versions] == ["superseded", "current"]
        # The original remains, row for row.
        assert [
            (row.due_on, row.interest, row.principal) for row in versions[0].instalments
        ] == original
        # The arrears of the original are carried, not lost.
        arrears = [row for row in versions[1].instalments if row.arrears]
        assert len(arrears) == 1
        events = db.scalars(select(AuditEvent).where(AuditEvent.entity_id == agreement.id)).all()
        actions = {event.action for event in events}
        assert {"loan_revision_proposed", "loan_revision_approved"} <= actions
        proposed = next(event for event in events if event.action == "loan_revision_proposed")
        assert proposed.reason == "Tender payment delayed by the Ministry"

    def test_a_revision_without_a_reason_is_refused(
        self, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        with pytest.raises(ValidationError):
            loans.propose_revision(
                admin,
                agreement.id,
                RevisionDraft(
                    kind="reschedule",
                    reason=" ",
                    effective_on=date(2026, 3, 11),
                    first_due_on=date(2026, 4, 10),
                    instalments=18,
                ),
            )

    def test_a_declined_revision_leaves_the_schedule_as_it_was(
        self, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        loans.propose_revision(
            admin,
            agreement.id,
            RevisionDraft(
                kind="interest_change",
                reason="Bank offered a lower rate",
                effective_on=date(2026, 1, 15),
                first_due_on=date(2026, 2, 10),
                instalments=12,
                annual_rate=D("20"),
            ),
        )
        loans.decide_revision(admin, agreement.id, approve=False, note="Fee too high")
        assert [version.state for version in loans.versions(admin, agreement.id)] == [
            "current",
            "declined",
        ]


class TestApproval:
    def test_a_material_agreement_waits_for_a_second_person(
        self, db: Session, clock: FixedClock, admin: Actor, loans: LoanService, bank
    ) -> None:
        loans.set_threshold(admin, D("10000"))
        clerk = make_actor(
            admin.business_id,  # type: ignore[arg-type]
            permissions={"loan.manage", "loan.view", "loan.approve"},
        )
        agreement = loans.create(clerk, borrowing(bank.id))
        loans.submit(clerk, agreement.id)
        assert agreement.state == AgreementState.AWAITING_APPROVAL.value
        with pytest.raises(ConflictError):
            loans.approve(clerk, agreement.id, note="Self-approved")
        with pytest.raises(ValidationError):
            loans.approve(admin, agreement.id)
        loans.approve(admin, agreement.id, note="Board resolution 2026/04")
        assert agreement.state == AgreementState.ACTIVE.value

    def test_nothing_is_recorded_against_an_unapproved_agreement(
        self, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = loans.create(admin, borrowing(bank.id))
        with pytest.raises(ConflictError):
            loans.drawdown(admin, agreement.id, amount=D("100"))


class TestCharges:
    def test_no_penalty_unless_the_agreement_provides_for_one(
        self, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        with pytest.raises(ConflictError):
            loans.charge_penalty(admin, agreement.id, reason="Late")

    def test_a_penalty_the_agreement_allows_is_a_finance_charge(
        self, db: Session, clock: FixedClock, admin: Actor, loans: LoanService, bank
    ) -> None:
        agreement = active(loans, admin, borrowing(bank.id, penalty_rate=D("10")))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        charge = loans.charge_penalty(admin, agreement.id, reason="Instalments in arrears")
        assert charge.amount > D("0")
        assert balance(db, clock, admin, "71300") == charge.amount
        assert loans.position(agreement).penalties_due == charge.amount


class TestSettlement:
    def test_early_settlement_closes_the_agreement_for_its_quotation(
        self, db: Session, clock: FixedClock, admin: Actor, loans: LoanService, bank
    ) -> None:
        loans_bank_float(db, clock, admin, bank)
        agreement = active(loans, admin, borrowing(bank.id, early_settlement_percent=D("2")))
        loans.drawdown(admin, agreement.id, amount=D("12000"), on=date(2026, 1, 10))
        loans.accrue(admin)
        quote = loans.quote(admin, agreement.id)
        assert quote.early_settlement_fee == (quote.principal * D("0.02")).quantize(D("0.01"))
        loans.settle(admin, agreement.id)
        assert agreement.state == AgreementState.SETTLED.value
        assert balance(db, clock, admin, "23000") == D("0")
        assert balance(db, clock, admin, "23100") == D("0")
        assert all(
            row.closed
            for row in db.scalars(
                select(LoanInstalment).where(LoanInstalment.agreement_fk == agreement.id)
            )
        )

    def test_refinancing_moves_the_balance_without_moving_cash(
        self, db: Session, clock: FixedClock, admin: Actor, loans: LoanService, bank
    ) -> None:
        old = active(loans, admin, borrowing(bank.id))
        loans.drawdown(admin, old.id, amount=D("12000"), on=date(2026, 1, 10))
        cash_before = balance(db, clock, admin, "10200")
        loans.accrue(admin, as_of=date(2026, 6, 10))
        owed = loans.position(old, date(2026, 6, 10))
        replacement = active(
            loans,
            admin,
            borrowing(
                bank.id,
                principal=D("15000"),
                agreement_date=date(2026, 6, 10),
                first_due_on=date(2026, 7, 10),
                annual_rate=D("20"),
                instalments=24,
            ),
        )
        loans.refinance(admin, old.id, replacement_id=replacement.id, on=date(2026, 6, 10))
        assert old.state == AgreementState.REFINANCED.value
        assert balance(db, clock, admin, "10200") == cash_before
        moved = owed.principal_outstanding + owed.interest_due + owed.fees_due
        assert loans.position(replacement).principal_drawn == moved
        assert loans.position(replacement).ledger_principal == moved
