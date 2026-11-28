"""Treasury endpoints: financial accounts, movements between them, the cashbook."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter

from nickland.application.services.treasury import FinancialAccountDraft, MovementDraft
from nickland.interface.dependencies import ActorDependency, SessionDependency, TreasuryDependency
from nickland.interface.finance_replies import Names, financial_account, movement
from nickland.interface.finance_schemas import (
    AmendFinancialAccountContract,
    CashbookLineReply,
    CashbookReply,
    FinancialAccountContract,
    FinancialAccountReply,
    MovementContract,
    MovementReply,
)

router = APIRouter(prefix="/treasury", tags=["treasury"])


def _position(actor: ActorDependency, treasury: TreasuryDependency, account_id: uuid.UUID):  # type: ignore[no-untyped-def]
    for item in treasury.accounts(actor, include_inactive=True):
        if item.account.id == account_id:
            return item
    raise AssertionError("account listed")


@router.get("/accounts", response_model=list[FinancialAccountReply], summary="Financial accounts")
def accounts(
    actor: ActorDependency, treasury: TreasuryDependency, include_inactive: bool = False
) -> list[FinancialAccountReply]:
    return [
        financial_account(item)
        for item in treasury.accounts(actor, include_inactive=include_inactive)
    ]


@router.post(
    "/accounts", response_model=FinancialAccountReply, status_code=201, summary="Add an account"
)
def create_account(
    body: FinancialAccountContract, actor: ActorDependency, treasury: TreasuryDependency
) -> FinancialAccountReply:
    row = treasury.create_account(actor, FinancialAccountDraft(**body.model_dump()))
    return financial_account(_position(actor, treasury, row.id))


@router.patch(
    "/accounts/{account_id}", response_model=FinancialAccountReply, summary="Amend an account"
)
def amend_account(
    account_id: uuid.UUID,
    body: AmendFinancialAccountContract,
    actor: ActorDependency,
    treasury: TreasuryDependency,
) -> FinancialAccountReply:
    treasury.amend_account(actor, account_id, **body.model_dump(exclude_unset=True))
    return financial_account(_position(actor, treasury, account_id))


@router.get(
    "/accounts/{account_id}/cashbook", response_model=CashbookReply, summary="Cashbook or bank book"
)
def cashbook(
    account_id: uuid.UUID,
    actor: ActorDependency,
    treasury: TreasuryDependency,
    start: date | None = None,
    end: date | None = None,
) -> CashbookReply:
    _, ledger = treasury.cashbook(actor, account_id, start=start, end=end)
    return CashbookReply(
        account=financial_account(_position(actor, treasury, account_id)),
        starts_on=ledger.starts_on,
        ends_on=ledger.ends_on,
        opening_balance=ledger.opening_balance,
        closing_balance=ledger.closing_balance,
        total_in=ledger.total_debit,
        total_out=ledger.total_credit,
        lines=[
            CashbookLineReply(
                entry_id=item.entry_id,
                number=item.number,
                posting_date=item.posting_date,
                narration=item.line_narration or item.narration,
                source=str(item.source),
                debit=item.debit,
                credit=item.credit,
                running_balance=item.running_balance,
            )
            for item in ledger.movements
        ],
    )


@router.get("/movements", response_model=list[MovementReply], summary="Treasury movements")
def movements(
    actor: ActorDependency, treasury: TreasuryDependency, db: SessionDependency
) -> list[MovementReply]:
    names = Names(db)
    return [movement(names, item) for item in treasury.movements(actor)]


@router.post(
    "/movements",
    response_model=MovementReply,
    status_code=201,
    summary="Transfer, charge or interest",
)
def record_movement(
    body: MovementContract,
    actor: ActorDependency,
    treasury: TreasuryDependency,
    db: SessionDependency,
) -> MovementReply:
    return movement(Names(db), treasury.record(actor, MovementDraft(**body.model_dump())))
