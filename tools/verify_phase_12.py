#!/usr/bin/env python3
"""Verify each Phase 12 exit criterion from docs/00-build-plan.md, and AC-23 to AC-30.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_12.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase12-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.debts import DebtService, OtherDebtDraft  # noqa: E402
from nickland.application.services.expenses import ExpenseDraft, ExpenseService  # noqa: E402
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.ledger_reports import LedgerReportService  # noqa: E402
from nickland.application.services.loans import (  # noqa: E402
    LoanDraft,
    LoanService,
    RevisionDraft,
)
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.application.services.treasury import MovementDraft, TreasuryService  # noqa: E402
from nickland.domain.errors import ConflictError  # noqa: E402
from nickland.domain.finance import FinancialAccountKind  # noqa: E402
from nickland.domain.financing import DebtKind  # noqa: E402
from nickland.domain.money import present  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.immutability import register_append_only_guards  # noqa: E402
from nickland.persistence.ledger_models import Account  # noqa: E402
from nickland.persistence.models import AuditEvent, Business  # noqa: E402
from nickland.platform.clock import FixedClock  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
D = Decimal
START = date(2026, 1, 1)
END = date(2028, 12, 31)
CLOCK = FixedClock(datetime(2026, 11, 21, 10, 0, tzinfo=UTC))
Report = tuple[str, bool, str]


def build() -> tuple[Session, Actor]:
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-12')"))
    factory = create_session_factory(engine)
    register_append_only_guards(factory)
    session = factory()
    SetupService(session, clock=CLOCK).provision(
        SetupRequest(
            business_name="Nicholas Nickland Enterprise",
            administrator_username="proprietor",
            administrator_full_name="N. Nickland",
            administrator_password=ADMIN,
            theme_preset="nickland",
        )
    )
    session.commit()
    business_id = session.scalar(select(Business.id))
    assert business_id is not None
    actor = (
        IdentityService(session, clock=CLOCK)
        .sign_in("proprietor", ADMIN, business_id=business_id)
        .actor
    )
    return session, actor


def verify() -> list[Report]:
    outcomes: list[Report] = []

    def record(label: str, passed: bool, detail: str) -> None:
        outcomes.append((label, passed, detail))

    session, actor = build()
    business_id = actor.business_id
    assert business_id is not None
    reports = LedgerReportService(session, clock=CLOCK)
    treasury = TreasuryService(session, clock=CLOCK)
    loans = LoanService(session, clock=CLOCK)
    debts = DebtService(session, clock=CLOCK)
    bank = treasury.default_for(business_id, FinancialAccountKind.BANK)
    treasury.record(
        actor,
        MovementDraft(
            kind="adjustment_in", amount=D("30000"), to_account_id=bank.id, reason="Float"
        ),
    )

    def balance(code: str) -> Decimal:
        account = session.scalar(
            select(Account).where(Account.business_id == business_id, Account.code == code)
        )
        assert account is not None
        return reports.account_balance(business_id, account.id, as_at=END)

    def result():  # type: ignore[no-untyped-def]
        return reports.profit_and_loss(actor, start=START, end=END)

    def agreement(**changes: object):  # type: ignore[no-untyped-def]
        values: dict[str, object] = {
            "direction": "borrowing",
            "loan_type": "term_loan",
            "counterparty_name": "GCB Bank",
            "principal": D("60000"),
            "agreement_date": date(2026, 3, 1),
            "model": "reducing_balance",
            "annual_rate": D("24"),
            "frequency": "monthly",
            "instalments": 24,
            "first_due_on": date(2026, 4, 1),
            "financial_account_id": bank.id,
            "agreement_reference": "GCB/TL/2026/118",
        }
        values.update(changes)
        row = loans.create(actor, LoanDraft(**values))  # type: ignore[arg-type]
        loans.submit(actor, row.id)
        return row

    # 1. A drawdown: cash and liability up, neither revenue nor profit.
    before = result()
    cash_before = balance("10200")
    gcb = agreement()
    loans.drawdown(actor, gcb.id, amount=D("60000"), on=date(2026, 3, 1))
    after = result()
    record(
        "A loan drawdown raises cash and liability, not revenue or profit",
        balance("10200") - cash_before == D("60000")
        and balance("23000") == D("60000")
        and after.total_revenue == before.total_revenue
        and after.net_result == before.net_result,
        f"cash +{present(balance('10200') - cash_before, 2)}, liability "
        f"{present(balance('23000'), 2)}, revenue and result unchanged",
    )

    # 2. A principal repayment: cash and liability down, not an expense; interest apart.
    first = loans.current(gcb).instalments[0]
    expenses_before = result().total_expenses
    loans.repay(actor, gcb.id, amount=first.owed, on=first.due_on)
    record(
        "Principal repaid is not an expense; interest is recognised separately",
        balance("23000") == D("60000") - first.principal
        and result().total_expenses - expenses_before == first.interest
        and balance("71000") == first.interest,
        f"liability down {present(first.principal, 2)}, the only new expense is "
        f"{present(first.interest, 2)} on 71000 Interest expense",
    )

    # 3. A loan issued is a receivable asset, not expensed.
    expenses_before = result().total_expenses
    advance = agreement(
        direction="lending",
        loan_type="staff_advance",
        counterparty_name="Kofi Mensah",
        principal=D("3000"),
        model="none",
        annual_rate=D("0"),
        instalments=6,
        agreement_date=date(2026, 9, 1),
        first_due_on=date(2026, 9, 30),
        agreement_reference=None,
    )
    loans.drawdown(actor, advance.id, amount=D("3000"), on=date(2026, 9, 1))
    record(
        "A loan or advance issued is a receivable asset and is not expensed",
        balance("11200") == D("3000") and result().total_expenses == expenses_before,
        f"11200 Loans and advances issued {present(balance('11200'), 2)}, expenses unchanged",
    )

    # 4. A partial repayment allocates per the configured order; the agreement is unchanged.
    ordered = agreement(
        counterparty_name="Fidelity Bank",
        principal=D("12000"),
        periodic_fee=D("50"),
        allocation_order=["fees", "interest", "principal", "penalty"],
        agreement_reference="FID-2026-7",
    )
    loans.drawdown(actor, ordered.id, amount=D("12000"), on=date(2026, 3, 1))
    instalment = loans.current(ordered).instalments[0]
    terms_before = dict(loans.current(ordered).terms)
    payment = loans.repay(actor, ordered.id, amount=D("300"), on=instalment.due_on)
    split = {row.component: row.amount for row in payment.allocations}
    expected = {"fees": D("50"), "interest": instalment.interest, "principal": D("10")}
    record(
        "A partial repayment allocates per the agreed order and leaves the agreement intact",
        split == expected
        and instalment.paid_principal == D("10")
        and ordered.principal == D("12000")
        and dict(loans.current(ordered).terms) == terms_before
        and len(ordered.schedules) == 1,
        f"300 split fees {present(split.get('fees', D(0)), 2)}, interest "
        f"{present(split.get('interest', D(0)), 2)}, principal "
        f"{present(split.get('principal', D(0)), 2)}; terms and schedule version unchanged",
    )

    # 5. An amended schedule is versioned; the original reproducible; reason and approval audited.
    original = [(row.due_on, row.interest, row.principal) for row in loans.current(gcb).instalments]
    loans.accrue(actor)
    loans.propose_revision(
        actor,
        gcb.id,
        RevisionDraft(
            kind="payment_holiday",
            reason="Ministry payment for the tender delayed to January",
            effective_on=date(2026, 11, 21),
            first_due_on=date(2026, 12, 21),
            instalments=16,
            grace_periods=1,
        ),
    )
    loans.decide_revision(actor, gcb.id, approve=True, note="Approved by the proprietor")
    versions = loans.versions(actor, gcb.id)
    kept = [(row.due_on, row.interest, row.principal) for row in versions[0].instalments]
    actions = {
        event.action: event
        for event in session.scalars(select(AuditEvent).where(AuditEvent.entity_id == gcb.id))
    }
    proposed = actions.get("loan_revision_proposed")
    approved = actions.get("loan_revision_approved")
    record(
        "An amended schedule is versioned; the original remains, with reason and approval",
        [version.state for version in versions] == ["superseded", "current"]
        and kept == original
        and proposed is not None
        and proposed.reason == "Ministry payment for the tender delayed to January"
        and approved is not None
        and (approved.after_values or {}).get("note") == "Approved by the proprietor",
        f"version 1 kept row for row ({len(kept)} rows), version 2 current; "
        "audit holds the reason and the approval",
    )

    # 6. Receivables and payables apart; no net position.
    ExpenseService(session, clock=CLOCK).record(
        actor,
        ExpenseDraft(
            payee_name="Osu Properties Ltd",
            category_code="60000",
            description="Shop rent",
            net=D("1500"),
            settlement="on_credit",
            payee_reference="OSU-INV-2211",
        ),
    )
    summary = debts.summary(actor)
    owed_to_us = debts.register(actor, owed_to_us=True)
    we_owe = debts.register(actor, owed_to_us=False)
    record(
        "Receivables and payables are presented apart; nothing implies set-off",
        all(line.kind.owed_to_us for line in owed_to_us)
        and not any(line.kind.owed_to_us for line in we_owe)
        and summary.receivables == sum((line.outstanding for line in owed_to_us), D(0))
        and summary.payables == sum((line.outstanding for line in we_owe), D(0))
        and not hasattr(summary, "net_position"),
        f"owed to us {present(summary.receivables, 2)}, we owe {present(summary.payables, 2)}, "
        "two totals and no net figure",
    )

    # AC-23: a customer-style debt shows original, collections and balance; agreements too.
    line = next(row for row in owed_to_us if row.source_id == advance.id)
    loans.repay(actor, advance.id, amount=D("500"), on=date(2026, 9, 30))
    line = next(
        row for row in debts.register(actor, owed_to_us=True) if row.source_id == advance.id
    )
    record(
        "AC-23: a debt shows its original amount, collections and balance",
        line.original == D("3000") and line.settled == D("500") and line.outstanding == D("2500"),
        f"{line.reference}: original {present(line.original, 2)}, collected "
        f"{present(line.settled, 2)}, balance {present(line.outstanding, 2)}",
    )

    # AC-28: every agreement's balance agrees with its ledger subledger.
    disagree = []
    for row in loans.agreements(actor, state="active"):
        position = loans.position(row)
        charges = position.interest_due + position.fees_due + position.penalties_due
        if position.ledger_principal != position.principal_outstanding or (
            position.ledger_accrued != charges
        ):
            disagree.append(row.number)
    record(
        "AC-28: each agreement's position reconciles to the ledger",
        not disagree,
        "principal and accrued charges agree for every active agreement"
        if not disagree
        else f"differ: {', '.join(disagree)}",
    )

    # AC-29: the cash-flow classes keep principal, interest, trade and transfers apart.
    cash = treasury.default_for(business_id, FinancialAccountKind.CASH_DRAWER)
    treasury.record(
        actor,
        MovementDraft(
            kind="transfer",
            amount=D("500"),
            from_account_id=bank.id,
            to_account_id=cash.id,
            reason="Counter float",
        ),
    )
    classes = {row.code: row for row in debts.cash_flow(actor, start=START, end=END)}
    principal_in = classes.get("loan_principal_borrowed")
    interest = classes.get("interest_paid")
    lent = classes.get("loan_principal_lent")
    transfers = classes.get("transfers")
    record(
        "AC-29: loan principal, interest, lending and transfers are classified distinctly",
        principal_in is not None
        and principal_in.inflow == D("72000")
        and interest is not None
        and interest.outflow > D("0")
        and lent is not None
        and lent.outflow == D("3000")
        and transfers is not None
        and transfers.inflow == D("500"),
        f"borrowed in {present(principal_in.inflow, 2) if principal_in else '—'}, interest "
        f"out {present(interest.outflow, 2) if interest else '—'}, lent "
        f"{present(lent.outflow, 2) if lent else '—'}, transfers "
        f"{present(transfers.inflow, 2) if transfers else '—'}",
    )

    # AC-30: no debt counted twice; a hand entry duplicating a source is refused.
    keys = [(row.source_type, row.source_id) for row in we_owe + owed_to_us]
    refused = False
    try:
        debts.record_other(
            actor,
            OtherDebtDraft(
                owed_to_us=False,
                counterparty_name="GCB Bank",
                description="Term loan balance",
                amount=D("50000"),
                financial_account_id=bank.id,
                reference="GCB/TL/2026/118",
            ),
        )
    except ConflictError:
        refused = True
    kinds = {row.kind for row in we_owe}
    record(
        "AC-30: each source is counted once; a duplicate hand entry is refused",
        len(keys) == len(set(keys)) and refused and {DebtKind.BORROWING, DebtKind.EXPENSE} <= kinds,
        f"{len(keys)} lines from {len(set(keys))} distinct sources; the agreement's balance "
        "re-entered by hand was refused",
    )
    session.commit()

    trial = reports.trial_balance(actor, as_at=END)
    record(
        "The trial balance agrees",
        trial.balanced,
        f"debits {present(trial.total_debit, 2)} equal credits {present(trial.total_credit, 2)}",
    )
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
    broken = [label for label, passed, _ in outcomes if not passed]
    if broken:
        print(f"{len(broken)} criterion(a) not met: {broken}")
        return 1
    print("Every Phase 12 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
