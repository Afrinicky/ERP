"""Records as the finance screens read them: names in place of identifiers.

The interface reads names through this one place so that each router stays a thin
translation of its service.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from nickland.application.services.receivables import CustomerStatement
from nickland.application.services.treasury import AccountPosition
from nickland.domain.finance import (
    DepositCondition,
    ExpenseSettlement,
    ExpenseState,
    FinancialAccountKind,
    InvoiceState,
    TreasuryMovementKind,
)
from nickland.domain.money import money
from nickland.domain.orders import AgeingBucket
from nickland.interface.finance_schemas import (
    DepositReply,
    ExpenseReply,
    FinancialAccountReply,
    InvoiceLineReply,
    InvoiceReply,
    MovementReply,
    PartyStatementReply,
    ReceiptReply,
    StatementEntryReply,
)
from nickland.persistence.catalogue_models import Party
from nickland.persistence.finance_models import (
    CustomerDeposit,
    CustomerReceipt,
    Expense,
    FinancialAccount,
    SalesInvoice,
    TreasuryMovement,
)
from nickland.persistence.ledger_models import Account
from nickland.persistence.models import User
from nickland.persistence.order_models import SalesOrder


class Names:
    """Looks up and remembers the names a reply shows."""

    def __init__(self, session: Session) -> None:
        self._db = session
        self._cache: dict[tuple[str, uuid.UUID], str | None] = {}

    def _get(
        self, kind: str, model: type, identifier: uuid.UUID | None, attribute: str
    ) -> str | None:
        if identifier is None:
            return None
        key = (kind, identifier)
        if key not in self._cache:
            row = self._db.get(model, identifier)
            self._cache[key] = getattr(row, attribute) if row is not None else None
        return self._cache[key]

    def party(self, identifier: uuid.UUID | None) -> str | None:
        return self._get("party", Party, identifier, "name")

    def user(self, identifier: uuid.UUID | None) -> str | None:
        return self._get("user", User, identifier, "full_name")

    def account(self, identifier: uuid.UUID | None) -> str | None:
        return self._get("financial_account", FinancialAccount, identifier, "name")

    def order(self, identifier: uuid.UUID | None) -> str | None:
        return self._get("order", SalesOrder, identifier, "number")

    def ledger(self, code: str, business_id: uuid.UUID) -> str | None:
        from sqlalchemy import select

        row = self._db.scalar(
            select(Account).where(Account.business_id == business_id, Account.code == code)
        )
        return row.name if row else None


def financial_account(position: AccountPosition) -> FinancialAccountReply:
    account = position.account
    return FinancialAccountReply(
        id=account.id,
        kind=account.kind,
        kind_label=FinancialAccountKind(account.kind).label,
        name=account.name,
        ledger_code=position.ledger.code,
        institution=account.institution,
        number=account.number,
        branch_name=account.branch_name,
        custodian=position.custodian,
        balance=position.balance,
        is_active=account.is_active,
    )


def movement(names: Names, item: TreasuryMovement) -> MovementReply:
    return MovementReply(
        id=item.id,
        number=item.number,
        kind=item.kind,
        kind_label=TreasuryMovementKind(item.kind).label,
        occurred_on=item.occurred_on,
        from_account=names.account(item.from_account_fk),
        to_account=names.account(item.to_account_fk),
        amount=item.amount,
        charge=item.charge,
        reference=item.reference,
        reason=item.reason,
    )


def invoice(names: Names, item: SalesInvoice, today: date) -> InvoiceReply:
    outstanding = money(item.outstanding)
    return InvoiceReply(
        id=item.id,
        number=item.number,
        origin=item.origin,
        customer_id=item.customer_fk,
        customer=names.party(item.customer_fk),
        order_number=names.order(item.sales_order_fk),
        issued_on=item.issued_on,
        due_on=item.due_on,
        net_total=item.net_total,
        tax_total=item.tax_total,
        total=item.total,
        amount_received=item.amount_received,
        amount_credited=item.amount_credited,
        amount_written_off=item.amount_written_off,
        amount_provided=item.amount_provided,
        outstanding=outstanding,
        state=item.state,
        state_label=InvoiceState(item.state).label,
        disputed=item.disputed,
        days_overdue=max(0, (today - item.due_on).days) if outstanding > 0 else 0,
        credit_override_reason=item.credit_override_reason,
        lines=[
            InvoiceLineReply(
                id=line.id,
                position=line.position,
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                net=line.net,
                tax=line.tax,
                total=line.total,
                quantity_credited=line.quantity_credited,
            )
            for line in item.lines
        ],
    )


def receipt(names: Names, item: CustomerReceipt) -> ReceiptReply:
    return ReceiptReply(
        id=item.id,
        number=item.number,
        customer_id=item.customer_fk,
        customer=names.party(item.customer_fk),
        account=names.account(item.financial_account_fk),
        received_on=item.received_on,
        amount=item.amount,
        allocated=item.allocated,
        refunded=item.refunded,
        unapplied=money(item.unapplied),
        reference=item.reference,
    )


def deposit(names: Names, item: CustomerDeposit) -> DepositReply:
    return DepositReply(
        id=item.id,
        number=item.number,
        customer_id=item.customer_fk,
        customer=names.party(item.customer_fk),
        order_number=names.order(item.sales_order_fk),
        received_on=item.received_on,
        amount=item.amount,
        applied=item.applied,
        refunded=item.refunded,
        held=money(item.held),
        condition=item.condition,
        condition_label=DepositCondition(item.condition).label,
        state=item.state,
    )


def expense(names: Names, item: Expense) -> ExpenseReply:
    return ExpenseReply(
        id=item.id,
        number=item.number,
        payee_name=item.payee_name,
        category_code=item.category_code,
        category=names.ledger(item.category_code, item.business_id),
        description=item.description,
        classification=item.classification,
        net=item.net,
        input_tax=item.input_tax,
        total=item.total,
        incurred_on=item.incurred_on,
        payee_reference=item.payee_reference,
        settlement=item.settlement,
        settlement_label=ExpenseSettlement(item.settlement).label,
        account=names.account(item.financial_account_fk or item.settled_from_fk),
        employee=names.user(item.employee_id),
        state=item.state,
        state_label=ExpenseState(item.state).label,
        submitted_by=names.user(item.submitted_by_id),
        approved_by=names.user(item.approved_by_id),
        rejection_reason=item.rejection_reason,
        duplicate_of=None,
        duplicate_reason=item.duplicate_reason,
        hold_reason=item.hold_reason,
        settled_on=item.settled_on,
    )


def customer_statement(statement: CustomerStatement) -> PartyStatementReply:
    return PartyStatementReply(
        party_id=statement.customer.id,
        party=statement.customer.name,
        starts_on=statement.starts_on,
        ends_on=statement.ends_on,
        opening=statement.opening,
        closing=statement.closing,
        entries=[StatementEntryReply.model_validate(item) for item in statement.entries],
        ageing={bucket.value: value for bucket, value in statement.ageing.items()},
    )


BUCKETS = (
    AgeingBucket.CURRENT,
    AgeingBucket.DAYS_1_30,
    AgeingBucket.DAYS_31_60,
    AgeingBucket.DAYS_61_90,
    AgeingBucket.OVER_90,
)
