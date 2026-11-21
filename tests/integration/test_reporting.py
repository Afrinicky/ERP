"""Reports, the management pack, saved runs, schedules and the period close against a real
database. PRD §8, §20.6, §29.8, §29.9, AC-22."""

from __future__ import annotations

import csv
import io
import re
import uuid
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.accounts import AccountService
from nickland.application.services.identity import IdentityService
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.management_pack import ManagementPackService
from nickland.application.services.periods import CHECKLIST, PeriodService
from nickland.application.services.report_runs import (
    ReportArchive,
    ReportScheduler,
    ScheduleDraft,
)
from nickland.application.services.reports import ReportParameters, ReportService
from nickland.documents.report_export import present, to_csv, to_html, to_xlsx
from nickland.domain.errors import (
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from nickland.domain.ledger import EntryLine, JournalSource, Side
from nickland.domain.permissions import ALL_CODES, ROLES_BY_CODE
from nickland.domain.reporting import (
    REPORTS,
    ColumnKind,
    Comparison,
    Granularity,
    Report,
)
from nickland.persistence.models import AuditEvent
from nickland.persistence.reporting_models import ReportDelivery
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

STAFF = "Counter9Staff"
MONTH = ReportParameters(granularity=Granularity.MONTH, anchor=date(2026, 11, 5))


@pytest.fixture
def journals(db: Session, clock: FixedClock) -> JournalService:
    return JournalService(db, clock=clock)


@pytest.fixture
def reports(db: Session, clock: FixedClock) -> ReportService:
    return ReportService(db, clock=clock)


@pytest.fixture
def pack(db: Session, clock: FixedClock) -> ManagementPackService:
    return ManagementPackService(db, clock=clock)


@pytest.fixture
def periods(db: Session, clock: FixedClock) -> PeriodService:
    return PeriodService(db, clock=clock)


@pytest.fixture
def trading(db: Session, journals: JournalService, admin: Actor) -> None:
    """Capital, a register sale and rent in November, and a sale in October."""
    for on, narration, lines, source in (
        (
            date(2026, 10, 20),
            "October sales",
            [("10100", Side.DEBIT, "800.00"), ("40100", Side.CREDIT, "800.00")],
            JournalSource.POINT_OF_SALE,
        ),
        (
            date(2026, 11, 2),
            "Capital introduced",
            [("10200", Side.DEBIT, "50000.00"), ("30000", Side.CREDIT, "50000.00")],
            None,
        ),
        (
            date(2026, 11, 3),
            "Counter sales",
            [("10100", Side.DEBIT, "1200.00"), ("40100", Side.CREDIT, "1200.00")],
            JournalSource.POINT_OF_SALE,
        ),
        (
            date(2026, 11, 4),
            "Rent for November",
            [("60000", Side.DEBIT, "1500.00"), ("10200", Side.CREDIT, "1500.00")],
            None,
        ),
    ):
        draft = JournalDraft(
            posting_date=on,
            narration=narration,
            lines=[EntryLine(code, side, Decimal(amount)) for code, side, amount in lines],
            **({"source": source} if source else {}),
        )
        journals.post(admin, draft)
    db.flush()


def seller(db: Session, clock: FixedClock, admin: Actor) -> Actor:
    identity = IdentityService(db, clock=clock)
    identity.create_user(
        admin,
        username="counter1",
        full_name="E. Mensah",
        password=STAFF,
        role_codes=["sales_person"],
        must_change_password=False,
    )
    db.flush()
    assert admin.business_id is not None
    return identity.sign_in("counter1", STAFF, business_id=admin.business_id).actor


def money_cells(report: Report) -> list[Decimal]:
    found = []
    for section in report.sections:
        for table in section.tables:
            kinds = {column.key: column.kind for column in table.columns}
            for row in [*table.rows, *([table.total] if table.total else [])]:
                for key, value in row.cells.items():
                    if kinds.get(key) is ColumnKind.MONEY and isinstance(value, Decimal):
                        found.append(value)
    return found


class TestOperationalReports:
    @pytest.mark.parametrize("report_id", [item.value for item in REPORTS])
    def test_every_report_runs_and_states_what_it_covers(
        self, reports: ReportService, admin: Actor, trading: None, report_id: str
    ) -> None:
        report = reports.run(admin, report_id, MONTH)
        head = report.header
        assert head.currency == "GHS"
        assert head.basis
        assert head.generated_at is not None
        assert head.period
        assert report.sections

    def test_a_comparison_sets_the_prior_window_beside_each_figure(
        self, reports: ReportService, admin: Actor, trading: None
    ) -> None:
        report = reports.run(
            admin,
            "finance",
            ReportParameters(
                granularity=Granularity.MONTH, anchor=date(2026, 11, 5), comparison=Comparison.PRIOR
            ),
        )
        assert report.header.comparison == "Prior period"
        assert report.header.comparison_starts_on == date(2026, 10, 1)
        figures = {figure.key: figure for figure in report.sections[0].figures}
        assert figures["revenue"].value == Decimal("1200.00")
        assert figures["revenue"].prior == Decimal("800.00")
        assert figures["revenue"].change == Decimal("50.0")

    def test_the_financial_summary_against_budget(
        self, db: Session, clock: FixedClock, reports: ReportService, admin: Actor, trading: None
    ) -> None:
        from nickland.application.services.budgets import BudgetDraft, BudgetService, LineDraft
        from nickland.domain.planning import PeriodKind

        budgets = BudgetService(db, clock=clock)
        budget = budgets.create(
            admin,
            BudgetDraft(
                name="Plan",
                period_kind=PeriodKind.MONTHLY,
                starts_on=date(2026, 1, 1),
                ends_on=date(2026, 12, 31),
            ),
        )
        budgets.set_lines(
            admin, budget.id, [LineDraft(account_code="60000", total=Decimal("12000"))]
        )
        budgets.submit(admin, budget.id)
        other = make_actor(admin.business_id, permissions=set(ALL_CODES))  # type: ignore[arg-type]
        budgets.approve(other, budget.id)
        db.flush()
        report = reports.run(
            admin,
            "finance",
            ReportParameters(
                granularity=Granularity.MONTH,
                anchor=date(2026, 11, 5),
                comparison=Comparison.BUDGET,
            ),
        )
        rent = next(row for row in report.sections[1].tables[0].rows if row.key == "acc:60000")
        assert rent.cells["amount"] == Decimal("1500.00")
        assert rent.cells["amount__prior"] == Decimal("1000.00")
        assert rent.cells["amount__change"] == Decimal("500.00")

    def test_a_grouping_the_report_does_not_offer_is_refused(
        self, reports: ReportService, admin: Actor
    ) -> None:
        with pytest.raises(ValidationError, match="grouping"):
            reports.run(admin, "sales", ReportParameters(grouping="planet"))
        with pytest.raises(ValidationError, match="filtered"):
            reports.run(admin, "sales", ReportParameters(filters={"warehouse": str(uuid.uuid4())}))


class TestSensitivity:
    def test_a_report_disclosing_cost_is_closed_to_a_sales_person(
        self, db: Session, clock: FixedClock, reports: ReportService, admin: Actor
    ) -> None:
        counter = seller(db, clock, admin)
        for report_id in ("order_profitability", "supplier_comparison", "purchasing", "finance"):
            with pytest.raises(PermissionDeniedError):
                reports.run(counter, report_id, MONTH)
        with pytest.raises(PermissionDeniedError):
            ManagementPackService(db, clock=clock).build(counter, MONTH)
        catalogue = {entry.definition.id.value: entry for entry in reports.catalogue(counter)}
        assert not catalogue["finance"].available
        assert catalogue["sales"].available and not catalogue["sales"].exportable

    def test_cost_columns_are_withheld_from_an_open_report(
        self, db: Session, clock: FixedClock, reports: ReportService, admin: Actor
    ) -> None:
        counter = seller(db, clock, admin)
        report = reports.run(counter, "inventory", MONTH)
        assert report.withheld
        positions = report.sections[0].tables[0]
        assert {"value", "unit_cost"}.isdisjoint(column.key for column in positions.columns)
        assert "value" not in {figure.key for figure in report.sections[0].figures}
        full = reports.run(admin, "inventory", MONTH)
        assert not full.withheld

    def test_the_sales_person_role_does_not_hold_the_sensitive_permission(self) -> None:
        assert "report.export_sensitive" not in ROLES_BY_CODE["sales_person"].permissions


class TestManagementPack:
    def test_a_month_produces_a_pack_that_reconciles_to_the_ledger(
        self, pack: ManagementPackService, admin: Actor, trading: None
    ) -> None:
        report = pack.build(
            admin,
            ReportParameters(
                granularity=Granularity.MONTH, anchor=date(2026, 11, 5), comparison=Comparison.PRIOR
            ),
        )
        keys = [section.key for section in report.sections]
        assert keys == [
            "summary",
            "profit_and_loss",
            "balance_sheet",
            "cash_flow",
            "trial_balance",
            "sales",
            "profitability",
            "procurement",
            "expenses",
            "tax",
            "ageing",
            "loans",
            "inventory",
            "budget",
            "risks",
        ]
        assert report.checks, "the pack carries its reconciliation"
        assert report.reconciled, [
            (check.label, check.reported, check.ledger)
            for check in report.checks
            if not check.passed
        ]
        summary = {figure.key: figure for figure in report.sections[0].figures}
        assert summary["revenue"].value == Decimal("1200.00")
        assert summary["net"].value == Decimal("-300.00")
        assert summary["revenue"].prior == Decimal("800.00")
        names = {check.key for check in report.checks}
        assert {
            "trial_balance",
            "net_result",
            "balance_sheet",
            "cash_opening",
            "cash_closing",
            "receivables",
            "payables",
        } <= names

    def test_an_unusual_rise_in_an_expense_is_named(
        self, pack: ManagementPackService, admin: Actor, trading: None
    ) -> None:
        report = pack.build(
            admin,
            ReportParameters(
                granularity=Granularity.MONTH, anchor=date(2026, 11, 5), comparison=Comparison.PRIOR
            ),
        )
        expenses = next(section for section in report.sections if section.key == "expenses")
        assert any("Rent rose by 1,500.00" in sentence for sentence in expenses.commentary)


class TestExports:
    def test_exported_figures_match_the_screen_exactly(
        self, pack: ManagementPackService, admin: Actor, trading: None
    ) -> None:
        report = pack.build(
            admin,
            ReportParameters(
                granularity=Granularity.MONTH, anchor=date(2026, 11, 5), comparison=Comparison.PRIOR
            ),
        )
        figures = money_cells(report)
        assert figures
        exact = {format(value, "f") for value in figures}

        text = to_csv(report).decode("utf-8-sig")
        csv_values = {cell for row in csv.reader(io.StringIO(text)) for cell in row}
        assert exact <= csv_values

        with zipfile.ZipFile(io.BytesIO(to_xlsx(report))) as book:
            sheet = book.read("xl/worksheets/sheet1.xml").decode()
        assert exact <= set(re.findall(r"<v>([^<]+)</v>", sheet))

        markup = to_html(report)
        for value in figures:
            assert present(value, ColumnKind.MONEY) in markup


class TestSavedRuns:
    def test_a_saved_run_keeps_its_figures_and_takes_notes(
        self,
        db: Session,
        clock: FixedClock,
        pack: ManagementPackService,
        journals: JournalService,
        admin: Actor,
        trading: None,
    ) -> None:
        archive = ReportArchive(db, clock=clock)
        params = ReportParameters(granularity=Granularity.MONTH, anchor=date(2026, 11, 5))
        run = archive.save(admin, pack.build(admin, params), params)
        db.flush()
        journals.post(
            admin,
            JournalDraft(
                posting_date=date(2026, 11, 5),
                narration="A late sale",
                source=JournalSource.POINT_OF_SALE,
                lines=[
                    EntryLine("10100", Side.DEBIT, Decimal("100.00")),
                    EntryLine("40100", Side.CREDIT, Decimal("100.00")),
                ],
            ),
        )
        db.flush()
        _, kept = archive.open(admin, run.id)
        assert kept.sections[0].figures[1].value == Decimal("1200.00")
        assert pack.build(admin, params).sections[0].figures[1].value == Decimal("1300.00")

        archive.annotate(admin, run.id, "summary", "Rent is paid a quarter ahead from January.")
        db.flush()
        assert run.notes == {"summary": "Rent is paid a quarter ahead from January."}
        assert db.scalars(select(AuditEvent).where(AuditEvent.action == "report_note")).all()
        with pytest.raises(ValidationError):
            archive.annotate(admin, run.id, "nowhere", "A note")

    def test_a_sensitive_run_is_not_listed_for_a_sales_person(
        self, db: Session, clock: FixedClock, pack: ManagementPackService, admin: Actor
    ) -> None:
        archive = ReportArchive(db, clock=clock)
        run = archive.save(admin, pack.build(admin, MONTH), MONTH)
        db.flush()
        counter = seller(db, clock, admin)
        assert run.id not in {item.id for item in archive.runs(counter)}
        with pytest.raises(PermissionDeniedError):
            archive.open(counter, run.id)


class TestSchedules:
    def test_a_due_schedule_runs_as_the_system_and_delivers(
        self, db: Session, clock: FixedClock, admin: Actor, trading: None
    ) -> None:
        scheduler = ReportScheduler(db, clock=clock)
        schedule = scheduler.create(
            admin,
            ScheduleDraft(
                name="Monthly management report",
                report_id="management_pack",
                frequency="monthly",
                run_day=1,
                run_hour=7,
                recipients=[admin.user_id],
                formats=["pdf"],
                parameters={"comparison": "prior"},
            ),
        )
        db.flush()
        assert schedule.next_run_at is not None
        assert scheduler.run_due(admin.business_id) == []  # type: ignore[arg-type]

        clock.advance((schedule.next_run_at - clock.now()).total_seconds() + 60)
        runs = scheduler.run_due(admin.business_id)  # type: ignore[arg-type]
        db.flush()
        assert len(runs) == 1
        assert runs[0].period_label == "November 2026"
        assert runs[0].schedule_fk == schedule.id
        deliveries = db.scalars(select(ReportDelivery)).all()
        assert [item.recipient_id for item in deliveries] == [admin.user_id]
        assert schedule.next_run_at.month == 1
        inbox = ReportArchive(db, clock=clock).inbox(admin)
        assert len(inbox) == 1 and inbox[0].delivery.read_at is None

    def test_a_recipient_who_may_not_read_the_report_is_refused(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        counter = seller(db, clock, admin)
        with pytest.raises(ValidationError, match="may not read"):
            ReportScheduler(db, clock=clock).create(
                admin,
                ScheduleDraft(
                    name="Pack",
                    report_id="management_pack",
                    frequency="monthly",
                    run_day=1,
                    run_hour=7,
                    recipients=[counter.user_id],
                    formats=["pdf"],
                    parameters={},
                ),
            )


class TestPeriodClose:
    def november(self, db: Session, clock: FixedClock, admin: Actor):  # type: ignore[no-untyped-def]
        accounts = AccountService(db, clock=clock)
        return accounts.require_period_for(admin.business_id, date(2026, 11, 5))  # type: ignore[arg-type]

    def close_through(
        self, db: Session, clock: FixedClock, periods: PeriodService, admin: Actor, last: date
    ) -> None:
        accounts = AccountService(db, clock=clock)
        for period in accounts.periods(admin, financial_year=2026):
            if period.ends_on <= last:
                for key, _ in CHECKLIST:
                    periods.confirm_step(admin, period.id, key, done=True)
                periods.close(admin, period.id)
        db.flush()

    def test_the_checklist_must_be_confirmed_before_the_close(
        self, db: Session, clock: FixedClock, periods: PeriodService, admin: Actor
    ) -> None:
        accounts = AccountService(db, clock=clock)
        january = accounts.periods(admin, financial_year=2026)[0]
        checklist = periods.checklist(admin, january.id)
        assert not checklist.ready
        assert [step.key for step in checklist.steps] == [key for key, _ in CHECKLIST]
        with pytest.raises(ConflictError, match="Confirm the remaining steps"):
            periods.close(admin, january.id)
        for key, _ in CHECKLIST:
            periods.confirm_step(admin, january.id, key, done=True)
        assert periods.checklist(admin, january.id).ready
        periods.close(admin, january.id)
        assert january.state == "closed"

    def test_a_closed_period_refuses_posting(
        self,
        db: Session,
        clock: FixedClock,
        periods: PeriodService,
        journals: JournalService,
        admin: Actor,
    ) -> None:
        self.close_through(db, clock, periods, admin, date(2026, 11, 30))
        with pytest.raises(ConflictError, match="closed"):
            journals.post(
                admin,
                JournalDraft(
                    posting_date=date(2026, 11, 5),
                    narration="Into a closed month",
                    lines=[
                        EntryLine("60000", Side.DEBIT, Decimal("5.00")),
                        EntryLine("10100", Side.CREDIT, Decimal("5.00")),
                    ],
                ),
            )

    def test_reopening_is_requested_and_decided_by_someone_else(
        self, db: Session, clock: FixedClock, periods: PeriodService, admin: Actor
    ) -> None:
        self.close_through(db, clock, periods, admin, date(2026, 11, 30))
        november = self.november(db, clock, admin)
        clerk = make_actor(
            admin.business_id,
            permissions={
                "account.view",
                "period.close",  # type: ignore[arg-type]
                "period.reopen",
            },
        )
        controller = make_actor(
            admin.business_id,
            permissions={
                "account.view",  # type: ignore[arg-type]
                "period.reopen",
            },
        )
        with pytest.raises(ConflictError, match="second person"):
            periods.reopen(clerk, november.id, "A supplier invoice arrived late")
        request = periods.request_reopen(clerk, november.id, "A supplier invoice arrived late")
        with pytest.raises(ConflictError, match="someone other"):
            periods.decide_reopen(clerk, request.id, approve=True)
        periods.decide_reopen(controller, request.id, approve=True, note="Agreed")
        db.flush()
        assert november.state == "reopened"
        assert november.reopened_by_id == clerk.user_id
        assert november.reopen_approved_by_id == controller.user_id
        assert request.state == "approved" and not request.self_approved
        trail = db.scalars(select(AuditEvent).where(AuditEvent.action == "period_reopened")).all()
        assert trail[-1].reason == "A supplier invoice arrived late"

    def test_the_administrator_may_approve_their_own_request_and_it_is_recorded(
        self, db: Session, clock: FixedClock, periods: PeriodService, admin: Actor
    ) -> None:
        self.close_through(db, clock, periods, admin, date(2026, 11, 30))
        november = self.november(db, clock, admin)
        request = periods.request_reopen(admin, november.id, "Correct the November rent accrual")
        periods.decide_reopen(admin, request.id, approve=True)
        db.flush()
        assert request.self_approved
        assert november.state == "reopened"
        after = (
            db.scalars(select(AuditEvent).where(AuditEvent.action == "period_reopened"))
            .all()[-1]
            .after_values
        )
        assert after["self_approved"] is True

    def test_a_thin_reason_and_a_second_request_are_refused(
        self, db: Session, clock: FixedClock, periods: PeriodService, admin: Actor
    ) -> None:
        self.close_through(db, clock, periods, admin, date(2026, 11, 30))
        november = self.november(db, clock, admin)
        with pytest.raises(ValidationError, match="substantive"):
            periods.request_reopen(admin, november.id, "oops")
        periods.request_reopen(admin, november.id, "Correct the November rent accrual")
        with pytest.raises(ConflictError, match="already waiting"):
            periods.request_reopen(admin, november.id, "Correct the November rent accrual")
