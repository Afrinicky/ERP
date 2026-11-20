"""Financial management records: treasury, receivables, expenses, reconciliation. PRD §20.

Every amount here is also in the ledger; these tables hold what the ledger does not —
which account is which bank, which invoice a receipt settled, which statement line a
posting was matched to — so that every figure can be traced to its document and back.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON as SA_JSON
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nickland.persistence.base import (
    Base,
    BranchScopedMixin,
    BusinessScopedMixin,
    IdentifiedMixin,
    TimestampedMixin,
)
from nickland.persistence.types import Money, Quantity, UnitPrice, UtcDateTime, Uuid

# -- treasury -----------------------------------------------------------------------------


class FinancialAccount(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A cash drawer, petty cash box, bank account or Mobile Money wallet."""

    __tablename__ = "financial_accounts"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    account_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    """The ledger account it posts to. Its own, so it has its own balance."""
    institution: Mapped[str | None] = mapped_column(String(120))
    """The bank or the Mobile Money provider."""
    number: Mapped[str | None] = mapped_column(String(60))
    """The account or wallet number, as the institution writes it."""
    branch_name: Mapped[str | None] = mapped_column(String(120))
    custodian_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    """Who holds it: the cashier for a drawer, the officer for petty cash."""
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("business_id", "account_fk", name=None),
        UniqueConstraint("business_id", "name", name=None),
    )


class TreasuryMovement(
    Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin
):
    """Money moved between the business's own accounts, or a charge, interest or adjustment
    on one of them."""

    __tablename__ = "treasury_movements"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    from_account_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("financial_accounts.id", ondelete="RESTRICT")
    )
    to_account_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("financial_accounts.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    charge: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    """A fee on a transfer, borne by the sending account."""
    reference: Mapped[str | None] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("charge >= 0", name="charge_non_negative"),
        UniqueConstraint("business_id", "number", name=None),
    )


# -- receivables --------------------------------------------------------------------------


class SalesInvoice(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin):
    """What a customer owes for goods or services, and how much of it has been settled."""

    __tablename__ = "sales_invoices"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    customer_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sales_order_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sales_orders.id", ondelete="RESTRICT"), index=True
    )
    pos_sale_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pos_sales.id", ondelete="RESTRICT"), index=True
    )
    issued_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    customer_reference: Mapped[str | None] = mapped_column(String(120))
    net_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    amount_received: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    """Receipts and deposits allocated to it."""
    amount_credited: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    amount_written_off: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    amount_provided: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    """Provided against as doubtful, and not yet used or released."""
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    disputed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    credit_override_reason: Mapped[str | None] = mapped_column(Text)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    document_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    notes: Mapped[str | None] = mapped_column(Text)
    issued_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    lines: Mapped[list[SalesInvoiceLine]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="SalesInvoiceLine.position",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        CheckConstraint("total >= 0", name="total_non_negative"),
    )

    @property
    def outstanding(self) -> Decimal:
        return self.total - self.amount_received - self.amount_credited - self.amount_written_off


class SalesInvoiceLine(Base, IdentifiedMixin, BusinessScopedMixin):
    __tablename__ = "sales_invoice_lines"

    invoice_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sales_invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sales_order_line_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sales_order_lines.id", ondelete="RESTRICT")
    )
    product_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False)
    net: Mapped[Decimal] = mapped_column(Money, nullable=False)
    tax: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    tax_breakdown: Mapped[dict[str, str] | None] = mapped_column(SA_JSON)
    total: Mapped[Decimal] = mapped_column(Money, nullable=False)
    quantity_credited: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )

    invoice: Mapped[SalesInvoice] = relationship(back_populates="lines")


class CustomerCreditNote(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A credit against an invoice: goods returned, a price corrected, a concession."""

    __tablename__ = "customer_credit_notes"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    invoice_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sales_invoices.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    customer_fk: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    issued_on: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    net: Mapped[Decimal] = mapped_column(Money, nullable=False)
    tax: Mapped[Decimal] = mapped_column(Money, nullable=False)
    total: Mapped[Decimal] = mapped_column(Money, nullable=False)
    lines: Mapped[list[dict[str, str]]] = mapped_column(SA_JSON, nullable=False, default=list)
    """Each invoice line credited: its id, quantity, net and tax."""
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    issued_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (UniqueConstraint("business_id", "number", name=None),)


class CustomerReceipt(
    Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin
):
    """Money received from a customer against what they owe."""

    __tablename__ = "customer_receipts"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    financial_account_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("financial_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    allocated: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    refunded: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    reference: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    allocations: Mapped[list[ReceiptAllocation]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("allocated + refunded <= amount", name="not_over_allocated"),
    )

    @property
    def unapplied(self) -> Decimal:
        return self.amount - self.allocated - self.refunded


class ReceiptAllocation(Base, IdentifiedMixin, BusinessScopedMixin):
    __tablename__ = "receipt_allocations"

    receipt_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("customer_receipts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sales_invoices.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    allocated_on: Mapped[date] = mapped_column(Date, nullable=False)
    allocated_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    receipt: Mapped[CustomerReceipt] = relationship(back_populates="allocations")


class CustomerDeposit(
    Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin
):
    """An advance, held as owed back to the customer until its condition is met."""

    __tablename__ = "customer_deposits"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sales_order_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sales_orders.id", ondelete="RESTRICT"), index=True
    )
    financial_account_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("financial_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    applied: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    refunded: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    condition: Mapped[str] = mapped_column(String(16), nullable=False, default="on_invoice")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="held")
    reference: Mapped[str | None] = mapped_column(String(120))
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("applied + refunded <= amount", name="not_over_applied"),
    )

    @property
    def held(self) -> Decimal:
        return self.amount - self.applied - self.refunded


class DepositApplication(Base, IdentifiedMixin, BusinessScopedMixin):
    __tablename__ = "deposit_applications"

    deposit_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("customer_deposits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sales_invoices.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    applied_on: Mapped[date] = mapped_column(Date, nullable=False)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class CollectionNote(Base, IdentifiedMixin, BusinessScopedMixin):
    """A contact, promise or dispute, against a customer and perhaps one invoice."""

    __tablename__ = "collection_notes"

    customer_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sales_invoices.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    promised_on: Mapped[date | None] = mapped_column(Date)
    promised_amount: Mapped[Decimal | None] = mapped_column(Money)
    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class DebtProvision(Base, IdentifiedMixin, BusinessScopedMixin):
    """A provision against a doubtful invoice, or its release."""

    __tablename__ = "debt_provisions"

    invoice_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sales_invoices.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    """Positive provides; negative releases."""
    recorded_on: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
