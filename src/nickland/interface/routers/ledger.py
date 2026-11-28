"""Chart of accounts, the accounting calendar and period close."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter

from nickland.interface.dependencies import (
    AccountDependency,
    ActorDependency,
    LedgerReportDependency,
    PeriodDependency,
)
from nickland.interface.ledger_schemas import (
    AccountBalanceReply,
    AccountGroupReply,
    AccountReply,
    AmendAccountContract,
    CloseCheckReply,
    ClosePeriodContract,
    CloseYearContract,
    CreateAccountContract,
    OpenYearContract,
    PeriodReply,
    ReadinessReply,
    ReopenPeriodContract,
    SubledgerVarianceReply,
    YearEndReply,
)

router = APIRouter(prefix="/ledger", tags=["ledger"])


# -- chart of accounts -----------------------------------------------------------------


@router.get("/groups", response_model=list[AccountGroupReply], summary="List account groups")
def list_groups(actor: ActorDependency, accounts: AccountDependency) -> list[AccountGroupReply]:
    return [AccountGroupReply.model_validate(group) for group in accounts.groups(actor)]


@router.get("/accounts", response_model=list[AccountBalanceReply], summary="List accounts")
def list_accounts(
    actor: ActorDependency,
    accounts: AccountDependency,
    reports: LedgerReportDependency,
    term: str | None = None,
    kind: str | None = None,
    include_inactive: bool = False,
    with_balances: bool = True,
    as_at: date | None = None,
) -> list[AccountBalanceReply]:
    """The chart, optionally with each account's balance.

    The balances are gathered in one pass rather than one query per account, so a
    chart of several hundred accounts costs the same as a chart of ten.
    """
    rows = accounts.accounts(actor, term=term, kind=kind, include_inactive=include_inactive)
    replies = [AccountBalanceReply.model_validate(account) for account in rows]
    if not with_balances or actor.business_id is None:
        return replies

    balances = {
        account.id: balance for account, balance in reports.balances(actor.business_id, end=as_at)
    }
    for reply in replies:
        balance = balances.get(reply.id)
        if balance is None:
            continue
        reply.balance = balance
        reply.presented_side = _side_for(reply.kind, balance)
    return replies


def _side_for(kind: str, balance: object) -> str:
    from decimal import Decimal

    from nickland.domain.ledger import AccountKind, presentation_side

    return presentation_side(AccountKind(kind), Decimal(str(balance))).value


@router.post("/accounts", response_model=AccountReply, status_code=201, summary="Create an account")
def create_account(
    contract: CreateAccountContract, actor: ActorDependency, accounts: AccountDependency
) -> AccountReply:
    return AccountReply.model_validate(accounts.create(actor, **contract.model_dump()))


@router.get("/accounts/{account_id}", response_model=AccountReply, summary="Read an account")
def read_account(
    account_id: uuid.UUID, actor: ActorDependency, accounts: AccountDependency
) -> AccountReply:
    return AccountReply.model_validate(accounts.require(actor, account_id))


@router.patch("/accounts/{account_id}", response_model=AccountReply, summary="Amend an account")
def amend_account(
    account_id: uuid.UUID,
    contract: AmendAccountContract,
    actor: ActorDependency,
    accounts: AccountDependency,
) -> AccountReply:
    return AccountReply.model_validate(accounts.amend(actor, account_id, contract.changes()))


# -- the accounting calendar -----------------------------------------------------------


@router.get("/periods", response_model=list[PeriodReply], summary="List accounting periods")
def list_periods(
    actor: ActorDependency, accounts: AccountDependency, financial_year: int | None = None
) -> list[PeriodReply]:
    return [
        PeriodReply.model_validate(period)
        for period in accounts.periods(actor, financial_year=financial_year)
    ]


@router.post(
    "/periods",
    response_model=list[PeriodReply],
    status_code=201,
    summary="Open a financial year",
)
def open_year(
    contract: OpenYearContract, actor: ActorDependency, accounts: AccountDependency
) -> list[PeriodReply]:
    actor.require("period.close")
    accounts.open_financial_year(_business(actor), contract.financial_year, contract.start_month)
    return [
        PeriodReply.model_validate(period)
        for period in accounts.periods(actor, financial_year=contract.financial_year)
    ]


@router.get(
    "/periods/{period_id}/readiness",
    response_model=ReadinessReply,
    summary="Check whether a period may be closed",
)
def period_readiness(
    period_id: uuid.UUID, actor: ActorDependency, periods: PeriodDependency
) -> ReadinessReply:
    readiness = periods.readiness(actor, period_id)
    return ReadinessReply(
        period=PeriodReply.model_validate(readiness.period),
        checks=[CloseCheckReply.model_validate(check) for check in readiness.checks],
        ready=readiness.ready,
    )


@router.post("/periods/{period_id}/close", response_model=ReadinessReply, summary="Close a period")
def close_period(
    period_id: uuid.UUID,
    contract: ClosePeriodContract,
    actor: ActorDependency,
    periods: PeriodDependency,
) -> ReadinessReply:
    readiness = periods.close(actor, period_id, note=contract.note)
    return ReadinessReply(
        period=PeriodReply.model_validate(readiness.period),
        checks=[CloseCheckReply.model_validate(check) for check in readiness.checks],
        ready=readiness.ready,
    )


@router.post(
    "/periods/{period_id}/reopen", response_model=PeriodReply, summary="Reopen a closed period"
)
def reopen_period(
    period_id: uuid.UUID,
    contract: ReopenPeriodContract,
    actor: ActorDependency,
    periods: PeriodDependency,
) -> PeriodReply:
    return PeriodReply.model_validate(
        periods.reopen(actor, period_id, contract.reason, approved_by_id=contract.approved_by_id)
    )


@router.post("/year-end", response_model=YearEndReply, summary="Close a financial year")
def close_year(
    contract: CloseYearContract, actor: ActorDependency, periods: PeriodDependency
) -> YearEndReply:
    outcome = periods.close_year(actor, contract.financial_year, note=contract.note)
    return YearEndReply(
        period=PeriodReply.model_validate(outcome.period),
        entry_number=outcome.entry.number if outcome.entry else None,
        accounts_closed=outcome.accounts_closed,
        net_result=outcome.net_result,
    )


# -- reconciliation --------------------------------------------------------------------


@router.get(
    "/reconciliation/subledgers",
    response_model=list[SubledgerVarianceReply],
    summary="Compare control accounts with their subledgers",
)
def subledger_reconciliation(
    actor: ActorDependency, reports: LedgerReportDependency
) -> list[SubledgerVarianceReply]:
    return [
        SubledgerVarianceReply(
            account_code=variance.account_code,
            account_name=variance.account_name,
            subledger=variance.subledger,
            control_balance=variance.control_balance,
            subledger_balance=variance.subledger_balance,
            difference=variance.difference,
            agrees=variance.agrees,
        )
        for variance in reports.subledger_variances(actor)
    ]


def _business(actor: ActorDependency) -> uuid.UUID:
    from nickland.domain.errors import ConflictError

    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id


__all__ = ["router"]
