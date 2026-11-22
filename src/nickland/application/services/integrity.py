"""Whether the record is whole. PRD §9.5: no partial stock or accounting posting.

Run after anything that could have cut a transaction short (a host restart, a power cut,
a client that vanished mid-request) and by the restore drill. Each check is an invariant
that a transaction either keeps entirely or never begins:

* every posted journal entry balances, line by line, and agrees with its own totals;
* a completed register sale has its posting, and a sale not completed has none;
* every posted goods receipt and supplier invoice has its posting;
* stock positions agree with the movements and the cost layers they summarise;
* the trial balance balances, and each control account agrees with its subledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.context import AnyActor
from nickland.application.services.inventory_reports import InventoryReportService
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.persistence.ledger_models import JournalEntry, JournalLine
from nickland.persistence.order_models import GoodsReceipt, SupplierInvoice
from nickland.persistence.pos_models import PosSale
from nickland.platform.clock import Clock, SystemClock

POSTED = ("posted", "reversed")


@dataclass(slots=True)
class IntegrityReport:
    checks: dict[str, bool] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def whole(self) -> bool:
        return not self.problems

    def record(self, name: str, problems: list[str]) -> None:
        self.checks[name] = not problems
        self.problems.extend(problems)


class IntegrityService:
    __slots__ = ("_clock", "_db")

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()

    def check(self, actor: AnyActor) -> IntegrityReport:
        actor.require("account.view")
        business = actor.business_id
        report = IntegrityReport()

        unbalanced = self._db.execute(
            select(
                JournalEntry.number,
                JournalEntry.total_debit,
                JournalEntry.total_credit,
                func.coalesce(func.sum(JournalLine.debit), 0),
                func.coalesce(func.sum(JournalLine.credit), 0),
            )
            .join(JournalLine, JournalLine.entry_fk == JournalEntry.id, isouter=True)
            .where(JournalEntry.business_id == business, JournalEntry.state.in_(POSTED))
            .group_by(
                JournalEntry.id,
                JournalEntry.number,
                JournalEntry.total_debit,
                JournalEntry.total_credit,
            )
        ).all()
        report.record(
            "Every posted entry balances",
            [
                f"Entry {number} does not balance or disagrees with its lines."
                for number, debit, credit, lines_debit, lines_credit in unbalanced
                if debit != credit or lines_debit != debit or lines_credit != credit
            ],
        )

        sales = self._db.execute(
            select(PosSale.number, PosSale.state, PosSale.journal_entry_fk).where(
                PosSale.business_id == business
            )
        ).all()
        report.record(
            "Register sales are posted whole",
            [
                f"Sale {number or '(unnumbered)'} is {state} "
                + ("without its posting." if state == "completed" else "yet carries a posting.")
                for number, state, entry in sales
                if (state in ("completed", "refunded", "partially_refunded")) != (entry is not None)
            ],
        )

        receipts = self._db.execute(
            select(GoodsReceipt.number, GoodsReceipt.state, GoodsReceipt.journal_entry_fk).where(
                GoodsReceipt.business_id == business
            )
        ).all()
        invoices = self._db.execute(
            select(
                SupplierInvoice.number, SupplierInvoice.state, SupplierInvoice.journal_entry_fk
            ).where(SupplierInvoice.business_id == business)
        ).all()
        report.record(
            "Receipts and supplier invoices are posted whole",
            [
                f"{number} is {state} without its posting."
                for number, state, entry in [*receipts, *invoices]
                if state in ("posted", "partially_paid", "paid") and entry is None
            ],
        )

        reconciliation = InventoryReportService(self._db, clock=self._clock).reconcile(actor)
        report.record(
            "Stock agrees with its movements and layers",
            [f"Stock disagrees: {item}" for item in reconciliation.discrepancies[:10]],
        )

        ledger = LedgerReportService(self._db, clock=self._clock)
        trial = ledger.trial_balance(actor)
        report.record(
            "The trial balance balances",
            [] if trial.balanced else [f"The trial balance is out by {trial.difference}."],
        )
        report.record(
            "Control accounts agree with their subledgers",
            [
                f"{item.account_code} is out by {item.difference} against its subledger."
                for item in ledger.subledger_variances(actor)
                if not item.agrees
            ],
        )
        return report


__all__ = ["IntegrityReport", "IntegrityService"]
