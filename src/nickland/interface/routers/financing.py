"""Debt, loans and financing endpoints. PRD §25.

Agreements and their schedules, every money movement against them, revisions that are
proposed and decided, security, and the registers of what is owed in each direction.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter

from nickland.application.context import AnyActor
from nickland.application.services.debts import DebtLine, OtherDebtDraft
from nickland.application.services.loans import (
    LoanDraft,
    LoanService,
    RevisionDraft,
    TransactionKind,
)
from nickland.domain.financing import (
    AgreementState,
    Component,
    CustomRow,
    InstalmentState,
    InterestModel,
    LoanDirection,
    LoanType,
    RepaymentFrequency,
    RevisionKind,
    ScheduledInstalment,
    SecurityKind,
    instalment_state,
    summarise,
)
from nickland.domain.money import ZERO
from nickland.interface.dependencies import (
    ActorDependency,
    DebtDependency,
    LoanDependency,
    SessionDependency,
)
from nickland.interface.finance_replies import Names
from nickland.interface.financing_schemas import (
    AccrueContract,
    AccrueReply,
    ApprovalContract,
    CashClassReply,
    CommitmentReply,
    CounterpartyReply,
    DebtLineReply,
    DebtSummaryReply,
    DecisionContract,
    DisputeContract,
    LoanBookReply,
    LoanContract,
    LoanDetailReply,
    LoanPositionReply,
    LoanSummaryReply,
    LoanThresholdContract,
    LoanThresholdReply,
    MaturityReply,
    MovementContract,
    OtherDebtContract,
    OtherDebtReply,
    OtherSettlementContract,
    PenaltyContract,
    PreviewReply,
    QuoteReply,
    RefinanceContract,
    RevisionContract,
    ScheduleRowReply,
    ScheduleSummaryReply,
    SecurityContract,
    SecurityReply,
    SettleContract,
    SplitReply,
    TransactionReply,
    TrendReply,
    VersionReply,
    WriteOffContract,
)
from nickland.persistence.financing_models import (
    LoanAgreement,
    LoanAllocation,
    LoanInstalment,
    LoanSchedule,
    LoanSecurity,
    LoanTransaction,
    OtherDebt,
)

router = APIRouter(prefix="/financing", tags=["financing"])


# -- agreements ------------------------------------------------------------------------------


def _draft(body: LoanContract) -> LoanDraft:
    values = body.model_dump(exclude={"custom_rows"})
    return LoanDraft(
        **values,
        custom_rows=[
            CustomRow(
                due_on=row.due_on, principal=row.principal, interest=row.interest, fees=row.fees
            )
            for row in body.custom_rows
        ],
    )


@router.get("/loans", response_model=list[LoanSummaryReply], summary="Agreements")
def agreements(
    actor: ActorDependency,
    service: LoanDependency,
    direction: str | None = None,
    state: str | None = None,
) -> list[LoanSummaryReply]:
    return [
        _summary(service, item)
        for item in service.agreements(actor, direction=direction, state=state)
    ]


@router.post("/loans/preview", response_model=PreviewReply, summary="Preview a schedule")
def preview(body: LoanContract, actor: ActorDependency, service: LoanDependency) -> PreviewReply:
    rows = service.preview(actor, _draft(body))
    return PreviewReply(rows=[_planned(row) for row in rows], summary=_totals(rows))


@router.post("/loans", response_model=LoanDetailReply, status_code=201, summary="Record")
def create(
    body: LoanContract, actor: ActorDependency, service: LoanDependency, db: SessionDependency
) -> LoanDetailReply:
    return _detail(service, Names(db), actor, service.create(actor, _draft(body)))


@router.get("/loans/{agreement_id}", response_model=LoanDetailReply, summary="Agreement")
def detail(
    agreement_id: uuid.UUID,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    return _detail(service, Names(db), actor, service.require(actor, agreement_id))


@router.put("/loans/{agreement_id}", response_model=LoanDetailReply, summary="Amend a draft")
def amend(
    agreement_id: uuid.UUID,
    body: LoanContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    return _detail(service, Names(db), actor, service.amend(actor, agreement_id, _draft(body)))


@router.post("/loans/{agreement_id}/submit", response_model=LoanDetailReply, summary="Submit")
def submit(
    agreement_id: uuid.UUID,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    return _detail(service, Names(db), actor, service.submit(actor, agreement_id))


@router.post("/loans/{agreement_id}/approve", response_model=LoanDetailReply, summary="Approve")
def approve(
    agreement_id: uuid.UUID,
    body: ApprovalContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    made = service.approve(actor, agreement_id, note=body.note, attachment_id=body.attachment_id)
    return _detail(service, Names(db), actor, made)


@router.post("/loans/{agreement_id}/reject", response_model=LoanDetailReply, summary="Reject")
def reject(
    agreement_id: uuid.UUID,
    body: WriteOffContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    return _detail(
        service, Names(db), actor, service.reject(actor, agreement_id, reason=body.reason)
    )


@router.post("/loans/{agreement_id}/drawdowns", response_model=LoanDetailReply, summary="Drawdown")
def drawdown(
    agreement_id: uuid.UUID,
    body: MovementContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    service.drawdown(
        actor,
        agreement_id,
        amount=body.amount,
        on=body.on,
        financial_account_id=body.financial_account_id,
        fee=body.fee,
        reference=body.reference,
        evidence_attachment_id=body.evidence_attachment_id,
    )
    return _detail(service, Names(db), actor, service.require(actor, agreement_id))


@router.post(
    "/loans/{agreement_id}/repayments", response_model=LoanDetailReply, summary="Repayment"
)
def repay(
    agreement_id: uuid.UUID,
    body: MovementContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    service.repay(
        actor,
        agreement_id,
        amount=body.amount,
        on=body.on,
        financial_account_id=body.financial_account_id,
        reference=body.reference,
        evidence_attachment_id=body.evidence_attachment_id,
    )
    return _detail(service, Names(db), actor, service.require(actor, agreement_id))


@router.get("/loans/{agreement_id}/quote", response_model=QuoteReply, summary="Settlement quote")
def quote(
    agreement_id: uuid.UUID,
    actor: ActorDependency,
    service: LoanDependency,
    on: date | None = None,
) -> QuoteReply:
    made = service.quote(actor, agreement_id, on)
    return QuoteReply(
        on=made.on,
        principal=made.principal,
        interest_due=made.interest_due,
        interest_accrued=made.interest_accrued,
        fees_due=made.fees_due,
        penalties_due=made.penalties_due,
        early_settlement_fee=made.early_settlement_fee,
        total=made.total,
    )


@router.post("/loans/{agreement_id}/settle", response_model=LoanDetailReply, summary="Settle")
def settle(
    agreement_id: uuid.UUID,
    body: SettleContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    service.settle(
        actor,
        agreement_id,
        on=body.on,
        financial_account_id=body.financial_account_id,
        reference=body.reference,
        evidence_attachment_id=body.evidence_attachment_id,
    )
    return _detail(service, Names(db), actor, service.require(actor, agreement_id))


@router.post("/loans/{agreement_id}/refinance", response_model=LoanDetailReply, summary="Refinance")
def refinance(
    agreement_id: uuid.UUID,
    body: RefinanceContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    service.refinance(actor, agreement_id, replacement_id=body.replacement_id, on=body.on)
    return _detail(service, Names(db), actor, service.require(actor, agreement_id))


@router.post("/loans/{agreement_id}/write-off", response_model=LoanDetailReply, summary="Write off")
def write_off(
    agreement_id: uuid.UUID,
    body: WriteOffContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    service.write_off(actor, agreement_id, reason=body.reason, on=body.on)
    return _detail(service, Names(db), actor, service.require(actor, agreement_id))


@router.post(
    "/loans/{agreement_id}/penalty", response_model=LoanDetailReply, summary="Charge a penalty"
)
def penalty(
    agreement_id: uuid.UUID,
    body: PenaltyContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    service.charge_penalty(actor, agreement_id, reason=body.reason, on=body.on, amount=body.amount)
    return _detail(service, Names(db), actor, service.require(actor, agreement_id))


@router.post(
    "/loans/{agreement_id}/revisions", response_model=LoanDetailReply, summary="Propose a revision"
)
def propose(
    agreement_id: uuid.UUID,
    body: RevisionContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    service.propose_revision(actor, agreement_id, RevisionDraft(**body.model_dump()))
    return _detail(service, Names(db), actor, service.require(actor, agreement_id))


@router.post(
    "/loans/{agreement_id}/revisions/decision",
    response_model=LoanDetailReply,
    summary="Approve or decline the revision",
)
def decide(
    agreement_id: uuid.UUID,
    body: DecisionContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    service.decide_revision(actor, agreement_id, approve=body.approve, note=body.note)
    return _detail(service, Names(db), actor, service.require(actor, agreement_id))


@router.post(
    "/loans/{agreement_id}/securities", response_model=LoanDetailReply, summary="Add security"
)
def add_security(
    agreement_id: uuid.UUID,
    body: SecurityContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    service.add_security(actor, agreement_id, **body.model_dump())
    return _detail(service, Names(db), actor, service.require(actor, agreement_id))


@router.post(
    "/securities/{security_id}/release", response_model=SecurityReply, summary="Release security"
)
def release_security(
    security_id: uuid.UUID, actor: ActorDependency, service: LoanDependency
) -> SecurityReply:
    return _security(service.release_security(actor, security_id))


@router.post(
    "/loans/{agreement_id}/dispute", response_model=LoanDetailReply, summary="Raise or resolve"
)
def dispute(
    agreement_id: uuid.UUID,
    body: DisputeContract,
    actor: ActorDependency,
    service: LoanDependency,
    db: SessionDependency,
) -> LoanDetailReply:
    made = service.set_disputed(actor, agreement_id, disputed=body.disputed, note=body.note)
    return _detail(service, Names(db), actor, made)


@router.post("/accrue", response_model=AccrueReply, summary="Recognise what has fallen due")
def accrue(body: AccrueContract, actor: ActorDependency, service: LoanDependency) -> AccrueReply:
    return AccrueReply(accrued=service.accrue(actor, as_of=body.as_of))


@router.get("/threshold", response_model=LoanThresholdReply, summary="The approval threshold")
def threshold(actor: ActorDependency, service: LoanDependency) -> LoanThresholdReply:
    actor.require_any("loan.view", "loan.manage")
    assert actor.business_id is not None
    return LoanThresholdReply(amount=service.threshold(actor.business_id))


@router.put("/threshold", response_model=LoanThresholdReply, summary="Set the threshold")
def set_threshold(
    body: LoanThresholdContract, actor: ActorDependency, service: LoanDependency
) -> LoanThresholdReply:
    return LoanThresholdReply(amount=service.set_threshold(actor, body.amount))


# -- the registers ---------------------------------------------------------------------------


@router.get("/register", response_model=list[DebtLineReply], summary="A debt register")
def register(
    actor: ActorDependency,
    service: DebtDependency,
    db: SessionDependency,
    side: str = "receivable",
    kind: str | None = None,
    standing: str | None = None,
    counterparty: str | None = None,
    branch_id: uuid.UUID | None = None,
    responsible_id: uuid.UUID | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    include_settled: bool = False,
) -> list[DebtLineReply]:
    names = Names(db)
    lines = service.register(
        actor,
        owed_to_us=side != "payable",
        kind=kind,
        standing=standing,
        counterparty=counterparty,
        branch_id=branch_id,
        responsible_id=responsible_id,
        due_from=due_from,
        due_to=due_to,
        include_settled=include_settled,
    )
    return [_line(names, line) for line in lines]


@router.get("/summary", response_model=DebtSummaryReply, summary="The debt dashboard")
def summary(
    actor: ActorDependency,
    service: DebtDependency,
    window_days: int = 30,
    start: date | None = None,
    end: date | None = None,
) -> DebtSummaryReply:
    made = service.summary(actor, window_days=window_days, start=start, end=end)

    def splits(rows: list) -> list[SplitReply]:  # type: ignore[type-arg]
        return [
            SplitReply(code=r.code, label=r.label, amount=r.amount, count=r.count) for r in rows
        ]

    def people(rows: list) -> list[CounterpartyReply]:  # type: ignore[type-arg]
        return [CounterpartyReply(name=r.name, amount=r.amount, count=r.count) for r in rows]

    def book(row) -> LoanBookReply:  # type: ignore[no-untyped-def]
        return LoanBookReply(
            principal=row.principal, charges=row.charges, total=row.total, count=row.count
        )

    return DebtSummaryReply(
        on=made.on,
        receivables=made.receivables,
        receivables_by_kind=splits(made.receivables_by_kind),
        payables=made.payables,
        payables_by_kind=splits(made.payables_by_kind),
        loans_receivable=book(made.loans_receivable),
        loans_payable=book(made.loans_payable),
        receivable_due_soon=made.receivable_due_soon,
        receivable_overdue=made.receivable_overdue,
        payable_due_soon=made.payable_due_soon,
        payable_overdue=made.payable_overdue,
        collected=made.collected,
        repaid=made.repaid,
        receivable_ageing=splits(made.receivable_ageing),
        payable_ageing=splits(made.payable_ageing),
        largest_debtors=people(made.largest_debtors),
        largest_creditors=people(made.largest_creditors),
        debt_service=made.debt_service,
        cash_available=made.cash_available,
        coverage=made.coverage,
        window_days=made.window_days,
        period_start=made.period_start,
        period_end=made.period_end,
        trend=[
            TrendReply(
                month=point.month,
                collected=point.collected,
                settled=point.settled,
                borrowed=point.borrowed,
                lent=point.lent,
            )
            for point in made.trend
        ],
    )


@router.get("/calendar", response_model=list[CommitmentReply], summary="Money expected in and out")
def calendar(
    actor: ActorDependency, service: DebtDependency, days: int = 90
) -> list[CommitmentReply]:
    return [
        CommitmentReply(
            due_on=line.due_on,
            inward=line.inward,
            kind=line.kind.value,
            kind_label=line.kind.label,
            reference=line.reference,
            counterparty=line.counterparty,
            amount=line.amount,
            source_type=line.source_type,
            source_id=line.source_id,
        )
        for line in service.calendar(actor, days=max(1, min(days, 730)))
    ]


@router.get("/maturity", response_model=list[MaturityReply], summary="Maturity profile")
def maturity(actor: ActorDependency, service: DebtDependency) -> list[MaturityReply]:
    return [
        MaturityReply(code=row.code, label=row.label, borrowed=row.borrowed, lent=row.lent)
        for row in service.maturity(actor)
    ]


@router.get("/cash-flow", response_model=list[CashClassReply], summary="Cash by class")
def cash_flow(
    actor: ActorDependency, service: DebtDependency, start: date, end: date
) -> list[CashClassReply]:
    return [
        CashClassReply(
            code=row.code,
            label=row.label,
            category=row.category,
            inflow=row.inflow,
            outflow=row.outflow,
            net=row.net,
        )
        for row in service.cash_flow(actor, start=start, end=end)
    ]


@router.post(
    "/other-debts", response_model=OtherDebtReply, status_code=201, summary="Record an other debt"
)
def record_other(
    body: OtherDebtContract, actor: ActorDependency, service: DebtDependency
) -> OtherDebtReply:
    return _other(service.record_other(actor, OtherDebtDraft(**body.model_dump())))


@router.post("/other-debts/{debt_id}/settlements", response_model=OtherDebtReply, summary="Settle")
def settle_other(
    debt_id: uuid.UUID,
    body: OtherSettlementContract,
    actor: ActorDependency,
    service: DebtDependency,
) -> OtherDebtReply:
    service.settle_other(
        actor,
        debt_id,
        amount=body.amount,
        financial_account_id=body.financial_account_id,
        on=body.on,
        reference=body.reference,
    )
    return _other(service.require_other(actor, debt_id))


# -- replies ---------------------------------------------------------------------------------


def _planned(row: ScheduledInstalment) -> ScheduleRowReply:
    return ScheduleRowReply(
        sequence=row.sequence,
        due_on=row.due_on,
        opening=row.opening,
        interest=row.interest,
        fees=row.fees,
        principal=row.principal,
        capitalised=row.capitalised,
        total=row.total,
        closing=row.closing,
    )


def _totals(rows: list[ScheduledInstalment]) -> ScheduleSummaryReply:
    made = summarise(rows)
    return ScheduleSummaryReply(
        principal=made.principal,
        interest=made.interest,
        fees=made.fees,
        capitalised=made.capitalised,
        total=made.total,
        instalments=made.instalments,
        final_due_on=made.final_due_on,
    )


def _stored(row: LoanInstalment, today: date, live: bool) -> ScheduleRowReply:
    state = instalment_state(row.due_on, row.owed, row.paid, today, closed=row.closed or not live)
    if row.closed and row.owed > ZERO and row.paid >= row.owed:
        state = InstalmentState.PAID
    return ScheduleRowReply(
        id=row.id,
        sequence=row.sequence,
        due_on=row.due_on,
        opening=row.opening,
        interest=row.interest,
        fees=row.fees,
        principal=row.principal,
        capitalised=row.capitalised,
        penalty=row.penalty,
        total=row.owed,
        closing=row.closing,
        paid=row.paid,
        arrears=row.arrears,
        state=state.value,
        state_label=state.label,
    )


def _version(names: Names, version: LoanSchedule, today: date, live: bool) -> VersionReply:
    rows = list(version.instalments)
    planned = [
        ScheduledInstalment(
            row.sequence,
            row.due_on,
            row.opening,
            row.interest,
            row.fees,
            row.principal,
            row.closing,
            row.capitalised,
        )
        for row in rows
        if not row.arrears
    ]
    return VersionReply(
        id=version.id,
        version=version.version,
        kind=version.kind,
        kind_label=RevisionKind(version.kind).label,
        state=version.state,
        reason=version.reason,
        effective_on=version.effective_on,
        principal_basis=version.principal_basis,
        annual_rate=Decimal(str(version.terms.get("annual_rate", "0"))),
        instalments=len(planned),
        proposed_by=names.user(version.proposed_by_id),
        decided_by=names.user(version.decided_by_id),
        decided_at=version.decided_at.isoformat() if version.decided_at else None,
        decision_note=version.decision_note,
        rows=[_stored(row, today, live) for row in rows],
        summary=_totals(planned),
    )


def _summary(service: LoanService, agreement: LoanAgreement) -> LoanSummaryReply:
    return LoanSummaryReply(**_summary_fields(service, agreement))


def _summary_fields(service: LoanService, agreement: LoanAgreement) -> dict[str, object]:
    position = service.position(agreement)
    state = AgreementState(agreement.state)
    return {
        "id": agreement.id,
        "number": agreement.number,
        "direction": agreement.direction,
        "direction_label": LoanDirection(agreement.direction).label,
        "loan_type": agreement.loan_type,
        "loan_type_label": LoanType(agreement.loan_type).label,
        "counterparty_name": agreement.counterparty_name,
        "agreement_reference": agreement.agreement_reference,
        "principal": agreement.principal,
        "annual_rate": agreement.annual_rate,
        "model": agreement.model,
        "model_label": InterestModel(agreement.model).label,
        "frequency": agreement.frequency,
        "frequency_label": RepaymentFrequency(agreement.frequency).label,
        "agreement_date": agreement.agreement_date,
        "maturity_date": agreement.maturity_date,
        "state": state.value,
        "state_label": state.label,
        "disputed": agreement.disputed,
        "outstanding": position.total_outstanding,
        "charges": position.interest_due + position.fees_due + position.penalties_due,
        "next_due_on": position.next_due_on,
        "next_due_amount": position.next_due_amount,
        "days_overdue": position.days_overdue,
        "revision_pending": service.proposed(agreement) is not None,
    }


def _detail(
    service: LoanService, names: Names, actor: AnyActor, agreement: LoanAgreement
) -> LoanDetailReply:
    today = service.today(agreement.business_id)
    position = service.position(agreement)
    current = service.current(agreement)
    live = agreement.state == AgreementState.ACTIVE.value
    proposed = service.proposed(agreement)
    transactions = list(reversed(service.transactions(actor, agreement.id)))
    securities = service.securities(actor, agreement.id)
    return LoanDetailReply(
        **_summary_fields(service, agreement),
        counterparty_party_id=agreement.counterparty_party_fk,
        counterparty_employee_id=agreement.counterparty_employee_id,
        purpose=agreement.purpose,
        currency_code=agreement.currency_code,
        disbursement_date=agreement.disbursement_date,
        compounding=agreement.compounding,
        instalments=agreement.instalments,
        first_due_on=agreement.first_due_on,
        grace_periods=agreement.grace_periods,
        interest_only_periods=agreement.interest_only_periods,
        balloon=agreement.balloon,
        periodic_fee=agreement.periodic_fee,
        fixed_interest=agreement.fixed_interest,
        upfront_fee=agreement.upfront_fee,
        penalty_rate=agreement.penalty_rate,
        early_settlement_percent=agreement.early_settlement_percent,
        prepayment_allowed=agreement.prepayment_allowed,
        allocation_order=list(agreement.allocation_order),
        financial_account_id=agreement.financial_account_fk,
        account=names.account(agreement.financial_account_fk),
        responsible_id=agreement.responsible_id,
        responsible=names.user(agreement.responsible_id),
        created_by=names.user(agreement.created_by_id),
        submitted_by=names.user(agreement.submitted_by_id),
        approved_by=names.user(agreement.approved_by_id),
        approval_note=agreement.approval_note,
        rejection_reason=agreement.rejection_reason,
        refinances=_number(service, agreement.refinances_fk),
        refinanced_by=_number(service, agreement.refinanced_by_fk),
        closed_on=agreement.closed_on,
        closing_note=agreement.closing_note,
        notes=agreement.notes,
        position=LoanPositionReply(
            principal_agreed=position.principal_agreed,
            principal_drawn=position.principal_drawn,
            undrawn=position.undrawn,
            capitalised=position.capitalised,
            principal_repaid=position.principal_repaid,
            principal_outstanding=position.principal_outstanding,
            interest_due=position.interest_due,
            fees_due=position.fees_due,
            penalties_due=position.penalties_due,
            total_outstanding=position.total_outstanding,
            arrears=position.arrears,
            next_due_on=position.next_due_on,
            next_due_amount=position.next_due_amount,
            days_overdue=position.days_overdue,
            settlement_today=position.settlement_today,
            ledger_principal=position.ledger_principal,
            ledger_accrued=position.ledger_accrued,
            agrees_with_ledger=(
                position.ledger_principal == position.principal_outstanding
                and position.ledger_accrued
                == position.interest_due + position.fees_due + position.penalties_due
            )
            if live
            else True,
        ),
        current=_version(names, current, today, live),
        proposed=_version(names, proposed, today, False) if proposed else None,
        versions=[
            _version(names, version, today, live and version.id == current.id)
            for version in agreement.schedules
        ],
        transactions=[_transaction(names, row) for row in transactions],
        securities=[_security(row) for row in securities],
    )


def _number(service: LoanService, agreement_id: uuid.UUID | None) -> str | None:
    if agreement_id is None:
        return None
    return service.number_of(agreement_id)


def _transaction(names: Names, row: LoanTransaction) -> TransactionReply:
    split: dict[str, Decimal] = {component.value: ZERO for component in Component}
    allocation: LoanAllocation
    for allocation in row.allocations:
        split[allocation.component] += allocation.amount
    return TransactionReply(
        id=row.id,
        kind=row.kind,
        kind_label=TransactionKind.LABELS.get(row.kind, row.kind),
        occurred_on=row.occurred_on,
        amount=row.amount,
        fee=row.fee,
        account=names.account(row.financial_account_fk),
        reference=row.reference,
        note=row.note,
        recorded_by=names.user(row.recorded_by_id),
        principal=split["principal"],
        interest=split["interest"],
        fees=split["fees"],
        penalty=split["penalty"],
        journal_entry_id=row.journal_entry_fk,
    )


def _security(row: LoanSecurity) -> SecurityReply:
    return SecurityReply(
        id=row.id,
        kind=row.kind,
        kind_label=SecurityKind(row.kind).label,
        description=row.description,
        party_name=row.party_name,
        value=row.value,
        review_on=row.review_on,
        released_on=row.released_on,
    )


def _line(names: Names, line: DebtLine) -> DebtLineReply:
    return DebtLineReply(
        kind=line.kind.value,
        kind_label=line.kind.label,
        source_type=line.source_type,
        source_id=line.source_id,
        reference=line.reference,
        counterparty=line.counterparty,
        counterparty_id=line.counterparty_id,
        description=line.description,
        original=line.original,
        settled=line.settled,
        outstanding=line.outstanding,
        charges=line.charges,
        issued_on=line.issued_on,
        due_on=line.due_on,
        standing=line.standing.value,
        standing_label=line.standing.label,
        bucket=line.bucket,
        responsible=names.user(line.responsible_id),
    )


def _other(row: OtherDebt) -> OtherDebtReply:
    return OtherDebtReply(
        id=row.id,
        number=row.number,
        owed_to_us=row.owed_to_us,
        counterparty_name=row.counterparty_name,
        description=row.description,
        original=row.original,
        settled=row.settled,
        outstanding=max(ZERO, row.original - row.settled),
        issued_on=row.issued_on,
        due_on=row.due_on,
    )
