#!/usr/bin/env python3
"""Verify each Phase 3 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_3.py

The six criteria are the properties the accounting model rests on. Each is tested by
attempting the thing that must not be possible, not by asserting that the code path
exists.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase3-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import func, select, text  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.accounts import AccountService  # noqa: E402
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.journals import JournalDraft, JournalService  # noqa: E402
from nickland.application.services.ledger_reports import LedgerReportService  # noqa: E402
from nickland.application.services.periods import PeriodService  # noqa: E402
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.domain.errors import ConflictError, ValidationError  # noqa: E402
from nickland.domain.ledger import (  # noqa: E402
    AccountingBasis,
    EntryLine,
    JournalSource,
    Side,
    SubledgerKind,
)
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.ledger_models import (  # noqa: E402
    Account,
    JournalEntry,
)
from nickland.persistence.models import AuditEvent, Business  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
TODAY = date(2026, 11, 6)
Outcome = tuple[str, bool, str]


def build_session() -> tuple[object, Actor, uuid.UUID]:
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-3')"))

    session = create_session_factory(engine)()
    SetupService(session).provision(
        SetupRequest(
            business_name="Nicholas Nickland Enterprise",
            administrator_username="proprietor",
            administrator_full_name="N. Nickland",
            administrator_password=ADMIN,
            theme_preset="charcoal",
        )
    )
    session.commit()

    business_id = session.scalar(select(Business.id))
    assert business_id is not None
    actor = IdentityService(session).sign_in("proprietor", ADMIN, business_id=business_id).actor
    session.commit()
    return session, actor, business_id


def verify() -> list[Outcome]:
    outcomes: list[Outcome] = []

    def record(label: str, passed: bool, detail: str = "") -> None:
        outcomes.append((label, passed, detail))

    session, actor, business_id = build_session()
    journals = JournalService(session)  # type: ignore[arg-type]
    accounts = AccountService(session)  # type: ignore[arg-type]
    reports = LedgerReportService(session)  # type: ignore[arg-type]
    periods = PeriodService(session)  # type: ignore[arg-type]

    # --- Criterion 1 ------------------------------------------------------------------
    # An unbalanced journal cannot be posted.
    try:
        journals.post(
            actor,
            JournalDraft(
                posting_date=TODAY,
                narration="Deliberately out of balance",
                lines=[
                    EntryLine("10100", Side.DEBIT, Decimal("100.00")),
                    EntryLine("40100", Side.CREDIT, Decimal("90.00")),
                ],
            ),
        )
        record("An unbalanced journal cannot post", False, "it posted")
    except ValidationError as error:
        session.rollback()
        record("An unbalanced journal cannot post", True, str(error)[:72])

    # Two sound entries to work from.
    journals.post(
        actor,
        JournalDraft(
            posting_date=date(2026, 11, 2),
            narration="Capital introduced by the proprietor",
            lines=[
                EntryLine("10200", Side.DEBIT, Decimal("50000.00")),
                EntryLine("30000", Side.CREDIT, Decimal("50000.00")),
            ],
        ),
    )
    sale = journals.post(
        actor,
        JournalDraft(
            posting_date=date(2026, 11, 3),
            narration="Cash sale of laboratory consumables",
            source=JournalSource.POINT_OF_SALE,
            lines=[
                EntryLine("10100", Side.DEBIT, Decimal("1150.00")),
                EntryLine("40100", Side.CREDIT, Decimal("1000.00")),
                EntryLine(
                    "21000",
                    Side.CREDIT,
                    Decimal("150.00"),
                    party_id=uuid.uuid4(),
                    subledger=SubledgerKind.AUTHORITY,
                ),
            ],
        ),
    )
    session.commit()

    # --- Criterion 2 ------------------------------------------------------------------
    # A posted entry cannot be edited; correction is by reversal only.
    try:
        sale.entry.narration = "Tampered with"
        session.flush()
        record("A posted entry cannot be edited", False, "the change was accepted")
        session.rollback()
    except ConflictError as error:
        session.rollback()
        record("A posted entry cannot be edited", True, str(error)[:72])

    posted = session.get(JournalEntry, sale.entry.id)
    assert posted is not None
    reversal = journals.reverse(actor, posted.id, "Receipt voided at the counter")
    session.commit()
    session.refresh(posted)
    record(
        "A posted entry is corrected by reversal",
        posted.state == "reversed"
        and posted.reversed_by_fk == reversal.id
        and reversal.total_debit == posted.total_credit,
        f"{posted.number} reversed by {reversal.number}",
    )

    # --- Criterion 3 ------------------------------------------------------------------
    # A repeated idempotency key creates exactly one entry.
    key = f"receipt:{uuid.uuid4()}"
    draft = JournalDraft(
        posting_date=date(2026, 11, 4),
        narration="Receipt against an outstanding balance",
        source=JournalSource.RECEIPT,
        idempotency_key=key,
        lines=[
            EntryLine("10100", Side.DEBIT, Decimal("300.00")),
            EntryLine("40100", Side.CREDIT, Decimal("300.00")),
        ],
    )
    first = journals.post(actor, draft)
    session.commit()
    second = journals.post(actor, draft)
    session.commit()
    written = session.scalar(
        select(func.count()).select_from(JournalEntry).where(JournalEntry.idempotency_key == key)
    )
    record(
        "A repeated idempotency key creates one entry",
        written == 1 and first.entry.id == second.entry.id and not second.created,
        f"{written} entry written, the same one returned twice",
    )

    # A credit sale, so a control account and its subledger both carry a balance.
    customer = uuid.uuid4()
    journals.post(
        actor,
        JournalDraft(
            posting_date=date(2026, 11, 4),
            narration="Credit sale to Korle Bu Teaching Hospital",
            source=JournalSource.SALES,
            lines=[
                EntryLine(
                    "11000",
                    Side.DEBIT,
                    Decimal("4200.00"),
                    party_id=customer,
                    subledger=SubledgerKind.CUSTOMER,
                ),
                EntryLine("40000", Side.CREDIT, Decimal("4200.00")),
            ],
        ),
    )
    session.commit()

    # --- Criterion 4 ------------------------------------------------------------------
    # The trial balance sums to zero and the statements reconcile to it.
    trial = reports.trial_balance(actor, as_at=TODAY)
    profit = reports.profit_and_loss(actor, start=date(2026, 1, 1), end=TODAY)
    position = reports.balance_sheet(actor, as_at=TODAY)
    record(
        "The trial balance sums to zero",
        trial.balanced,
        f"debits {trial.total_debit}, credits {trial.total_credit}",
    )
    record(
        "The statements reconcile to the trial balance",
        position.balanced and position.retained_result == profit.net_result,
        f"assets {position.total_assets} = liabilities and equity "
        f"{position.total_liabilities + position.total_equity}; "
        f"result {profit.net_result} carried into equity",
    )

    # --- Criterion 5 ------------------------------------------------------------------
    # Every subledger balance equals its control account.
    variances = reports.subledger_variances(actor)
    disagreeing = [item for item in variances if not item.agrees]
    carrying = [item for item in variances if item.control_balance != Decimal("0")]
    record(
        "Every subledger agrees with its control account",
        not disagreeing,
        f"{len(variances)} control accounts checked, {len(carrying)} carrying a balance",
    )

    # A control account refuses a manual journal, which is what keeps the two in step.
    try:
        journals.post(
            actor,
            JournalDraft(
                posting_date=TODAY,
                narration="Hand-written receivable",
                lines=[
                    EntryLine("11000", Side.DEBIT, Decimal("10.00")),
                    EntryLine("40100", Side.CREDIT, Decimal("10.00")),
                ],
            ),
        )
        record("A control account refuses a manual journal", False, "it posted")
    except ConflictError as error:
        session.rollback()
        record("A control account refuses a manual journal", True, str(error)[:72])

    # --- Criterion 6 ------------------------------------------------------------------
    # A closed period rejects a posting, and reopening it is audited.
    november = accounts.require_period_for(business_id, TODAY)
    for earlier in accounts.periods(actor, financial_year=2026):
        if earlier.starts_on < november.starts_on:
            periods.close(actor, earlier.id)
    session.commit()

    readiness = periods.readiness(actor, november.id)
    periods.close(actor, november.id, note="Month-end close")
    session.commit()
    session.refresh(november)
    record(
        "A period closes only when every gate passes",
        november.state == "closed" and readiness.ready and len(readiness.checks) == 5,
        f"{len(readiness.checks)} reconciliations recorded with the close",
    )

    try:
        journals.post(
            actor,
            JournalDraft(
                posting_date=TODAY,
                narration="A posting after the close",
                lines=[
                    EntryLine("60000", Side.DEBIT, Decimal("5.00")),
                    EntryLine("10100", Side.CREDIT, Decimal("5.00")),
                ],
            ),
        )
        record("A closed period rejects a posting", False, "it posted")
    except ConflictError as error:
        session.rollback()
        record("A closed period rejects a posting", True, str(error)[:72])

    before = session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "period_reopened")
    )
    periods.reopen(actor, november.id, "A supplier invoice arrived after the close was run")
    session.commit()
    session.refresh(november)
    after = session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "period_reopened")
    )
    record(
        "Reopening a period is audited",
        november.state == "reopened"
        and november.reopen_reason is not None
        and november.reopened_by_id == actor.user_id
        and (after or 0) == (before or 0) + 1,
        "the reason, the person and the moment are on the trail",
    )

    try:
        periods.reopen(actor, november.id, "oops")
        record("A reopening without a reason is refused", False, "it was accepted")
    except ValidationError:
        session.rollback()
        record("A reopening without a reason is refused", True, "a substantive reason is required")

    # --- Beyond the criteria ----------------------------------------------------------
    # Maker-checker, and the cash basis, both of which the criteria depend upon.
    journals.set_threshold(actor, Decimal("1000.00"))
    session.commit()
    held = journals.post(
        actor,
        JournalDraft(
            posting_date=TODAY,
            narration="A large manual adjustment",
            lines=[
                EntryLine("60000", Side.DEBIT, Decimal("2500.00")),
                EntryLine("10200", Side.CREDIT, Decimal("2500.00")),
            ],
        ),
    )
    session.commit()
    rent = session.scalar(
        select(Account.id).where(Account.business_id == business_id, Account.code == "60000")
    )
    assert rent is not None
    unposted = reports.account_balance(business_id, rent, as_at=TODAY)
    record(
        "An entry above the threshold awaits a checker",
        held.awaiting_approval and unposted == Decimal("0"),
        "it affects no balance until approved",
    )

    try:
        journals.approve(actor, held.entry.id)
        record("The maker cannot approve their own entry", False, "they approved it")
    except ConflictError:
        session.rollback()
        record(
            "The maker cannot approve their own entry", True, "a second pair of eyes is required"
        )

    checker = Actor(
        user_id=uuid.uuid4(),
        username="akosua",
        full_name="Akosua Mensah",
        business_id=business_id,
        permissions=actor.permissions,
    )
    journals.approve(checker, held.entry.id, note="Reviewed against the supporting invoice")
    session.commit()
    record(
        "A checker's approval posts the entry",
        reports.account_balance(business_id, rent, as_at=TODAY) == Decimal("2500"),
        "the balance moves only on approval",
    )

    accrual = reports.profit_and_loss(actor, start=date(2026, 1, 1), end=TODAY)
    cash = reports.profit_and_loss(
        actor, start=date(2026, 1, 1), end=TODAY, basis=AccountingBasis.CASH
    )
    record(
        "The cash basis differs from the accrual basis, and says so",
        cash.total_revenue < accrual.total_revenue
        and accrual.basis is AccountingBasis.ACCRUAL
        and cash.basis is AccountingBasis.CASH,
        f"accrual revenue {accrual.total_revenue}, cash revenue {cash.total_revenue}",
    )

    flow = reports.cash_flow(actor, start=date(2026, 1, 1), end=TODAY)
    record(
        "The cash-flow statement reconciles to the cash accounts",
        flow.reconciles,
        f"opening {flow.opening_cash} plus {flow.net_movement} equals {flow.closing_cash}",
    )

    final = reports.trial_balance(actor, as_at=TODAY)
    record(
        "The trial balance still sums to zero at the end",
        final.balanced,
        f"{len(final.rows)} accounts, difference {final.difference}",
    )

    event = session.scalars(select(AuditEvent).limit(1)).all()[0]
    try:
        event.action = "tampered"
        session.flush()
        record("The audit trail cannot be altered", False, "the change was accepted")
        session.rollback()
    except ConflictError:
        session.rollback()
        record("The audit trail cannot be altered", True, "it is append-only")

    session.close()
    return outcomes


def main() -> int:
    try:
        outcomes = verify()
    finally:
        shutil.rmtree(WORKSPACE, ignore_errors=True)

    width = max(len(label) for label, _, _ in outcomes)
    print()
    for label, passed, detail in outcomes:
        print(f"  {'PASS' if passed else 'FAIL'}  {label.ljust(width)}  {detail}")
    print()

    failures = [label for label, passed, _ in outcomes if not passed]
    if failures:
        print(f"{len(failures)} criterion(a) not met: {failures}")
        return 1
    print("Every Phase 3 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
