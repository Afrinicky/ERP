"""Expense endpoints: record, approve, reject, settle; the threshold; recurring expenses."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from nickland.application.services.expenses import ExpenseDraft, RecurringDraft
from nickland.interface.dependencies import ActorDependency, ExpenseDependency, SessionDependency
from nickland.interface.finance_replies import Names, expense
from nickland.interface.finance_schemas import (
    ExpenseContract,
    ExpenseReply,
    ReasonContract,
    RecurringContract,
    RecurringReply,
    SettleContract,
    ThresholdContract,
    ThresholdReply,
)

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.get("", response_model=list[ExpenseReply], summary="Expenses")
def expenses(
    actor: ActorDependency,
    service: ExpenseDependency,
    db: SessionDependency,
    state: str | None = None,
) -> list[ExpenseReply]:
    names = Names(db)
    return [expense(names, item) for item in service.expenses(actor, state=state)]


@router.post("", response_model=ExpenseReply, status_code=201, summary="Record an expense")
def record(
    body: ExpenseContract, actor: ActorDependency, service: ExpenseDependency, db: SessionDependency
) -> ExpenseReply:
    return expense(Names(db), service.record(actor, ExpenseDraft(**body.model_dump())))


@router.post("/{expense_id}/approve", response_model=ExpenseReply, summary="Approve")
def approve(
    expense_id: uuid.UUID, actor: ActorDependency, service: ExpenseDependency, db: SessionDependency
) -> ExpenseReply:
    return expense(Names(db), service.approve(actor, expense_id))


@router.post("/{expense_id}/reject", response_model=ExpenseReply, summary="Reject")
def reject(
    expense_id: uuid.UUID,
    body: ReasonContract,
    actor: ActorDependency,
    service: ExpenseDependency,
    db: SessionDependency,
) -> ExpenseReply:
    return expense(Names(db), service.reject(actor, expense_id, reason=body.reason))


@router.post("/{expense_id}/settle", response_model=ExpenseReply, summary="Pay what is owed")
def settle(
    expense_id: uuid.UUID,
    body: SettleContract,
    actor: ActorDependency,
    service: ExpenseDependency,
    db: SessionDependency,
) -> ExpenseReply:
    made = service.settle(
        actor,
        expense_id,
        financial_account_id=body.financial_account_id,
        settled_on=body.settled_on,
    )
    return expense(Names(db), made)


@router.get("/threshold", response_model=ThresholdReply, summary="The approval threshold")
def threshold(actor: ActorDependency, service: ExpenseDependency) -> ThresholdReply:
    actor.require_any("expense.manage", "expense.approve")
    assert actor.business_id is not None
    return ThresholdReply(amount=service.threshold(actor.business_id))


@router.put("/threshold", response_model=ThresholdReply, summary="Set the approval threshold")
def set_threshold(
    body: ThresholdContract, actor: ActorDependency, service: ExpenseDependency
) -> ThresholdReply:
    return ThresholdReply(amount=service.set_threshold(actor, body.amount))


@router.get("/recurring", response_model=list[RecurringReply], summary="Recurring expenses")
def recurring(actor: ActorDependency, service: ExpenseDependency) -> list[RecurringReply]:
    return [RecurringReply.model_validate(item) for item in service.recurring(actor)]


@router.post(
    "/recurring", response_model=RecurringReply, status_code=201, summary="Schedule an expense"
)
def schedule(
    body: RecurringContract, actor: ActorDependency, service: ExpenseDependency
) -> RecurringReply:
    return RecurringReply.model_validate(
        service.schedule(actor, RecurringDraft(**body.model_dump()))
    )


@router.post("/recurring/raise", response_model=list[ExpenseReply], summary="Raise what is due")
def raise_due(
    actor: ActorDependency, service: ExpenseDependency, db: SessionDependency
) -> list[ExpenseReply]:
    names = Names(db)
    return [expense(names, item) for item in service.raise_due(actor)]
