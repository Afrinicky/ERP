"""Reconciliation against a real database: statements matched to the ledger, and every
source of money against its postings. PRD §20.4, AC-20.

Exit criterion 6 held here: POS shift, bank, Mobile Money, customer receipt, supplier
payment and tax payment reconciliation all complete against source records.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.parties import PartyDraft, PartyService
from nickland.application.services.receivables import ReceiptDraft, ReceivablesService
from nickland.application.services.reconciliation import ReconciliationService
from nickland.application.services.taxation import TaxService
from nickland.application.services.treasury import MovementDraft, TreasuryService
from nickland.domain.errors import ConflictError, PermissionDeniedError, ValidationError
from nickland.domain.finance import FinancialAccountKind, parse_statement
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

D = Decimal
DAY = date(2026, 11, 5)


@pytest.fixture
def recon(db: Session, clock: FixedClock) -> ReconciliationService:
    return ReconciliationService(db, clock=clock)


@pytest.fixture
def bank(db: Session, clock: FixedClock, admin: Actor) -> dict:
    """The main bank with interest of 1,000 received and 250 moved out to petty cash."""
    treasury = TreasuryService(db, clock=clock)
    assert admin.business_id is not None
    bank = treasury.default_for(admin.business_id, FinancialAccountKind.BANK)
    petty = treasury.default_for(admin.business_id, FinancialAccountKind.PETTY_CASH)
    treasury.record(
        admin,
        MovementDraft(
            kind="interest",
            amount=D("1000"),
            to_account_id=bank.id,
            reason="Interest",
            reference="INT-11",
        ),
    )
    treasury.record(
        admin,
        MovementDraft(
            kind="transfer",
            amount=D("250"),
            from_account_id=bank.id,
            to_account_id=petty.id,
            reason="Petty cash float",
            reference="CHQ-004512",
        ),
    )
    return {"bank": bank, "petty": petty, "treasury": treasury}


STATEMENT = """Date,Description,Reference,Debit,Credit
05/11/2026,Interest credited,INT-11,,1000.00
05/11/2026,Cheque 004512,CHQ-004512,250.00,
06/11/2026,Monthly account fee,FEE-NOV,"12.50",
"""


class TestParsing:
    def test_debit_and_credit_columns_and_day_first_dates(self) -> None:
        lines = parse_statement(STATEMENT)
        assert [item.amount for item in lines] == [D("1000"), D("-250"), D("-12.5")]
        assert lines[0].line_date == date(2026, 11, 5)
        assert lines[1].reference == "CHQ-004512"

    def test_a_signed_amount_column_and_bracketed_negatives(self) -> None:
        lines = parse_statement(
            "Transaction Date,Narration,Amount\n2026-11-05,MoMo cash out,\"(1,250.00)\"\n"
        )
        assert lines[0].amount == D("-1250")

    def test_an_unreadable_statement_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="names the columns"):
            parse_statement("When,What\n1,2\n")
        with pytest.raises(ValidationError, match="Row 2"):
            parse_statement("Date,Description,Amount\nyesterday,x,1\n")


class TestStatements:
    def test_a_statement_reconciles_once_every_line_is_matched_and_it_closes_to_the_ledger(
        self, recon: ReconciliationService, admin: Actor, bank: dict
    ) -> None:
        statement = recon.import_statement(
            admin,
            bank["bank"].id,
            content=STATEMENT,
            closing_balance=D("737.50"),
            reference="GCB 2026/11",
        )
        assert recon.auto_match(admin, statement.id) == 2
        position = recon.position(admin, statement.id)
        assert position.unmatched == 1 and not position.reconciled
        with pytest.raises(ConflictError, match="unmatched"):
            recon.complete(admin, statement.id)

        fee = next(line for line in statement.lines if line.reference == "FEE-NOV")
        assert recon.suggestions(admin, fee.id) == []
        with pytest.raises(ValidationError):
            recon.post_from_line(admin, fee.id, kind="interest", reason="x")
        recon.post_from_line(admin, fee.id, kind="bank_charge", reason="Account fee")
        assert fee.state == "matched"
        done = recon.complete(admin, statement.id)
        assert done.reconciled and done.ledger_balance == D("737.50")
        assert statement.state == "reconciled"

    def test_a_line_imported_twice_is_a_duplicate_and_is_never_matched(
        self, recon: ReconciliationService, admin: Actor, bank: dict
    ) -> None:
        recon.import_statement(
            admin, bank["bank"].id, content=STATEMENT, closing_balance=D("737.50"), reference="A"
        )
        again = recon.import_statement(
            admin, bank["bank"].id, content=STATEMENT, closing_balance=D("737.50"), reference="B"
        )
        assert all(line.state == "duplicate" for line in again.lines)
        assert recon.auto_match(admin, again.id) == 0

    def test_postings_matched_must_equal_the_line(
        self, recon: ReconciliationService, admin: Actor, bank: dict
    ) -> None:
        statement = recon.import_statement(
            admin, bank["bank"].id, content=STATEMENT, closing_balance=D("0"), reference="C"
        )
        cheque = next(line for line in statement.lines if line.reference == "CHQ-004512")
        interest = recon.unreconciled(admin, bank["bank"].id)[0]
        with pytest.raises(ConflictError, match="total"):
            recon.match(admin, cheque.id, [interest.line.id])
        suggestion = recon.suggestions(admin, cheque.id)[0]
        assert suggestion.score >= 100  # the cheque number is in the posting's reference
        recon.match(admin, cheque.id, [suggestion.item.line.id])
        with pytest.raises(ConflictError):
            other = next(line for line in statement.lines if line.reference == "FEE-NOV")
            recon.match(admin, other.id, [suggestion.item.line.id])
        recon.unmatch(admin, cheque.id)
        assert cheque.state == "unmatched"

    def test_matching_needs_its_permission(
        self, recon: ReconciliationService, admin: Actor, bank: dict
    ) -> None:
        clerk = make_actor(admin.business_id, permissions={"journal.view"})
        with pytest.raises(PermissionDeniedError):
            recon.import_statement(
                clerk, bank["bank"].id, content=STATEMENT, closing_balance=D("0"), reference="D"
            )


class TestSources:
    def test_every_source_is_checked_against_its_postings(
        self,
        db: Session,
        clock: FixedClock,
        recon: ReconciliationService,
        admin: Actor,
        bank: dict,
    ) -> None:
        taxation = TaxService(db, clock=clock)
        taxation.record_payment(admin, reference="GRA-VAT-2026-10", paid_on=DAY, amount=D("300"))
        statement = recon.import_statement(
            admin,
            bank["bank"].id,
            content=STATEMENT + "05/11/2026,GRA VAT October,GRA-VAT-2026-10,300.00,\n",
            closing_balance=D("437.50"),
            reference="E",
        )
        recon.auto_match(admin, statement.id)
        fee = next(line for line in statement.lines if line.reference == "FEE-NOV")
        recon.post_from_line(admin, fee.id, kind="bank_charge", reason="Account fee")
        recon.complete(admin, statement.id)

        customer = PartyService(db, clock=clock).create(
            admin, PartyDraft(name="Ridge Pharmacy", is_customer=True)
        )
        ReceivablesService(db, clock=clock).receive(
            admin,
            ReceiptDraft(
                customer_id=customer.id,
                amount=D("80"),
                financial_account_id=bank["treasury"]
                .default_for(admin.business_id, FinancialAccountKind.CASH_DRAWER)
                .id,
            ),
        )

        rows = {
            row.source: row
            for row in recon.sources(admin, start=date(2026, 1, 1), end=date(2026, 12, 31))
        }
        assert rows["pos_shifts"].agrees
        assert rows["customer_receipts"].agrees and rows["customer_receipts"].recorded == D("80")
        assert rows["supplier_payments"].agrees
        assert rows["tax_payments"].agrees and rows["tax_payments"].posted == D("300")
        assert rows[f"account:{bank['bank'].id}"].agrees
        wallet = next(row for key, row in rows.items() if row.label == "Mobile Money wallet")
        assert not wallet.agrees and wallet.notes == ["No statement imported"]
