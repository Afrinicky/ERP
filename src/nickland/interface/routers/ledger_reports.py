"""Financial reports: trial balance, general ledger and the statements."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter

from nickland.domain.ledger import AccountingBasis
from nickland.interface.dependencies import ActorDependency, LedgerReportDependency
from nickland.interface.ledger_schemas import (
    AccountLedgerReply,
    AccountReply,
    BalanceSheetReply,
    CashFlowReply,
    LedgerSummaryReply,
    MovementReply,
    ProfitAndLossReply,
    StatementSectionReply,
    TrialBalanceReply,
    TrialBalanceRowReply,
)

router = APIRouter(prefix="/reports/ledger", tags=["financial reports"])


@router.get("/summary", response_model=LedgerSummaryReply, summary="Financial overview")
def summary(
    actor: ActorDependency,
    reports: LedgerReportDependency,
    as_at: date | None = None,
    basis: AccountingBasis = AccountingBasis.ACCRUAL,
) -> LedgerSummaryReply:
    return LedgerSummaryReply.model_validate(reports.summary(actor, as_at=as_at, basis=basis))


@router.get("/trial-balance", response_model=TrialBalanceReply, summary="Trial balance")
def trial_balance(
    actor: ActorDependency,
    reports: LedgerReportDependency,
    as_at: date | None = None,
    start: date | None = None,
    basis: AccountingBasis = AccountingBasis.ACCRUAL,
) -> TrialBalanceReply:
    balance = reports.trial_balance(actor, as_at=as_at, start=start, basis=basis)
    return TrialBalanceReply(
        as_at=balance.as_at,
        starts_on=balance.starts_on,
        basis=balance.basis.value,
        rows=[
            TrialBalanceRowReply(
                code=row.code,
                name=row.name,
                kind=row.kind.value,
                debit=row.presented_debit,
                credit=row.presented_credit,
            )
            for row in balance.rows
        ],
        total_debit=balance.total_debit,
        total_credit=balance.total_credit,
        difference=balance.difference,
        balanced=balance.balanced,
    )


@router.get(
    "/accounts/{account_id}",
    response_model=AccountLedgerReply,
    summary="An account's general ledger",
)
def account_ledger(
    account_id: uuid.UUID,
    actor: ActorDependency,
    reports: LedgerReportDependency,
    start: date | None = None,
    end: date | None = None,
    basis: AccountingBasis = AccountingBasis.ACCRUAL,
    limit: int = 500,
) -> AccountLedgerReply:
    ledger = reports.account_ledger(
        actor, account_id, start=start, end=end, basis=basis, limit=limit
    )
    return AccountLedgerReply(
        account=AccountReply.model_validate(ledger.account),
        starts_on=ledger.starts_on,
        ends_on=ledger.ends_on,
        basis=ledger.basis.value,
        opening_balance=ledger.opening_balance,
        movements=[MovementReply.model_validate(item) for item in ledger.movements],
        total_debit=ledger.total_debit,
        total_credit=ledger.total_credit,
        closing_balance=ledger.closing_balance,
        presented_side=ledger.presented_side.value,
    )


@router.get(
    "/profit-and-loss", response_model=ProfitAndLossReply, summary="Profit and loss account"
)
def profit_and_loss(
    actor: ActorDependency,
    reports: LedgerReportDependency,
    start: date,
    end: date,
    basis: AccountingBasis = AccountingBasis.ACCRUAL,
) -> ProfitAndLossReply:
    statement = reports.profit_and_loss(actor, start=start, end=end, basis=basis)
    return ProfitAndLossReply(
        starts_on=statement.starts_on,
        ends_on=statement.ends_on,
        basis=statement.basis.value,
        revenue=[StatementSectionReply.model_validate(s) for s in statement.revenue],
        expenses=[StatementSectionReply.model_validate(s) for s in statement.expenses],
        total_revenue=statement.total_revenue,
        total_cost_of_sales=statement.total_cost_of_sales,
        gross_profit=statement.gross_profit,
        total_expenses=statement.total_expenses,
        net_result=statement.net_result,
    )


@router.get("/balance-sheet", response_model=BalanceSheetReply, summary="Balance sheet")
def balance_sheet(
    actor: ActorDependency,
    reports: LedgerReportDependency,
    as_at: date | None = None,
    basis: AccountingBasis = AccountingBasis.ACCRUAL,
) -> BalanceSheetReply:
    statement = reports.balance_sheet(actor, as_at=as_at, basis=basis)
    return BalanceSheetReply(
        as_at=statement.as_at,
        basis=statement.basis.value,
        assets=[StatementSectionReply.model_validate(s) for s in statement.assets],
        liabilities=[StatementSectionReply.model_validate(s) for s in statement.liabilities],
        equity=[StatementSectionReply.model_validate(s) for s in statement.equity],
        total_assets=statement.total_assets,
        total_liabilities=statement.total_liabilities,
        total_equity=statement.total_equity,
        total_liabilities_and_equity=statement.total_liabilities_and_equity,
        retained_result=statement.retained_result,
        difference=statement.difference,
        balanced=statement.balanced,
    )


@router.get("/cash-flow", response_model=CashFlowReply, summary="Cash-flow statement")
def cash_flow(
    actor: ActorDependency,
    reports: LedgerReportDependency,
    start: date,
    end: date,
) -> CashFlowReply:
    return CashFlowReply.model_validate(reports.cash_flow(actor, start=start, end=end))


__all__ = ["router"]
