"""Reconciliation endpoints: statements, matching, the unmatched queue, every source."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter

from nickland.application.services.reconciliation import LedgerItem, StatementPosition
from nickland.interface.dependencies import (
    ActorDependency,
    ReconciliationDependency,
    SessionDependency,
)
from nickland.interface.finance_replies import Names
from nickland.interface.finance_schemas import (
    AutoMatchReply,
    ImportStatementContract,
    LedgerItemReply,
    MatchContract,
    PostFromLineContract,
    ReasonContract,
    SourceRowReply,
    StatementLineReply,
    StatementReply,
    SuggestionReply,
)

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


def _statement(
    db: SessionDependency, position: StatementPosition, *, lines: bool
) -> StatementReply:
    statement = position.statement
    return StatementReply(
        id=statement.id,
        account_id=statement.financial_account_fk,
        account=Names(db).account(statement.financial_account_fk),
        reference=statement.reference,
        starts_on=statement.starts_on,
        ends_on=statement.ends_on,
        closing_balance=statement.closing_balance,
        ledger_balance=position.ledger_balance,
        difference=position.difference,
        unmatched=position.unmatched,
        duplicates=sum(1 for line in statement.lines if line.state == "duplicate"),
        state=statement.state,
        reconciled=position.reconciled,
        lines=[StatementLineReply.model_validate(line) for line in statement.lines]
        if lines
        else [],
    )


def _item(item: LedgerItem) -> LedgerItemReply:
    return LedgerItemReply(
        journal_line_id=item.line.id,
        entry_number=item.entry.number,
        posting_date=item.line.posting_date,
        narration=item.line.narration or item.entry.narration,
        reference=item.entry.source_reference,
        amount=item.amount,
    )


@router.get("/statements", response_model=list[StatementReply], summary="Statements")
def statements(
    actor: ActorDependency,
    recon: ReconciliationDependency,
    db: SessionDependency,
    account_id: uuid.UUID | None = None,
) -> list[StatementReply]:
    return [
        _statement(db, recon.position(actor, item.id), lines=False)
        for item in recon.statements(actor, financial_account_id=account_id)
    ]


@router.post(
    "/statements", response_model=StatementReply, status_code=201, summary="Import a statement"
)
def import_statement(
    body: ImportStatementContract,
    actor: ActorDependency,
    recon: ReconciliationDependency,
    db: SessionDependency,
) -> StatementReply:
    statement = recon.import_statement(
        actor,
        body.financial_account_id,
        content=body.content,
        closing_balance=body.closing_balance,
        reference=body.reference,
    )
    return _statement(db, recon.position(actor, statement.id), lines=True)


@router.get("/statements/{statement_id}", response_model=StatementReply, summary="A statement")
def statement(
    statement_id: uuid.UUID,
    actor: ActorDependency,
    recon: ReconciliationDependency,
    db: SessionDependency,
) -> StatementReply:
    return _statement(db, recon.position(actor, statement_id), lines=True)


@router.post(
    "/statements/{statement_id}/auto-match", response_model=AutoMatchReply, summary="Match"
)
def auto_match(
    statement_id: uuid.UUID, actor: ActorDependency, recon: ReconciliationDependency
) -> AutoMatchReply:
    return AutoMatchReply(matched=recon.auto_match(actor, statement_id))


@router.post(
    "/statements/{statement_id}/complete", response_model=StatementReply, summary="Reconcile"
)
def complete(
    statement_id: uuid.UUID,
    actor: ActorDependency,
    recon: ReconciliationDependency,
    db: SessionDependency,
) -> StatementReply:
    return _statement(db, recon.complete(actor, statement_id), lines=True)


@router.get(
    "/lines/{line_id}/suggestions", response_model=list[SuggestionReply], summary="Suggestions"
)
def suggestions(
    line_id: uuid.UUID, actor: ActorDependency, recon: ReconciliationDependency
) -> list[SuggestionReply]:
    return [
        SuggestionReply(item=_item(item.item), score=item.score)
        for item in recon.suggestions(actor, line_id)
    ]


@router.post("/lines/{line_id}/match", response_model=StatementLineReply, summary="Match a line")
def match(
    line_id: uuid.UUID, body: MatchContract, actor: ActorDependency, recon: ReconciliationDependency
) -> StatementLineReply:
    return StatementLineReply.model_validate(recon.match(actor, line_id, body.journal_line_ids))


@router.post("/lines/{line_id}/unmatch", response_model=StatementLineReply, summary="Unmatch")
def unmatch(
    line_id: uuid.UUID, actor: ActorDependency, recon: ReconciliationDependency
) -> StatementLineReply:
    return StatementLineReply.model_validate(recon.unmatch(actor, line_id))


@router.post("/lines/{line_id}/exclude", response_model=StatementLineReply, summary="Set aside")
def exclude(
    line_id: uuid.UUID,
    body: ReasonContract,
    actor: ActorDependency,
    recon: ReconciliationDependency,
) -> StatementLineReply:
    return StatementLineReply.model_validate(recon.exclude(actor, line_id, reason=body.reason))


@router.post("/lines/{line_id}/post", response_model=StatementLineReply, summary="Post and match")
def post_from_line(
    line_id: uuid.UUID,
    body: PostFromLineContract,
    actor: ActorDependency,
    recon: ReconciliationDependency,
) -> StatementLineReply:
    return StatementLineReply.model_validate(
        recon.post_from_line(actor, line_id, kind=body.kind, reason=body.reason)
    )


@router.get(
    "/accounts/{account_id}/unreconciled",
    response_model=list[LedgerItemReply],
    summary="Postings not yet matched",
)
def unreconciled(
    account_id: uuid.UUID, actor: ActorDependency, recon: ReconciliationDependency
) -> list[LedgerItemReply]:
    return [_item(item) for item in recon.unreconciled(actor, account_id)]


@router.get("/sources", response_model=list[SourceRowReply], summary="Every source")
def sources(
    actor: ActorDependency, recon: ReconciliationDependency, start: date, end: date
) -> list[SourceRowReply]:
    return [
        SourceRowReply.model_validate(row) for row in recon.sources(actor, start=start, end=end)
    ]
