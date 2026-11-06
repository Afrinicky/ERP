"""Journal endpoints: posting, approval, reversal and the register."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter

from nickland.application.services.journals import JournalDraft
from nickland.domain.ledger import EntryLine, Side, SubledgerKind
from nickland.interface.dependencies import ActorDependency, JournalDependency
from nickland.interface.ledger_schemas import (
    ApprovalReply,
    ApproveContract,
    JournalEntryReply,
    JournalLineContract,
    JournalPageReply,
    PostingOutcomeReply,
    PostJournalContract,
    RejectContract,
    ReverseContract,
    SetThresholdContract,
    ThresholdReply,
)

router = APIRouter(prefix="/journals", tags=["journals"])


def _entry(entry: object) -> JournalEntryReply:
    """Present an entry with each line's account code and name alongside.

    The register is unreadable without them, and the account is already loaded on the
    line, so naming it here costs nothing and saves the interface a second request.
    """
    reply = JournalEntryReply.model_validate(entry)
    for line, source in zip(reply.lines, entry.lines, strict=True):  # type: ignore[attr-defined]
        line.account_code = source.account.code
        line.account_name = source.account.name
    return reply


def _line(contract: JournalLineContract) -> EntryLine:
    return EntryLine(
        account_code=contract.account_code,
        side=Side(contract.side),
        amount=contract.amount,
        narration=contract.narration,
        party_id=contract.party_id,
        subledger=SubledgerKind(contract.subledger) if contract.subledger else None,
        branch_id=contract.branch_id,
    )


@router.get("", response_model=JournalPageReply, summary="The journal register")
def list_entries(
    actor: ActorDependency,
    journals: JournalDependency,
    term: str | None = None,
    state: str | None = None,
    source: str | None = None,
    account_id: uuid.UUID | None = None,
    party_id: uuid.UUID | None = None,
    start: date | None = None,
    end: date | None = None,
    offset: int = 0,
    limit: int = 50,
) -> JournalPageReply:
    page = journals.entries(
        actor,
        term=term,
        state=state,
        source=source,
        account_id=account_id,
        party_id=party_id,
        start=start,
        end=end,
        offset=offset,
        limit=limit,
    )
    return JournalPageReply(
        entries=[_entry(entry) for entry in page.entries],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


@router.post(
    "", response_model=PostingOutcomeReply, status_code=201, summary="Post a journal entry"
)
def post_entry(
    contract: PostJournalContract, actor: ActorDependency, journals: JournalDependency
) -> PostingOutcomeReply:
    outcome = journals.post(
        actor,
        JournalDraft(
            posting_date=contract.posting_date,
            narration=contract.narration,
            lines=[_line(line) for line in contract.lines],
            source=contract.source,
            source_reference=contract.source_reference,
            idempotency_key=contract.idempotency_key,
            currency_code=contract.currency_code,
            attachment_ids=contract.attachment_ids,
            branch_id=contract.branch_id,
        ),
    )
    return PostingOutcomeReply(
        entry=_entry(outcome.entry),
        created=outcome.created,
        awaiting_approval=outcome.awaiting_approval,
    )


@router.get(
    "/pending", response_model=list[JournalEntryReply], summary="Entries awaiting a checker"
)
def pending(actor: ActorDependency, journals: JournalDependency) -> list[JournalEntryReply]:
    return [_entry(entry) for entry in journals.pending(actor)]


@router.get("/threshold", response_model=ThresholdReply | None, summary="The approval threshold")
def read_threshold(actor: ActorDependency, journals: JournalDependency) -> ThresholdReply | None:
    threshold = journals.threshold(actor)
    return ThresholdReply.model_validate(threshold) if threshold else None


@router.put(
    "/threshold", response_model=ThresholdReply | None, summary="Set the approval threshold"
)
def set_threshold(
    contract: SetThresholdContract, actor: ActorDependency, journals: JournalDependency
) -> ThresholdReply | None:
    threshold = journals.set_threshold(
        actor,
        contract.amount,
        requires_distinct_checker=contract.requires_distinct_checker,
    )
    return ThresholdReply.model_validate(threshold) if threshold else None


@router.get("/{entry_id}", response_model=JournalEntryReply, summary="Read a journal entry")
def read_entry(
    entry_id: uuid.UUID, actor: ActorDependency, journals: JournalDependency
) -> JournalEntryReply:
    return _entry(journals.require(actor, entry_id))


@router.get(
    "/{entry_id}/approvals",
    response_model=list[ApprovalReply],
    summary="Decisions taken on an entry",
)
def entry_approvals(
    entry_id: uuid.UUID, actor: ActorDependency, journals: JournalDependency
) -> list[ApprovalReply]:
    return [
        ApprovalReply.model_validate(approval) for approval in journals.approvals(actor, entry_id)
    ]


@router.post(
    "/{entry_id}/approve", response_model=JournalEntryReply, summary="Approve a pending entry"
)
def approve_entry(
    entry_id: uuid.UUID,
    contract: ApproveContract,
    actor: ActorDependency,
    journals: JournalDependency,
) -> JournalEntryReply:
    return _entry(journals.approve(actor, entry_id, note=contract.note))


@router.post(
    "/{entry_id}/reject", response_model=JournalEntryReply, summary="Decline a pending entry"
)
def reject_entry(
    entry_id: uuid.UUID,
    contract: RejectContract,
    actor: ActorDependency,
    journals: JournalDependency,
) -> JournalEntryReply:
    return _entry(journals.reject(actor, entry_id, contract.reason))


@router.post(
    "/{entry_id}/reverse",
    response_model=JournalEntryReply,
    status_code=201,
    summary="Reverse a posted entry",
)
def reverse_entry(
    entry_id: uuid.UUID,
    contract: ReverseContract,
    actor: ActorDependency,
    journals: JournalDependency,
) -> JournalEntryReply:
    return _entry(journals.reverse(actor, entry_id, contract.reason, on=contract.posting_date))


__all__ = ["router"]
