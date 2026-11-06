"""The posting engine, period close and reporting against a real database. PRD 29.2."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.accounts import AccountService
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.application.services.periods import PeriodService
from nickland.domain.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from nickland.domain.ledger import (
    AccountingBasis,
    EntryLine,
    JournalSource,
    Side,
    SubledgerKind,
)
from nickland.persistence.ledger_models import JournalEntry
from nickland.persistence.models import AuditEvent
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

TODAY = date(2026, 11, 6)


@pytest.fixture
def accounts(db: Session, clock: FixedClock) -> AccountService:
    return AccountService(db, clock=clock)


@pytest.fixture
def journals(db: Session, clock: FixedClock) -> JournalService:
    return JournalService(db, clock=clock)


@pytest.fixture
def reports(db: Session, clock: FixedClock) -> LedgerReportService:
    return LedgerReportService(db, clock=clock)


@pytest.fixture
def periods(db: Session, clock: FixedClock) -> PeriodService:
    return PeriodService(db, clock=clock)


def capital(journals: JournalService, admin: Actor, amount: str = "50000.00") -> JournalEntry:
    return journals.post(
        admin,
        JournalDraft(
            posting_date=date(2026, 11, 2),
            narration="Capital introduced by the proprietor",
            lines=[
                EntryLine("10200", Side.DEBIT, Decimal(amount)),
                EntryLine("30000", Side.CREDIT, Decimal(amount)),
            ],
        ),
    ).entry


class TestProvisioning:
    def test_the_chart_is_seeded_at_provisioning(
        self, accounts: AccountService, admin: Actor
    ) -> None:
        chart = accounts.accounts(admin)
        assert len(chart) > 40
        codes = {account.code for account in chart}
        assert {"10100", "11000", "20000", "30100", "40100", "50000"} <= codes

    def test_the_financial_year_is_opened_at_provisioning(
        self, accounts: AccountService, admin: Actor
    ) -> None:
        opened = accounts.periods(admin)
        assert len(opened) == 12
        assert {period.sequence for period in opened} == set(range(1, 13))
        assert opened[-1].is_year_end

    def test_control_accounts_are_marked_and_closed_to_manual_posting(
        self, accounts: AccountService, admin: Actor
    ) -> None:
        receivables = accounts.require_by_code(admin.business_id, "11000")  # type: ignore[arg-type]
        assert receivables.control_subledger == SubledgerKind.CUSTOMER.value
        assert not receivables.permits_manual_posting

    def test_seeding_twice_creates_nothing_further(
        self, accounts: AccountService, admin: Actor
    ) -> None:
        before = len(accounts.accounts(admin))
        groups, created = accounts.seed(admin.business_id)  # type: ignore[arg-type]
        assert (groups, created) == (0, 0)
        assert len(accounts.accounts(admin)) == before


class TestPosting:
    def test_a_balanced_entry_posts_and_is_numbered(
        self, journals: JournalService, admin: Actor
    ) -> None:
        entry = capital(journals, admin)
        assert entry.state == "posted"
        assert entry.number == "JV-2026-00001"
        assert entry.total_debit == entry.total_credit == Decimal("50000")

    def test_numbering_runs_on_within_the_financial_year(
        self, journals: JournalService, admin: Actor
    ) -> None:
        first = capital(journals, admin)
        second = capital(journals, admin, "10.00")
        assert (first.number, second.number) == ("JV-2026-00001", "JV-2026-00002")

    def test_an_unbalanced_entry_is_refused(self, journals: JournalService, admin: Actor) -> None:
        with pytest.raises(ValidationError, match="exceed"):
            journals.post(
                admin,
                JournalDraft(
                    posting_date=TODAY,
                    narration="Out of balance",
                    lines=[
                        EntryLine("10100", Side.DEBIT, Decimal("100.00")),
                        EntryLine("40100", Side.CREDIT, Decimal("90.00")),
                    ],
                ),
            )

    def test_an_unknown_account_is_refused_by_name(
        self, journals: JournalService, admin: Actor
    ) -> None:
        with pytest.raises(ValidationError, match="99999"):
            journals.post(
                admin,
                JournalDraft(
                    posting_date=TODAY,
                    narration="Against nothing",
                    lines=[
                        EntryLine("99999", Side.DEBIT, Decimal("10.00")),
                        EntryLine("10100", Side.CREDIT, Decimal("10.00")),
                    ],
                ),
            )

    def test_a_control_account_refuses_a_manual_journal(
        self, journals: JournalService, admin: Actor
    ) -> None:
        with pytest.raises(ConflictError, match="control account"):
            journals.post(
                admin,
                JournalDraft(
                    posting_date=TODAY,
                    narration="Hand-written receivable",
                    lines=[
                        EntryLine("11000", Side.DEBIT, Decimal("10.00")),
                        EntryLine("40100", Side.CREDIT, Decimal("10.00")),
                    ],
                ),
            )

    def test_a_control_line_must_name_its_party(
        self, journals: JournalService, admin: Actor
    ) -> None:
        with pytest.raises(ValidationError, match="name the party"):
            journals.post(
                admin,
                JournalDraft(
                    posting_date=TODAY,
                    narration="A receivable with nobody owing it",
                    source=JournalSource.SALES,
                    lines=[
                        EntryLine("11000", Side.DEBIT, Decimal("10.00")),
                        EntryLine("40000", Side.CREDIT, Decimal("10.00")),
                    ],
                ),
            )

    def test_a_line_filed_under_the_wrong_subledger_is_refused(
        self, journals: JournalService, admin: Actor
    ) -> None:
        with pytest.raises(ValidationError, match="cannot be filed under"):
            journals.post(
                admin,
                JournalDraft(
                    posting_date=TODAY,
                    narration="A receivable filed as a supplier",
                    source=JournalSource.SALES,
                    lines=[
                        EntryLine(
                            "11000",
                            Side.DEBIT,
                            Decimal("10.00"),
                            party_id=uuid.uuid4(),
                            subledger=SubledgerKind.SUPPLIER,
                        ),
                        EntryLine("40000", Side.CREDIT, Decimal("10.00")),
                    ],
                ),
            )

    def test_posting_requires_the_permission(self, journals: JournalService, admin: Actor) -> None:
        spectator = make_actor(admin.business_id, permissions={"journal.view"})  # type: ignore[arg-type]
        with pytest.raises(PermissionDeniedError):
            journals.post(
                spectator,
                JournalDraft(
                    posting_date=TODAY,
                    narration="Without authority",
                    lines=[
                        EntryLine("10100", Side.DEBIT, Decimal("1.00")),
                        EntryLine("40100", Side.CREDIT, Decimal("1.00")),
                    ],
                ),
            )

    def test_a_posting_outside_every_period_is_refused(
        self, journals: JournalService, admin: Actor
    ) -> None:
        with pytest.raises(ConflictError, match="No accounting period"):
            journals.post(
                admin,
                JournalDraft(
                    posting_date=date(2019, 3, 1),
                    narration="Long before the system",
                    lines=[
                        EntryLine("10100", Side.DEBIT, Decimal("1.00")),
                        EntryLine("40100", Side.CREDIT, Decimal("1.00")),
                    ],
                ),
            )


class TestIdempotency:
    def test_a_repeated_key_returns_the_original_and_creates_nothing(
        self, db: Session, journals: JournalService, admin: Actor
    ) -> None:
        draft = JournalDraft(
            posting_date=TODAY,
            narration="Receipt 9931",
            source=JournalSource.RECEIPT,
            idempotency_key="receipt:9931",
            lines=[
                EntryLine("10100", Side.DEBIT, Decimal("300.00")),
                EntryLine("40100", Side.CREDIT, Decimal("300.00")),
            ],
        )
        first = journals.post(admin, draft)
        second = journals.post(admin, draft)
        written = db.scalar(
            select(func.count())
            .select_from(JournalEntry)
            .where(JournalEntry.idempotency_key == "receipt:9931")
        )
        assert written == 1
        assert first.entry.id == second.entry.id
        assert first.created and not second.created

    def test_entries_without_a_key_are_not_deduplicated(
        self, journals: JournalService, admin: Actor
    ) -> None:
        first = capital(journals, admin, "25.00")
        second = capital(journals, admin, "25.00")
        assert first.id != second.id


class TestImmutability:
    def test_a_posted_entry_cannot_be_amended(
        self, db: Session, journals: JournalService, admin: Actor
    ) -> None:
        entry = capital(journals, admin)
        db.commit()
        entry.narration = "Tampered with"
        with pytest.raises(ConflictError, match="cannot be amended"):
            db.flush()
        db.rollback()

    def test_a_posted_line_cannot_be_amended(
        self, db: Session, journals: JournalService, admin: Actor
    ) -> None:
        entry = capital(journals, admin)
        db.commit()
        entry.lines[0].debit = Decimal("1.00")
        with pytest.raises(ConflictError, match="posted journal line"):
            db.flush()
        db.rollback()

    def test_a_posted_entry_cannot_be_deleted(
        self, db: Session, journals: JournalService, admin: Actor
    ) -> None:
        entry = capital(journals, admin)
        db.commit()
        db.delete(entry)
        with pytest.raises(ConflictError, match="cannot be deleted"):
            db.flush()
        db.rollback()

    def test_an_audit_entry_cannot_be_altered(self, db: Session, admin: Actor) -> None:
        event = db.scalars(select(AuditEvent).limit(1)).all()[0]
        db.commit()
        event.action = "tampered"
        with pytest.raises(ConflictError, match="append-only"):
            db.flush()
        db.rollback()


class TestReversal:
    def test_a_reversal_mirrors_the_original_and_marks_it(
        self, db: Session, journals: JournalService, admin: Actor
    ) -> None:
        entry = capital(journals, admin)
        db.commit()
        reversal = journals.reverse(admin, entry.id, "Posted to the wrong account")
        db.flush()

        assert reversal.total_debit == entry.total_credit
        assert reversal.reverses_fk == entry.id
        assert reversal.source == JournalSource.REVERSAL.value
        assert entry.state == "reversed"
        assert entry.reversed_by_fk == reversal.id
        assert [line.debit for line in reversal.lines] == [line.credit for line in entry.lines]

    def test_a_reversal_leaves_the_account_where_it_started(
        self,
        db: Session,
        journals: JournalService,
        reports: LedgerReportService,
        accounts: AccountService,
        admin: Actor,
    ) -> None:
        entry = capital(journals, admin)
        db.commit()
        bank = accounts.require_by_code(admin.business_id, "10200")  # type: ignore[arg-type]
        assert reports.account_balance(
            admin.business_id,
            bank.id,
            as_at=TODAY,  # type: ignore[arg-type]
        ) == Decimal("50000")

        journals.reverse(admin, entry.id, "Posted in error")
        db.flush()
        assert reports.account_balance(
            admin.business_id,
            bank.id,
            as_at=TODAY,  # type: ignore[arg-type]
        ) == Decimal("0")

    def test_an_entry_cannot_be_reversed_twice(
        self, db: Session, journals: JournalService, admin: Actor
    ) -> None:
        entry = capital(journals, admin)
        db.commit()
        journals.reverse(admin, entry.id, "First correction")
        db.flush()
        with pytest.raises(ConflictError, match="already reversed"):
            journals.reverse(admin, entry.id, "Second correction")

    def test_a_reversal_requires_a_reason(
        self, db: Session, journals: JournalService, admin: Actor
    ) -> None:
        entry = capital(journals, admin)
        db.commit()
        with pytest.raises(ValidationError, match="reason"):
            journals.reverse(admin, entry.id, "   ")

    def test_an_unknown_entry_is_not_found(self, journals: JournalService, admin: Actor) -> None:
        with pytest.raises(NotFoundError):
            journals.require(admin, uuid.uuid4())


class TestApproval:
    def test_an_entry_above_the_threshold_is_held(
        self, db: Session, journals: JournalService, admin: Actor
    ) -> None:
        journals.set_threshold(admin, Decimal("1000.00"))
        db.flush()
        outcome = journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="A large manual adjustment",
                lines=[
                    EntryLine("60000", Side.DEBIT, Decimal("2500.00")),
                    EntryLine("10200", Side.CREDIT, Decimal("2500.00")),
                ],
            ),
        )
        assert outcome.awaiting_approval
        assert outcome.entry.state == "pending_approval"
        assert outcome.entry.number is None

    def test_a_held_entry_affects_no_balance(
        self,
        db: Session,
        journals: JournalService,
        reports: LedgerReportService,
        accounts: AccountService,
        admin: Actor,
    ) -> None:
        journals.set_threshold(admin, Decimal("1000.00"))
        db.flush()
        journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="A large manual adjustment",
                lines=[
                    EntryLine("60000", Side.DEBIT, Decimal("2500.00")),
                    EntryLine("10200", Side.CREDIT, Decimal("2500.00")),
                ],
            ),
        )
        db.flush()
        rent = accounts.require_by_code(admin.business_id, "60000")  # type: ignore[arg-type]
        assert reports.account_balance(
            admin.business_id,
            rent.id,
            as_at=TODAY,  # type: ignore[arg-type]
        ) == Decimal("0")

    def test_the_maker_cannot_approve_their_own_entry(
        self, db: Session, journals: JournalService, admin: Actor
    ) -> None:
        journals.set_threshold(admin, Decimal("1000.00"))
        db.flush()
        outcome = journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="A large manual adjustment",
                lines=[
                    EntryLine("60000", Side.DEBIT, Decimal("2500.00")),
                    EntryLine("10200", Side.CREDIT, Decimal("2500.00")),
                ],
            ),
        )
        db.flush()
        with pytest.raises(ConflictError, match="person who raised it"):
            journals.approve(admin, outcome.entry.id)

    def test_a_checkers_approval_posts_it(
        self,
        db: Session,
        journals: JournalService,
        reports: LedgerReportService,
        accounts: AccountService,
        admin: Actor,
    ) -> None:
        journals.set_threshold(admin, Decimal("1000.00"))
        db.flush()
        outcome = journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="A large manual adjustment",
                lines=[
                    EntryLine("60000", Side.DEBIT, Decimal("2500.00")),
                    EntryLine("10200", Side.CREDIT, Decimal("2500.00")),
                ],
            ),
        )
        db.flush()
        checker = make_actor(admin.business_id)  # type: ignore[arg-type]
        approved = journals.approve(checker, outcome.entry.id)
        db.flush()

        assert approved.state == "posted"
        assert approved.number is not None
        rent = accounts.require_by_code(admin.business_id, "60000")  # type: ignore[arg-type]
        assert reports.account_balance(
            admin.business_id,
            rent.id,
            as_at=TODAY,  # type: ignore[arg-type]
        ) == Decimal("2500")

    def test_a_declined_entry_affects_nothing_and_stays(
        self, db: Session, journals: JournalService, admin: Actor
    ) -> None:
        journals.set_threshold(admin, Decimal("1000.00"))
        db.flush()
        outcome = journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="A large manual adjustment",
                lines=[
                    EntryLine("60000", Side.DEBIT, Decimal("2500.00")),
                    EntryLine("10200", Side.CREDIT, Decimal("2500.00")),
                ],
            ),
        )
        db.flush()
        checker = make_actor(admin.business_id)  # type: ignore[arg-type]
        declined = journals.reject(checker, outcome.entry.id, "Not supported by an invoice")
        assert declined.state == "rejected"
        assert declined.rejection_reason == "Not supported by an invoice"

    def test_an_entry_below_the_threshold_posts_directly(
        self, db: Session, journals: JournalService, admin: Actor
    ) -> None:
        journals.set_threshold(admin, Decimal("1000.00"))
        db.flush()
        outcome = journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="A small adjustment",
                lines=[
                    EntryLine("60000", Side.DEBIT, Decimal("40.00")),
                    EntryLine("10200", Side.CREDIT, Decimal("40.00")),
                ],
            ),
        )
        assert not outcome.awaiting_approval
        assert outcome.entry.state == "posted"

    def test_an_entry_raised_by_a_transaction_is_never_held(
        self, db: Session, journals: JournalService, admin: Actor
    ) -> None:
        journals.set_threshold(admin, Decimal("10.00"))
        db.flush()
        outcome = journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="A sale at the counter",
                source=JournalSource.POINT_OF_SALE,
                lines=[
                    EntryLine("10100", Side.DEBIT, Decimal("9000.00")),
                    EntryLine("40100", Side.CREDIT, Decimal("9000.00")),
                ],
            ),
        )
        assert outcome.entry.state == "posted"


class TestSubledgers:
    def test_a_party_line_maintains_the_subledger_balance(
        self,
        db: Session,
        journals: JournalService,
        reports: LedgerReportService,
        admin: Actor,
    ) -> None:
        journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="Credit sale to Korle Bu Teaching Hospital",
                source=JournalSource.SALES,
                lines=[
                    EntryLine(
                        "11000",
                        Side.DEBIT,
                        Decimal("4200.00"),
                        party_id=uuid.uuid4(),
                        subledger=SubledgerKind.CUSTOMER,
                    ),
                    EntryLine("40000", Side.CREDIT, Decimal("4200.00")),
                ],
            ),
        )
        db.flush()
        variances = reports.subledger_variances(admin)
        receivables = next(item for item in variances if item.account_code == "11000")
        assert receivables.control_balance == Decimal("4200")
        assert receivables.subledger_balance == Decimal("4200")
        assert receivables.agrees

    def test_a_reversal_carries_through_to_the_subledger(
        self,
        db: Session,
        journals: JournalService,
        reports: LedgerReportService,
        admin: Actor,
    ) -> None:
        entry = journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="Credit sale, later voided",
                source=JournalSource.SALES,
                lines=[
                    EntryLine(
                        "11000",
                        Side.DEBIT,
                        Decimal("4200.00"),
                        party_id=uuid.uuid4(),
                        subledger=SubledgerKind.CUSTOMER,
                    ),
                    EntryLine("40000", Side.CREDIT, Decimal("4200.00")),
                ],
            ),
        ).entry
        db.commit()
        journals.reverse(admin, entry.id, "The order was cancelled")
        db.flush()

        receivables = next(
            item for item in reports.subledger_variances(admin) if item.account_code == "11000"
        )
        assert receivables.control_balance == Decimal("0")
        assert receivables.subledger_balance == Decimal("0")
        assert receivables.agrees


class TestReporting:
    def test_the_trial_balance_sums_to_zero(
        self,
        db: Session,
        journals: JournalService,
        reports: LedgerReportService,
        admin: Actor,
    ) -> None:
        capital(journals, admin)
        db.flush()
        trial = reports.trial_balance(admin, as_at=TODAY)
        assert trial.balanced
        assert trial.total_debit == trial.total_credit == Decimal("50000")
        assert trial.as_at == TODAY
        assert trial.basis is AccountingBasis.ACCRUAL

    def test_the_balance_sheet_balances_and_carries_the_result(
        self,
        db: Session,
        journals: JournalService,
        reports: LedgerReportService,
        admin: Actor,
    ) -> None:
        capital(journals, admin)
        journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="Cash sale",
                source=JournalSource.POINT_OF_SALE,
                lines=[
                    EntryLine("10100", Side.DEBIT, Decimal("1000.00")),
                    EntryLine("40100", Side.CREDIT, Decimal("1000.00")),
                ],
            ),
        )
        db.flush()
        profit = reports.profit_and_loss(admin, start=date(2026, 1, 1), end=TODAY)
        position = reports.balance_sheet(admin, as_at=TODAY)

        assert position.balanced
        assert position.retained_result == profit.net_result == Decimal("1000")
        assert position.total_assets == position.total_liabilities + position.total_equity

    def test_the_unclosed_result_appears_on_the_face_of_the_statement(
        self,
        db: Session,
        journals: JournalService,
        reports: LedgerReportService,
        admin: Actor,
    ) -> None:
        capital(journals, admin)
        journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="Cash sale",
                source=JournalSource.POINT_OF_SALE,
                lines=[
                    EntryLine("10100", Side.DEBIT, Decimal("1000.00")),
                    EntryLine("40100", Side.CREDIT, Decimal("1000.00")),
                ],
            ),
        )
        db.flush()
        position = reports.balance_sheet(admin, as_at=TODAY)
        sections = sum(section.total for section in position.equity)
        assert sections == position.total_equity
        assert any(section.code == "RESULT" for section in position.equity)

    def test_the_cash_basis_excludes_what_has_not_been_paid(
        self,
        db: Session,
        journals: JournalService,
        reports: LedgerReportService,
        admin: Actor,
    ) -> None:
        journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="Cash sale",
                source=JournalSource.POINT_OF_SALE,
                lines=[
                    EntryLine("10100", Side.DEBIT, Decimal("300.00")),
                    EntryLine("40100", Side.CREDIT, Decimal("300.00")),
                ],
            ),
        )
        journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="Credit sale",
                source=JournalSource.SALES,
                lines=[
                    EntryLine(
                        "11000",
                        Side.DEBIT,
                        Decimal("4200.00"),
                        party_id=uuid.uuid4(),
                        subledger=SubledgerKind.CUSTOMER,
                    ),
                    EntryLine("40000", Side.CREDIT, Decimal("4200.00")),
                ],
            ),
        )
        db.flush()
        accrual = reports.profit_and_loss(admin, start=date(2026, 1, 1), end=TODAY)
        cash = reports.profit_and_loss(
            admin, start=date(2026, 1, 1), end=TODAY, basis=AccountingBasis.CASH
        )
        assert accrual.total_revenue == Decimal("4500")
        assert cash.total_revenue == Decimal("300")
        assert cash.basis is AccountingBasis.CASH

    def test_the_cash_flow_reconciles_to_the_cash_accounts(
        self,
        db: Session,
        journals: JournalService,
        reports: LedgerReportService,
        admin: Actor,
    ) -> None:
        capital(journals, admin)
        db.flush()
        flow = reports.cash_flow(admin, start=date(2026, 1, 1), end=TODAY)
        assert flow.reconciles
        assert flow.closing_cash == Decimal("50000")
        assert flow.financing == Decimal("50000")

    def test_the_general_ledger_carries_a_running_balance(
        self,
        db: Session,
        journals: JournalService,
        reports: LedgerReportService,
        accounts: AccountService,
        admin: Actor,
    ) -> None:
        capital(journals, admin)
        journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="Rent for the Accra premises",
                lines=[
                    EntryLine("60000", Side.DEBIT, Decimal("2400.00")),
                    EntryLine("10200", Side.CREDIT, Decimal("2400.00")),
                ],
            ),
        )
        db.flush()
        bank = accounts.require_by_code(admin.business_id, "10200")  # type: ignore[arg-type]
        ledger = reports.account_ledger(admin, bank.id, end=TODAY)
        assert [movement.running_balance for movement in ledger.movements] == [
            Decimal("50000"),
            Decimal("47600"),
        ]
        assert ledger.closing_balance == Decimal("47600")
        assert ledger.presented_side is Side.DEBIT

    def test_a_reversed_entry_stays_on_the_report(
        self,
        db: Session,
        journals: JournalService,
        reports: LedgerReportService,
        accounts: AccountService,
        admin: Actor,
    ) -> None:
        entry = capital(journals, admin)
        db.commit()
        journals.reverse(admin, entry.id, "Posted in error")
        db.flush()
        bank = accounts.require_by_code(admin.business_id, "10200")  # type: ignore[arg-type]
        ledger = reports.account_ledger(admin, bank.id, end=TODAY)
        assert len(ledger.movements) == 2
        assert ledger.closing_balance == Decimal("0")

    def test_reading_a_report_requires_the_permission(
        self, reports: LedgerReportService, admin: Actor
    ) -> None:
        spectator = make_actor(admin.business_id, permissions=set())  # type: ignore[arg-type]
        with pytest.raises(PermissionDeniedError):
            reports.trial_balance(spectator)


class TestPeriodClose:
    def close_everything_before(
        self, periods: PeriodService, accounts: AccountService, admin: Actor, upto: date
    ) -> None:
        for period in accounts.periods(admin, financial_year=2026):
            if period.ends_on < upto:
                periods.close(admin, period.id)

    def test_a_period_closes_when_every_gate_passes(
        self,
        db: Session,
        periods: PeriodService,
        accounts: AccountService,
        journals: JournalService,
        admin: Actor,
    ) -> None:
        capital(journals, admin)
        db.flush()
        self.close_everything_before(periods, accounts, admin, date(2026, 11, 1))
        november = accounts.require_period_for(admin.business_id, TODAY)  # type: ignore[arg-type]
        readiness = periods.close(admin, november.id)
        assert readiness.ready
        assert len(readiness.checks) == 5
        assert november.state == "closed"
        assert november.closed_by_id == admin.user_id

    def test_a_closed_period_refuses_a_posting(
        self,
        db: Session,
        periods: PeriodService,
        accounts: AccountService,
        journals: JournalService,
        admin: Actor,
    ) -> None:
        self.close_everything_before(periods, accounts, admin, date(2026, 11, 1))
        november = accounts.require_period_for(admin.business_id, TODAY)  # type: ignore[arg-type]
        periods.close(admin, november.id)
        db.flush()
        with pytest.raises(ConflictError, match="closed"):
            journals.post(
                admin,
                JournalDraft(
                    posting_date=TODAY,
                    narration="After the close",
                    lines=[
                        EntryLine("60000", Side.DEBIT, Decimal("5.00")),
                        EntryLine("10100", Side.CREDIT, Decimal("5.00")),
                    ],
                ),
            )

    def test_periods_close_in_order(
        self, periods: PeriodService, accounts: AccountService, admin: Actor
    ) -> None:
        november = accounts.require_period_for(admin.business_id, TODAY)  # type: ignore[arg-type]
        with pytest.raises(ConflictError, match="periods close in order"):
            periods.close(admin, november.id)

    def test_a_pending_entry_blocks_the_close(
        self,
        db: Session,
        periods: PeriodService,
        accounts: AccountService,
        journals: JournalService,
        admin: Actor,
    ) -> None:
        journals.set_threshold(admin, Decimal("10.00"))
        db.flush()
        journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="Held for a checker",
                lines=[
                    EntryLine("60000", Side.DEBIT, Decimal("500.00")),
                    EntryLine("10200", Side.CREDIT, Decimal("500.00")),
                ],
            ),
        )
        db.flush()
        self.close_everything_before(periods, accounts, admin, date(2026, 11, 1))
        november = accounts.require_period_for(admin.business_id, TODAY)  # type: ignore[arg-type]
        with pytest.raises(ConflictError, match="pending approval"):
            periods.close(admin, november.id)

    def test_the_gates_are_recorded_with_the_close(
        self,
        db: Session,
        periods: PeriodService,
        accounts: AccountService,
        admin: Actor,
    ) -> None:
        january = accounts.periods(admin, financial_year=2026)[0]
        periods.close(admin, january.id)
        db.flush()
        recorded = periods.checks_recorded(admin, january.id)
        assert len(recorded) == 5
        assert all(check.passed for check in recorded)
        assert {check.name for check in recorded} == {
            "earlier_periods_closed",
            "no_outstanding_entries",
            "entries_balanced",
            "trial_balance_balanced",
            "subledgers_reconciled",
        }

    def test_reopening_records_the_reason_and_the_person(
        self,
        db: Session,
        periods: PeriodService,
        accounts: AccountService,
        admin: Actor,
    ) -> None:
        january = accounts.periods(admin, financial_year=2026)[0]
        periods.close(admin, january.id)
        db.flush()
        reopened = periods.reopen(admin, january.id, "A supplier invoice arrived late")

        assert reopened.state == "reopened"
        assert reopened.reopen_reason == "A supplier invoice arrived late"
        assert reopened.reopened_by_id == admin.user_id
        trail = db.scalars(select(AuditEvent).where(AuditEvent.action == "period_reopened")).all()
        assert len(trail) == 1
        assert trail[0].reason == "A supplier invoice arrived late"

    def test_a_thin_reason_is_refused(
        self,
        db: Session,
        periods: PeriodService,
        accounts: AccountService,
        admin: Actor,
    ) -> None:
        january = accounts.periods(admin, financial_year=2026)[0]
        periods.close(admin, january.id)
        db.flush()
        with pytest.raises(ValidationError, match="substantive reason"):
            periods.reopen(admin, january.id, "oops")

    def test_a_later_closed_period_blocks_an_earlier_reopening(
        self,
        db: Session,
        periods: PeriodService,
        accounts: AccountService,
        admin: Actor,
    ) -> None:
        calendar = accounts.periods(admin, financial_year=2026)
        periods.close(admin, calendar[0].id)
        periods.close(admin, calendar[1].id)
        db.flush()
        with pytest.raises(ConflictError, match="reverse order"):
            periods.reopen(admin, calendar[0].id, "A supplier invoice arrived late")

    def test_closing_requires_the_permission(
        self, periods: PeriodService, accounts: AccountService, admin: Actor
    ) -> None:
        spectator = make_actor(admin.business_id, permissions={"account.view"})  # type: ignore[arg-type]
        january = accounts.periods(admin, financial_year=2026)[0]
        with pytest.raises(PermissionDeniedError):
            periods.close(spectator, january.id)


class TestYearEnd:
    def test_the_year_end_closes_trading_to_reserves(
        self,
        db: Session,
        periods: PeriodService,
        accounts: AccountService,
        journals: JournalService,
        reports: LedgerReportService,
        admin: Actor,
    ) -> None:
        capital(journals, admin)
        journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="Cash sale",
                source=JournalSource.POINT_OF_SALE,
                lines=[
                    EntryLine("10100", Side.DEBIT, Decimal("1000.00")),
                    EntryLine("40100", Side.CREDIT, Decimal("1000.00")),
                ],
            ),
        )
        db.flush()
        for period in accounts.periods(admin, financial_year=2026)[:-1]:
            periods.close(admin, period.id)
        db.flush()

        outcome = periods.close_year(admin, 2026)
        db.flush()

        assert outcome.net_result == Decimal("1000")
        assert outcome.accounts_closed == 1
        assert outcome.entry is not None
        assert outcome.period.year_end_posted_at is not None

        revenue = accounts.require_by_code(admin.business_id, "40100")  # type: ignore[arg-type]
        reserves = accounts.require_by_code(admin.business_id, "30100")  # type: ignore[arg-type]
        assert reports.account_balance(
            admin.business_id,
            revenue.id,
            as_at=date(2026, 12, 31),  # type: ignore[arg-type]
        ) == Decimal("0")
        assert reports.account_balance(
            admin.business_id,
            reserves.id,
            as_at=date(2026, 12, 31),  # type: ignore[arg-type]
        ) == Decimal("1000")

    def test_the_trial_balance_still_sums_to_zero_after_the_year_end(
        self,
        db: Session,
        periods: PeriodService,
        accounts: AccountService,
        journals: JournalService,
        reports: LedgerReportService,
        admin: Actor,
    ) -> None:
        capital(journals, admin)
        journals.post(
            admin,
            JournalDraft(
                posting_date=TODAY,
                narration="Rent",
                lines=[
                    EntryLine("60000", Side.DEBIT, Decimal("2400.00")),
                    EntryLine("10200", Side.CREDIT, Decimal("2400.00")),
                ],
            ),
        )
        db.flush()
        for period in accounts.periods(admin, financial_year=2026)[:-1]:
            periods.close(admin, period.id)
        periods.close_year(admin, 2026)
        db.flush()
        assert reports.trial_balance(admin, as_at=date(2026, 12, 31)).balanced

    def test_an_open_period_blocks_the_year_end(self, periods: PeriodService, admin: Actor) -> None:
        with pytest.raises(ConflictError, match="remain open"):
            periods.close_year(admin, 2026)

    def test_a_year_that_was_never_opened_is_not_found(
        self, periods: PeriodService, admin: Actor
    ) -> None:
        with pytest.raises(NotFoundError):
            periods.close_year(admin, 2031)
