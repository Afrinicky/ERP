"""ORM models for Phase 1: identity, organisation, audit, custom fields and theme.

Every business-owned table carries ``business_id`` from this first migration, per
ADR-0003. Monetary columns use fixed-precision decimal, per PRD 9.5.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON as SA_JSON
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
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
from nickland.persistence.types import UtcDateTime, Uuid

# --------------------------------------------------------------------------------------
# Organisation
# --------------------------------------------------------------------------------------


class Business(Base, IdentifiedMixin, TimestampedMixin):
    """The business. One row in the initial release, per ADR-0003."""

    __tablename__ = "businesses"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    trading_name: Mapped[str | None] = mapped_column(String(200))

    tax_identifier: Mapped[str | None] = mapped_column(String(60))
    registration_number: Mapped[str | None] = mapped_column(String(60))

    email: Mapped[str | None] = mapped_column(String(200))
    telephone: Mapped[str | None] = mapped_column(String(60))
    website: Mapped[str | None] = mapped_column(String(200))

    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    currency_symbol: Mapped[str] = mapped_column(String(8), nullable=False, default="GH₵")
    currency_precision: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Africa/Accra")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en-GB")

    logo_attachment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    financial_year_start_month: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    setup_completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    branches: Mapped[list[Branch]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "currency_precision >= 0 AND currency_precision <= 6",
            name="currency_precision_range",
        ),
        CheckConstraint(
            "financial_year_start_month >= 1 AND financial_year_start_month <= 12",
            name="financial_year_start_month_range",
        ),
    )


class Branch(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A branch. Document numbering is unique per business, branch and type."""

    __tablename__ = "branches"

    business_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    email: Mapped[str | None] = mapped_column(String(200))
    telephone: Mapped[str | None] = mapped_column(String(60))

    business: Mapped[Business] = relationship(back_populates="branches")
    warehouses: Mapped[list[Warehouse]] = relationship(
        back_populates="branch", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("business_id", "code", name=None),)


class Warehouse(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A stock-holding location."""

    __tablename__ = "warehouses"

    branch_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("branches.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allows_negative_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    branch: Mapped[Branch] = relationship(back_populates="warehouses")

    __table_args__ = (UniqueConstraint("business_id", "code", name=None),)


class Address(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A postal address, attached polymorphically to any owning record."""

    __tablename__ = "addresses"

    owner_type: Mapped[str] = mapped_column(String(40), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False, default="registered")

    line_one: Mapped[str | None] = mapped_column(String(200))
    line_two: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    postal_code: Mapped[str | None] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(80))
    digital_address: Mapped[str | None] = mapped_column(String(32))
    """Ghana Post GPS or an equivalent national addressing code."""

    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_addresses_owner", "business_id", "owner_type", "owner_id"),)


class NumberSequence(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A document-numbering sequence, per PRD 6.7.

    Unique by business, branch, document kind and period bucket. Allocation occurs
    inside the issuing transaction; a number is never reused.
    """

    __tablename__ = "number_sequences"

    branch_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("branches.id", ondelete="CASCADE")
    )
    document_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    pattern: Mapped[str] = mapped_column(String(120), nullable=False)
    reset_cadence: Mapped[str] = mapped_column(String(12), nullable=False, default="annually")
    period_key: Mapped[str] = mapped_column(String(16), nullable=False, default="all")
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("business_id", "branch_fk", "document_kind", "period_key", name=None),
        CheckConstraint("next_value >= 1", name="next_value_positive"),
    )


class Setting(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """Typed configuration, keyed per business."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[Any] = mapped_column(SA_JSON, nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False, default="json")
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("business_id", "key", name=None),)


class ThemeProfile(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """The active theme: a preset reference plus validated overrides, per ADR-0002."""

    __tablename__ = "theme_profiles"

    preset_code: Mapped[str] = mapped_column(String(32), nullable=False)
    overrides: Mapped[dict[str, Any]] = mapped_column(SA_JSON, nullable=False, default=dict)
    dark_mode_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    audit_passed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    """When the stored overrides last passed the contrast audit."""

    __table_args__ = (UniqueConstraint("business_id", name=None),)


# --------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------


class User(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin):
    """A user. Passwords are hashed with Argon2id, per PRD 9.6."""

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(32), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200))
    telephone: Mapped[str | None] = mapped_column(String(60))

    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_signed_in_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    discount_limit_percent: Mapped[Any | None] = mapped_column(SA_JSON)
    """Per-user discount approval limit. Interpreted by the pricing engine in Phase 5."""

    roles: Mapped[list[Role]] = relationship(
        secondary="user_roles", back_populates="users", viewonly=True, lazy="selectin"
    )
    """Read-only view. Assignment is written as a UserRole row, never through here."""
    password_history: Mapped[list[PasswordHistory]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("business_id", "username", name=None),
        CheckConstraint("failed_attempts >= 0", name="failed_attempts_non_negative"),
    )


