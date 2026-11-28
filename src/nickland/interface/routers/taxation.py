"""Taxation endpoints: rules, evaluation, assessments, obligations and settlement."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Query

from nickland.application.services.taxation import TaxableLine, TaxContext
from nickland.domain.taxation import Condition, ConditionField, ConditionOperator
from nickland.interface.dependencies import ActorDependency, TaxDependency
from nickland.interface.tax_schemas import (
    AssessmentReply,
    AuthorityReply,
    CreateRuleContract,
    EvaluateContract,
    EvaluationReply,
    FilingReply,
    LineAssessmentReply,
    ObligationReply,
    OverrideContract,
    PaymentReply,
    RecordedAssessmentReply,
    RecordPaymentContract,
    RuleReply,
    SetConditionsContract,
    SetRateContract,
    SubmitFilingContract,
    TaxSummaryReply,
    WithdrawRuleContract,
)
from nickland.persistence.tax_models import TaxRule

router = APIRouter(prefix="/taxation", tags=["taxation"])


def _rule(taxation: TaxDependency, rule: TaxRule) -> RuleReply:
    reply = RuleReply.model_validate(rule)
    reply.current_rate = taxation.current_rate(rule)
    return reply


# -- overview --------------------------------------------------------------------------


@router.get("/summary", response_model=TaxSummaryReply, summary="Taxation overview")
def summary(
    actor: ActorDependency, taxation: TaxDependency, as_at: date | None = None
) -> TaxSummaryReply:
    return TaxSummaryReply.model_validate(taxation.summary(actor, as_at=as_at))


# -- configuration ---------------------------------------------------------------------


@router.get("/authorities", response_model=list[AuthorityReply], summary="List authorities")
def authorities(actor: ActorDependency, taxation: TaxDependency) -> list[AuthorityReply]:
    return [AuthorityReply.model_validate(item) for item in taxation.authorities(actor)]


@router.get("/rules", response_model=list[RuleReply], summary="List tax rules")
def list_rules(
    actor: ActorDependency, taxation: TaxDependency, include_withdrawn: bool = False
) -> list[RuleReply]:
    return [
        _rule(taxation, rule) for rule in taxation.rules(actor, include_withdrawn=include_withdrawn)
    ]


@router.post("/rules", response_model=RuleReply, status_code=201, summary="Configure a rule")
def create_rule(
    contract: CreateRuleContract, actor: ActorDependency, taxation: TaxDependency
) -> RuleReply:
    payload = contract.model_dump()
    return _rule(
        taxation,
        taxation.create_rule(
            actor,
            code=payload.pop("code"),
            name=payload.pop("name"),
            category=payload.pop("category"),
            treatment=payload.pop("treatment"),
            rate=payload.pop("rate"),
            method=payload.pop("method"),
            effective_from=payload.pop("effective_from"),
            authority_id=payload.pop("authority_id"),
            **payload,
        ),
    )


@router.get("/rules/{rule_id}", response_model=RuleReply, summary="Read a tax rule")
def read_rule(rule_id: uuid.UUID, actor: ActorDependency, taxation: TaxDependency) -> RuleReply:
    return _rule(taxation, taxation.require_rule(actor, rule_id))


@router.post(
    "/rules/{rule_id}/rate",
    response_model=RuleReply,
    summary="Change a rate, from a date",
)
def set_rate(
    rule_id: uuid.UUID,
    contract: SetRateContract,
    actor: ActorDependency,
    taxation: TaxDependency,
) -> RuleReply:
    """A change is prospective: what was charged stays charged, per PRD §19.2."""
    payload = contract.model_dump()
    taxation.set_rate(
        actor,
        rule_id,
        rate=payload.pop("rate"),
        effective_from=payload.pop("effective_from"),
        note=payload.pop("note"),
        method=payload.pop("method"),
        **payload,
    )
    return _rule(taxation, taxation.require_rule(actor, rule_id))


@router.put(
    "/rules/{rule_id}/conditions",
    response_model=RuleReply,
    summary="Set when a rule applies",
)
def set_conditions(
    rule_id: uuid.UUID,
    contract: SetConditionsContract,
    actor: ActorDependency,
    taxation: TaxDependency,
) -> RuleReply:
    conditions = [
        Condition(
            field=ConditionField(item.field),
            operator=ConditionOperator(item.operator),
            value=item.value,
            values=tuple(item.values),
        )
        for item in contract.conditions
    ]
    return _rule(taxation, taxation.set_conditions(actor, rule_id, conditions))


@router.post("/rules/{rule_id}/withdraw", response_model=RuleReply, summary="Withdraw a rule")
def withdraw_rule(
    rule_id: uuid.UUID,
    contract: WithdrawRuleContract,
    actor: ActorDependency,
    taxation: TaxDependency,
) -> RuleReply:
    """Withdrawal, not deletion: the charges it explains stay explained."""
    return _rule(taxation, taxation.withdraw_rule(actor, rule_id, contract.reason))


@router.delete("/rules/{rule_id}", status_code=204, summary="Delete an unused rule")
def delete_rule(rule_id: uuid.UUID, actor: ActorDependency, taxation: TaxDependency) -> None:
    taxation.delete_rule(actor, rule_id)


# -- evaluation ------------------------------------------------------------------------


@router.post(
    "/evaluate",
    response_model=EvaluationReply,
    summary="Decide what a transaction owes",
)
def evaluate(
    contract: EvaluateContract, actor: ActorDependency, taxation: TaxDependency
) -> EvaluationReply:
    """Nothing is written. A draft may ask this as often as it likes."""
    context = TaxContext(
        transaction_kind=contract.transaction_kind,
        on=contract.on,
        direction=contract.direction,
        party_id=contract.party_id,
        party_tax_treatment=contract.party_tax_treatment,
        party_is_withholding_agent=contract.party_is_withholding_agent,
        branch_code=contract.branch_code,
        jurisdiction=contract.jurisdiction,
        is_import=contract.is_import,
        currency_code=contract.currency_code,
        exemption_evidence=contract.exemption_evidence,
    )
    lines = [
        TaxableLine(
            reference=item.reference,
            net=item.net,
            quantity=item.quantity,
            product_tax_code=item.product_tax_code,
            product_category=item.product_category,
            item_class=item.item_class,
            product_id=item.product_id,
        )
        for item in contract.lines
    ]
    evaluation = taxation.evaluate(actor, lines, context)
    return EvaluationReply(
        lines=[
            LineAssessmentReply(
                line_reference=line.line_reference,
                net=line.net,
                charged=line.charged,
                withheld=line.withheld,
                assessments=[
                    AssessmentReply(
                        rule_code=item.rule_code,
                        rule_name=item.rule_name,
                        category=item.category.value,
                        outcome=item.outcome.value,
                        treatment=item.treatment.value,
                        method=item.method.value,
                        base=item.base,
                        rate=item.rate,
                        amount=item.amount,
                        detail=item.detail,
                        version_effective_from=item.version_effective_from,
                    )
                    for item in line.assessments
                ],
            )
            for line in evaluation.lines
        ],
        charged=evaluation.charged,
        withheld=evaluation.withheld,
        needs_review=len(evaluation.needs_review),
        by_rule=evaluation.by_rule(),
    )


@router.get(
    "/assessments",
    response_model=list[RecordedAssessmentReply],
    summary="The register behind every tax figure",
)
def assessments(
    actor: ActorDependency,
    taxation: TaxDependency,
    rule_id: uuid.UUID | None = None,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[RecordedAssessmentReply]:
    """One row per rule per source line, so every amount traces to what caused it."""
    return [
        RecordedAssessmentReply.model_validate(item)
        for item in taxation.assessments(
            actor,
            rule_id=rule_id,
            source_type=source_type,
            source_id=source_id,
            start=start,
            end=end,
            limit=limit,
        )
    ]


@router.post(
    "/assessments/{assessment_id}/override",
    response_model=RecordedAssessmentReply,
    summary="Override a calculated amount",
)
def override(
    assessment_id: uuid.UUID,
    contract: OverrideContract,
    actor: ActorDependency,
    taxation: TaxDependency,
) -> RecordedAssessmentReply:
    """The original figure and the hand that altered it both stay on the record."""
    assessment, _ = taxation.override(
        actor,
        assessment_id,
        amount=contract.amount,
        reason=contract.reason,
        evidence_reference=contract.evidence_reference,
    )
    return RecordedAssessmentReply.model_validate(assessment)


# -- obligations and settlement ---------------------------------------------------------


@router.get("/obligations", response_model=list[ObligationReply], summary="What is owed")
def obligations(
    actor: ActorDependency,
    taxation: TaxDependency,
    state: str | None = None,
    rule_id: uuid.UUID | None = None,
) -> list[ObligationReply]:
    replies: list[ObligationReply] = []
    for item in taxation.obligations(actor, state=state, rule_id=rule_id):
        reply = ObligationReply.model_validate(item)
        reply.net_payable = taxation.net_payable(item)
        replies.append(reply)
    return replies


@router.get("/payments", response_model=list[PaymentReply], summary="Payments made")
def payments(
    actor: ActorDependency,
    taxation: TaxDependency,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[PaymentReply]:
    return [PaymentReply.model_validate(item) for item in taxation.payments(actor, limit=limit)]


@router.post("/payments", response_model=PaymentReply, status_code=201, summary="Record a payment")
def record_payment(
    contract: RecordPaymentContract, actor: ActorDependency, taxation: TaxDependency
) -> PaymentReply:
    """An unallocated remainder stays unallocated rather than being forced somewhere."""
    allocations: list[tuple[uuid.UUID, Decimal]] = [
        (item.obligation_id, item.amount) for item in contract.allocations
    ]
    return PaymentReply.model_validate(
        taxation.record_payment(
            actor,
            reference=contract.reference,
            paid_on=contract.paid_on,
            amount=contract.amount,
            authority_id=contract.authority_id,
            allocations=allocations or None,
            method=contract.method,
            bank_reference=contract.bank_reference,
            evidence_reference=contract.evidence_reference,
            note=contract.note,
        )
    )


@router.get("/filings", response_model=list[FilingReply], summary="Returns and filings")
def filings(
    actor: ActorDependency, taxation: TaxDependency, state: str | None = None
) -> list[FilingReply]:
    return [FilingReply.model_validate(item) for item in taxation.filings(actor, state=state)]


@router.post(
    "/obligations/{obligation_id}/filing",
    response_model=FilingReply,
    status_code=201,
    summary="Open a return",
)
def open_filing(
    obligation_id: uuid.UUID, actor: ActorDependency, taxation: TaxDependency
) -> FilingReply:
    return FilingReply.model_validate(taxation.open_filing(actor, obligation_id))


@router.post("/filings/{filing_id}/submit", response_model=FilingReply, summary="Lodge a return")
def submit_filing(
    filing_id: uuid.UUID,
    contract: SubmitFilingContract,
    actor: ActorDependency,
    taxation: TaxDependency,
) -> FilingReply:
    return FilingReply.model_validate(
        taxation.submit_filing(
            actor,
            filing_id,
            reference=contract.reference,
            acknowledgement=contract.acknowledgement,
        )
    )


__all__ = ["router"]
