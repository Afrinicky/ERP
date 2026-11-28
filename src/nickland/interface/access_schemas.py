"""Request and response contracts for access control."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from nickland.interface.schemas import Contract, Reply


class CreateRoleContract(Contract):
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    copy_from: str | None = Field(default=None, max_length=48)


class AmendRoleContract(Contract):
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)


class ReasonContract(Contract):
    reason: str = Field(min_length=1, max_length=500)


class ExceptionContract(Contract):
    permission_code: str = Field(max_length=64)
    effect: str = Field(pattern="^(grant|withhold)$")


class SetExceptionsContract(Contract):
    exceptions: list[ExceptionContract] = Field(max_length=256)
    reason: str = Field(min_length=1, max_length=500)


class ExceptionReply(Reply):
    permission_code: str
    effect: str
    reason: str
    set_at: datetime
    set_by: str | None


class UserAccessReply(Reply):
    user_id: uuid.UUID
    full_name: str
    username: str
    is_administrator: bool
    roles: list[str]
    role_names: list[str]
    from_roles: list[str]
    exceptions: list[ExceptionReply]
    effective: list[str]


class SessionReply(Reply):
    id: uuid.UUID
    issued_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    device: str | None
    address: str | None


class EndedReply(Reply):
    ended: int


class AccessPolicyContract(Contract):
    minimum_length: int
    require_uppercase: bool
    require_lowercase: bool
    require_digit: bool
    require_symbol: bool
    reuse_window: int
    lockout_threshold: int
    lockout_minutes: int
    idle_minutes: int
    absolute_hours: int
    reason: str = Field(min_length=1, max_length=500)


class AccessPolicyReply(Reply):
    minimum_length: int
    require_uppercase: bool
    require_lowercase: bool
    require_digit: bool
    require_symbol: bool
    reuse_window: int
    lockout_threshold: int
    lockout_minutes: int
    idle_minutes: int
    absolute_hours: int
    is_default: bool
    requirements: list[str]