class PasswordHistory(Base, IdentifiedMixin, BusinessScopedMixin):
    """Previous password hashes, refused on reuse within the policy window."""

    __tablename__ = "password_history"

    user_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    user: Mapped[User] = relationship(back_populates="password_history")

    __table_args__ = (Index("ix_password_history_user", "user_fk", "created_at"),)


class Role(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A role. Eight are seeded per PRD 3; each is editable and more may be created."""

    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_administrator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """A seeded role. It may be amended but not removed."""

    users: Mapped[list[User]] = relationship(
        secondary="user_roles", back_populates="roles", viewonly=True
    )
    permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (UniqueConstraint("business_id", "code", name=None),)


class RolePermission(Base, IdentifiedMixin, BusinessScopedMixin):
    """One permission granted to one role."""

    __tablename__ = "role_permissions"

    role_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    permission_code: Mapped[str] = mapped_column(String(64), nullable=False)

    role: Mapped[Role] = relationship(back_populates="permissions")

    __table_args__ = (UniqueConstraint("role_fk", "permission_code", name=None),)


class UserRole(Base, IdentifiedMixin, BusinessScopedMixin):
    """Assignment of a role to a user."""

    __tablename__ = "user_roles"

    user_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    assigned_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    assigned_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (UniqueConstraint("user_fk", "role_fk", name=None),)


class Session(Base, IdentifiedMixin, BusinessScopedMixin):
    """An authenticated session. The token itself is stored only as a hash."""

    __tablename__ = "sessions"

    user_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    device: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_sessions_user_active", "user_fk", "revoked_at"),)


# --------------------------------------------------------------------------------------
# Audit — append-only, per PRD 10
# --------------------------------------------------------------------------------------


class AuditEvent(Base, IdentifiedMixin, BusinessScopedMixin, BranchScopedMixin):
    """An audit entry.

    Append-only: there is no update or delete path. Written in the same transaction as
    the change it describes, so neither can commit without the other.
    """

    __tablename__ = "audit_events"

    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    actor_username: Mapped[str | None] = mapped_column(String(32))
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    device: Mapped[str | None] = mapped_column(String(200))

    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    entity_label: Mapped[str | None] = mapped_column(String(200))

    reason: Mapped[str | None] = mapped_column(Text)
    before_values: Mapped[dict[str, Any] | None] = mapped_column(SA_JSON)
    after_values: Mapped[dict[str, Any] | None] = mapped_column(SA_JSON)
    context: Mapped[dict[str, Any] | None] = mapped_column(SA_JSON)

    __table_args__ = (
        Index("ix_audit_events_entity", "business_id", "entity_type", "entity_id"),
        Index("ix_audit_events_occurred", "business_id", "occurred_at"),
    )


# --------------------------------------------------------------------------------------
# Custom fields — PRD 6.1
# --------------------------------------------------------------------------------------


class CustomFieldDefinition(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """An administrator-defined field, added without source-code change."""

    __tablename__ = "custom_field_definitions"

    target: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    field_type: Mapped[str] = mapped_column(String(16), nullable=False)
    help_text: Mapped[str | None] = mapped_column(Text)

    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    choices: Mapped[list[str] | None] = mapped_column(SA_JSON)
    default_value: Mapped[Any | None] = mapped_column(SA_JSON)
    validation: Mapped[dict[str, Any] | None] = mapped_column(SA_JSON)

    show_on_documents: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Whether the field is offered to the document designer's field palette."""

    __table_args__ = (UniqueConstraint("business_id", "target", "code", name=None),)


class CustomFieldValue(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One custom-field value against one record."""

    __tablename__ = "custom_field_values"

    definition_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("custom_field_definitions.id", ondelete="CASCADE"), nullable=False
    )
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    value_text: Mapped[str | None] = mapped_column(Text)
    value_number: Mapped[Any | None] = mapped_column(SA_JSON)
    value_date: Mapped[date | None] = mapped_column(Date)
    value_boolean: Mapped[bool | None] = mapped_column(Boolean)
    value_json: Mapped[Any | None] = mapped_column(SA_JSON)

    __table_args__ = (
        UniqueConstraint("definition_fk", "owner_id", name=None),
        Index("ix_custom_field_values_owner", "business_id", "owner_type", "owner_id"),
    )


# --------------------------------------------------------------------------------------
# Attachments — managed store, per PRD 9.5
# --------------------------------------------------------------------------------------


class Attachment(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A stored file.

    The path is derived from the identifier, never from user input. The original
    filename is retained for display only and is never trusted.
    """

    __tablename__ = "attachments"

    owner_type: Mapped[str | None] = mapped_column(String(40), index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)

    purpose: Mapped[str | None] = mapped_column(String(40))
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    is_immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Set on evidence that must be preserved without alteration, per PRD 6.7."""

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        Index("ix_attachments_owner", "business_id", "owner_type", "owner_id"),
    )


# --------------------------------------------------------------------------------------
# Backup register
# --------------------------------------------------------------------------------------


class BackupRecord(Base, IdentifiedMixin, BusinessScopedMixin):
    """A backup run, its outcome and its verification, per PRD 9.5."""

    __tablename__ = "backup_records"

    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")

    storage_key: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    includes_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    verification_note: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    schema_revision: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (Index("ix_backup_records_started", "business_id", "started_at"),)


# Phase 2 catalogue and party models. Imported here so that importing this module
# registers every mapped class with the metadata.
from nickland.persistence.catalogue_models import (  # noqa: E402
    AffiliateEligibility,
    AffiliateProfile,
    Barcode,
    BundleComponentRow,
    Contact,
    CustomerPrice,
    ImportRun,
    Party,
    PriceList,
    PriceListLine,
    Product,
    ProductAttribute,
    ProductAttributeValue,
    ProductCategory,
    SupplierProduct,
    UnitConversionRow,
    UnitOfMeasure,
)
from nickland.persistence.document_models import (  # noqa: E402
    DocumentTemplate,
    DocumentType,
    IssuedDocument,
)
from nickland.persistence.ledger_models import (  # noqa: E402
    JournalApproval,
    JournalEntry,
    JournalLine,
    OpeningBalance,
    PeriodCloseCheck,
    SubledgerBalance,
)
from nickland.persistence.pricing_models import (  # noqa: E402
    CommissionAccrual,
    DiscountLimitRow,
    InstalmentBand,
    InstalmentPlan,
    MarginPolicy,
    Quotation,
    QuotationExpense,
    QuotationInstalment,
    QuotationLine,
    QuotationLineCost,
    QuotationLineSource,
    QuotationRequest,
    QuotationRequestLine,
    QuotationSnapshot,
    SupplierQuotation,
    SupplierQuotationLine,
    SupplierRequest,
    SupplierRequestLine,
)
from nickland.persistence.tax_models import (  # noqa: E402
    TaxAssessment,
    TaxAuthority,
    TaxFiling,
    TaxObligation,
    TaxOverride,
    TaxPayment,
    TaxPaymentAllocation,
    TaxRule,
    TaxRuleCondition,
    TaxRuleVersion,
)

__all__ = [
    "Address",
    "AffiliateEligibility",
    "AffiliateProfile",
    "Attachment",
    "AuditEvent",
    "BackupRecord",
    "Barcode",
    "Branch",
    "BundleComponentRow",
    "Business",
    "CommissionAccrual",
    "Contact",
    "CustomFieldDefinition",
    "CustomFieldValue",
    "CustomerPrice",
    "DiscountLimitRow",
    "DocumentTemplate",
    "DocumentType",
    "ImportRun",
    "InstalmentBand",
    "InstalmentPlan",
    "IssuedDocument",
    "JournalApproval",
    "JournalEntry",
    "JournalLine",
    "MarginPolicy",
    "NumberSequence",
    "OpeningBalance",
    "Party",
    "PasswordHistory",
    "PeriodCloseCheck",
    "PriceList",
    "PriceListLine",
    "Product",
    "ProductAttribute",
    "ProductAttributeValue",
    "ProductCategory",
    "Quotation",
    "QuotationExpense",
    "QuotationInstalment",
    "QuotationLine",
    "QuotationLineCost",
    "QuotationLineSource",
    "QuotationRequest",
    "QuotationRequestLine",
    "QuotationSnapshot",
    "Role",
    "RolePermission",
    "Session",
    "Setting",
    "SubledgerBalance",
    "SupplierProduct",
    "SupplierQuotation",
    "SupplierQuotationLine",
    "SupplierRequest",
    "SupplierRequestLine",
    "TaxAssessment",
    "TaxAuthority",
    "TaxFiling",
    "TaxObligation",
    "TaxOverride",
    "TaxPayment",
    "TaxPaymentAllocation",
    "TaxRule",
    "TaxRuleCondition",
    "TaxRuleVersion",
    "ThemeProfile",
    "UnitConversionRow",
    "UnitOfMeasure",
    "User",
    "UserRole",
    "Warehouse",
]
