"""Request and response contracts.

Every field is validated at the service boundary, per PRD 9.6. Monetary values cross
the boundary as strings, so no amount passes through a JSON float.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    """Base contract. Unknown fields are rejected rather than ignored."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Reply(BaseModel):
    """Base response."""

    model_config = ConfigDict(from_attributes=True)


# -- setup -----------------------------------------------------------------------------


class PresetReply(Reply):
    code: str
    name: str
    description: str
    source: str
    recommended: bool = False


class SetupStatusReply(Reply):
    setup_required: bool
    presets: list[PresetReply]
    password_requirements: list[str]


class SetupContract(Contract):
    business_name: str = Field(min_length=1, max_length=200)
    administrator_username: str = Field(min_length=3, max_length=32)
    administrator_full_name: str = Field(min_length=1, max_length=160)
    administrator_password: str = Field(min_length=1, max_length=1024)
    theme_preset: str = Field(min_length=1, max_length=32)

    legal_name: str | None = Field(default=None, max_length=200)
    tax_identifier: str | None = Field(default=None, max_length=60)
    registration_number: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=200)
    telephone: str | None = Field(default=None, max_length=60)
    website: str | None = Field(default=None, max_length=200)
    administrator_email: str | None = Field(default=None, max_length=200)

    base_currency: str = Field(default="GHS", min_length=3, max_length=3)
    currency_symbol: str = Field(default="GH₵", max_length=8)
    currency_precision: int = Field(default=2, ge=0, le=6)
    timezone: str = Field(default="Africa/Accra", max_length=64)
    financial_year_start_month: int = Field(default=1, ge=1, le=12)

    branch_code: str = Field(default="main", max_length=16)
    branch_name: str = Field(default="Head office", max_length=160)
    warehouse_code: str = Field(default="main", max_length=16)
    warehouse_name: str = Field(default="Main store", max_length=160)


class SetupReply(Reply):
    business_id: uuid.UUID
    branch_id: uuid.UUID
    administrator_id: uuid.UUID
    roles_created: int
    sequences_created: int


# -- authentication --------------------------------------------------------------------


class SignInContract(Contract):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    device: str | None = Field(default=None, max_length=200)


class ActorReply(Reply):
    user_id: uuid.UUID
    username: str
    full_name: str
    business_id: uuid.UUID
    branch_id: uuid.UUID | None
    role_codes: list[str]
    permissions: list[str]
    is_administrator: bool


class SignInReply(Reply):
    token: str
    expires_at: datetime
    must_change_password: bool
    actor: ActorReply


class ChangePasswordContract(Contract):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class ResetPasswordContract(Contract):
    new_password: str = Field(min_length=1, max_length=1024)
    reason: str = Field(min_length=1, max_length=500)


# -- users and roles -------------------------------------------------------------------


class CreateUserContract(Contract):
    username: str = Field(min_length=3, max_length=32)
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=1024)
    role_codes: list[str] = Field(min_length=1, max_length=12)
    email: str | None = Field(default=None, max_length=200)
    telephone: str | None = Field(default=None, max_length=60)
    branch_id: uuid.UUID | None = None
    must_change_password: bool = True


class AmendUserContract(Contract):
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=200)
    telephone: str | None = Field(default=None, max_length=60)
    branch_id: uuid.UUID | None = None


class SetActiveContract(Contract):
    active: bool
    reason: str = Field(min_length=1, max_length=500)


class UserReply(Reply):
    id: uuid.UUID
    username: str
    full_name: str
    email: str | None
    telephone: str | None
    branch_id: uuid.UUID | None
    is_active: bool
    must_change_password: bool
    locked_until: datetime | None
    last_signed_in_at: datetime | None
    role_codes: list[str] = Field(default_factory=list)


class PermissionReply(Reply):
    code: str
    name: str
    group: str
    description: str
    sensitive: bool


class RoleReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_administrator: bool
    is_read_only: bool
    is_system: bool
    permissions: list[str] = Field(default_factory=list)


class SetRolePermissionsContract(Contract):
    permission_codes: list[str] = Field(max_length=256)
    reason: str = Field(min_length=1, max_length=500)


class AssignRoleContract(Contract):
    role_code: str = Field(min_length=1, max_length=48)


# -- business configuration ------------------------------------------------------------


class BusinessReply(Reply):
    id: uuid.UUID
    name: str
    legal_name: str | None
    trading_name: str | None
    tax_identifier: str | None
    registration_number: str | None
    email: str | None
    telephone: str | None
    website: str | None
    base_currency: str
    currency_symbol: str
    currency_precision: int
    timezone: str
    locale: str
    financial_year_start_month: int
    setup_completed_at: datetime | None


class AmendBusinessContract(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)
    trading_name: str | None = Field(default=None, max_length=200)
    tax_identifier: str | None = Field(default=None, max_length=60)
    registration_number: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=200)
    telephone: str | None = Field(default=None, max_length=60)
    website: str | None = Field(default=None, max_length=200)
    currency_symbol: str | None = Field(default=None, max_length=8)
    currency_precision: int | None = Field(default=None, ge=0, le=6)
    timezone: str | None = Field(default=None, max_length=64)
    financial_year_start_month: int | None = Field(default=None, ge=1, le=12)


class BranchReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    is_primary: bool
    is_active: bool
    email: str | None
    telephone: str | None


class CreateBranchContract(Contract):
    code: str = Field(min_length=3, max_length=16)
    name: str = Field(min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=200)
    telephone: str | None = Field(default=None, max_length=60)
    is_primary: bool = False


class WarehouseReply(Reply):
    id: uuid.UUID
    branch_fk: uuid.UUID
    code: str
    name: str
    is_default: bool
    is_active: bool
    allows_negative_stock: bool


class CreateWarehouseContract(Contract):
    branch_id: uuid.UUID
    code: str = Field(min_length=3, max_length=16)
    name: str = Field(min_length=1, max_length=160)
    is_default: bool = False
    allows_negative_stock: bool = False


# -- numbering -------------------------------------------------------------------------


class SequenceReply(Reply):
    id: uuid.UUID
    document_kind: str
    document_label: str = ""
    prefix: str
    pattern: str
    reset_cadence: str
    period_key: str
    next_value: int
    is_active: bool
    preview: str = ""


class AmendSequenceContract(Contract):
    prefix: str | None = Field(default=None, min_length=1, max_length=12)
    pattern: str | None = Field(default=None, min_length=1, max_length=120)
    reset_cadence: Literal["never", "annually", "monthly"] | None = None
    next_value: int | None = Field(default=None, ge=1)


# -- theme -----------------------------------------------------------------------------


class ThemeReply(Reply):
    preset_code: str
    preset_name: str
    preset_source: str
    colour: dict[str, str]
    overrides: dict[str, str]
    customisable: list[str]
    dark_mode_enabled: bool


class AuditLineReply(Reply):
    label: str
    foreground: str
    background: str
    ratio: float
    required: float
    passes: bool


class ThemePreviewReply(Reply):
    theme: ThemeReply
    audit: list[AuditLineReply]
    passes: bool


class ApplyThemeContract(Contract):
    preset_code: str = Field(min_length=1, max_length=32)
    overrides: dict[str, str] = Field(default_factory=dict)
    dark_mode_enabled: bool | None = None

    @field_validator("overrides")
    @classmethod
    def _bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 32:
            raise ValueError("Too many colour overrides.")
        return value


# -- custom fields ---------------------------------------------------------------------


class CustomFieldReply(Reply):
    id: uuid.UUID
    target: str
    code: str
    label: str
    field_type: str
    help_text: str | None
    is_required: bool
    is_active: bool
    display_order: int
    choices: list[str] | None
    default_value: Any | None
    show_on_documents: bool


class DefineCustomFieldContract(Contract):
    target: str = Field(min_length=1, max_length=32)
    code: str = Field(min_length=3, max_length=40)
    label: str = Field(min_length=1, max_length=80)
    field_type: str = Field(min_length=1, max_length=16)
    is_required: bool = False
    choices: list[str] | None = Field(default=None, max_length=64)
    default_value: Any | None = None
    help_text: str | None = Field(default=None, max_length=500)
    display_order: int = Field(default=0, ge=0, le=9999)
    show_on_documents: bool = False
    validation: dict[str, Any] | None = None


class AmendCustomFieldContract(Contract):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    help_text: str | None = Field(default=None, max_length=500)
    is_required: bool | None = None
    display_order: int | None = Field(default=None, ge=0, le=9999)
    choices: list[str] | None = Field(default=None, max_length=64)
    default_value: Any | None = None
    show_on_documents: bool | None = None
    validation: dict[str, Any] | None = None


class WithdrawContract(Contract):
    reason: str = Field(min_length=1, max_length=500)


# -- platform --------------------------------------------------------------------------


class CheckReply(Reply):
    name: str
    state: str
    detail: str
    measurements: dict[str, Any] = Field(default_factory=dict)


class HealthReply(Reply):
    state: str
    deployment_mode: str
    checks: list[CheckReply]


class BackupReply(Reply):
    id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None
    status: str
    trigger: str
    storage_key: str | None
    size_bytes: int | None
    verified_at: datetime | None
    verification_note: str | None
    failure_reason: str | None


class RunBackupContract(Contract):
    include_attachments: bool = True


class RestoreContract(Contract):
    archive_name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    confirm: bool = False


class AuditEventReply(Reply):
    id: uuid.UUID
    occurred_at: datetime
    actor_username: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    entity_label: str | None
    reason: str | None
    before_values: dict[str, Any] | None
    after_values: dict[str, Any] | None


class PageReply(Reply):
    """A page of results."""

    items: list[Any]
    total: int
    offset: int
    limit: int
