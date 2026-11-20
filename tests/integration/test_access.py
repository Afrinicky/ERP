"""Access control against a real database: roles, a person's exceptions, sessions and
the business's sign-in policy."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.access import AccessControlService
from nickland.application.services.identity import IdentityService
from nickland.domain.errors import (
    AccountLockedError,
    AuthenticationError,
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from nickland.domain.identity import AccessPolicyTerms, OverrideEffect
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

STAFF = "Tamale4Harbour"


@pytest.fixture
def identity(db: Session, clock: FixedClock) -> IdentityService:
    return IdentityService(db, clock=clock)


@pytest.fixture
def access(db: Session, clock: FixedClock, identity: IdentityService) -> AccessControlService:
    return AccessControlService(db, clock=clock, identity=identity)


@pytest.fixture
def seller(identity: IdentityService, admin: Actor) -> uuid.UUID:
    user = identity.create_user(
        admin,
        username="counter1",
        full_name="E. Mensah",
        password=STAFF,
        role_codes=["sales_person"],
        must_change_password=False,
    )
    return user.id


def signed_in(identity: IdentityService, admin: Actor, username: str = "counter1") -> Actor:
    assert admin.business_id is not None
    return identity.sign_in(username, STAFF, business_id=admin.business_id).actor


def policy(**changes: object) -> AccessPolicyTerms:
    values: dict[str, object] = {
        "minimum_length": 10,
        "require_uppercase": True,
        "require_lowercase": True,
        "require_digit": True,
        "require_symbol": False,
        "reuse_window": 5,
        "lockout_threshold": 5,
        "lockout_minutes": 15,
        "idle_minutes": 30,
        "absolute_hours": 12,
    }
    values.update(changes)
    return AccessPolicyTerms(**values)  # type: ignore[arg-type]


class TestRoles:
    def test_a_role_copied_from_another_starts_with_its_permissions(
        self, access: AccessControlService, admin: Actor, identity: IdentityService
    ) -> None:
        role = access.create_role(
            admin, name="Senior Sales Person", description="Counter lead", copy_from="sales_person"
        )
        assert role.code == "senior_sales_person" and not role.is_system
        copied = {item.permission_code for item in role.permissions}
        assert "pos.operate" in copied and "pos.discount" not in copied
        identity.set_role_permissions(
            admin, role.code, copied | {"pos.discount"}, reason="Leads may discount"
        )

    def test_names_are_unique(self, access: AccessControlService, admin: Actor) -> None:
        access.create_role(admin, name="Stores Clerk")
        with pytest.raises(ConflictError):
            access.create_role(admin, name="stores  clerk")

    def test_a_supplied_role_is_never_removed(
        self, access: AccessControlService, admin: Actor
    ) -> None:
        with pytest.raises(ConflictError, match="supplied"):
            access.remove_role(admin, "sales_person", reason="Tidy up")

    def test_a_role_someone_holds_is_not_removed(
        self,
        access: AccessControlService,
        admin: Actor,
        identity: IdentityService,
        seller: uuid.UUID,
    ) -> None:
        role = access.create_role(admin, name="Stores Clerk")
        identity.assign_role(admin, seller, role.code)
        assert access.holders(admin)[role.id] == 1
        with pytest.raises(ConflictError, match="hold"):
            access.remove_role(admin, role.code, reason="Unused")
        identity.withdraw_role(admin, seller, role.code)
        access.remove_role(admin, role.code, reason="Unused")
        assert role.id not in access.holders(admin)

    def test_creating_a_role_needs_role_management(
        self, access: AccessControlService, admin: Actor
    ) -> None:
        clerk = make_actor(admin.business_id, permissions={"user.view"})
        with pytest.raises(PermissionDeniedError):
            access.create_role(clerk, name="Anything")


class TestExceptions:
    def test_a_grant_and_a_withholding_change_what_the_person_may_do(
        self,
        access: AccessControlService,
        admin: Actor,
        identity: IdentityService,
        seller: uuid.UUID,
    ) -> None:
        before = signed_in(identity, admin)
        assert not before.has("pos.discount") and before.has("pos.shift_manage")
        found = access.set_overrides(
            admin,
            seller,
            {"pos.discount": OverrideEffect.GRANT, "pos.shift_manage": "withhold"},
            reason="Trusted with discounts; shifts are opened by the supervisor",
        )
        assert {item.permission_code for item in found.overrides} == {
            "pos.discount",
            "pos.shift_manage",
        }
        after = signed_in(identity, admin)
        assert after.has("pos.discount") and not after.has("pos.shift_manage")
        assert "pos.discount" in found.effective and "pos.shift_manage" not in found.effective

        cleared = access.set_overrides(admin, seller, {}, reason="Back to the role")
        assert cleared.overrides == []
        assert not signed_in(identity, admin).has("pos.discount")

    def test_nobody_amends_their_own_access(
        self,
        access: AccessControlService,
        admin: Actor,
        identity: IdentityService,
        seller: uuid.UUID,
    ) -> None:
        identity.assign_role(admin, seller, "administrator")
        other = signed_in(identity, admin)
        with pytest.raises(ConflictError, match="Your own access"):
            access.set_overrides(other, seller, {"pos.discount": "grant"}, reason="Myself")

    def test_an_administrator_takes_no_exceptions(
        self,
        access: AccessControlService,
        admin: Actor,
        identity: IdentityService,
        seller: uuid.UUID,
    ) -> None:
        identity.assign_role(admin, seller, "administrator")
        with pytest.raises(ConflictError, match="administrator"):
            access.set_overrides(admin, seller, {"pos.discount": "withhold"}, reason="Narrow")

    def test_an_exception_must_differ_from_the_roles(
        self, access: AccessControlService, admin: Actor, seller: uuid.UUID
    ) -> None:
        with pytest.raises(ValidationError, match="already comes"):
            access.set_overrides(admin, seller, {"pos.operate": "grant"}, reason="x")
        with pytest.raises(ValidationError, match="does not come"):
            access.set_overrides(admin, seller, {"pos.discount": "withhold"}, reason="x")
        with pytest.raises(ValidationError):
            access.set_overrides(admin, seller, {"pos.discount": "grant"}, reason=" ")

    def test_exceptions_need_role_management(
        self, access: AccessControlService, admin: Actor, seller: uuid.UUID
    ) -> None:
        manager = make_actor(admin.business_id, permissions={"user.view", "user.manage"})
        with pytest.raises(PermissionDeniedError):
            access.set_overrides(manager, seller, {"pos.discount": "grant"}, reason="x")


class TestSessions:
    def test_sessions_are_listed_and_ended(
        self,
        access: AccessControlService,
        admin: Actor,
        identity: IdentityService,
        seller: uuid.UUID,
    ) -> None:
        signed_in(identity, admin)
        signed_in(identity, admin)
        assert len(access.sessions(admin, seller)) == 2
        assert access.sign_out_everywhere(admin, seller, reason="Device lost") == 2
        assert access.sessions(admin, seller) == []


class TestSignInPolicy:
    def test_the_defaults_apply_until_the_business_sets_its_own(
        self, access: AccessControlService, admin: Actor
    ) -> None:
        terms, stored = access.policy(admin)
        assert not stored and terms.minimum_length == 10

    def test_a_longer_minimum_applies_to_new_passwords(
        self, access: AccessControlService, admin: Actor, identity: IdentityService
    ) -> None:
        access.set_policy(admin, policy(minimum_length=16), reason="Audit recommendation")
        assert access.policy(admin)[1]
        with pytest.raises(ValidationError, match="16"):
            identity.create_user(
                admin,
                username="counter2",
                full_name="K. Owusu",
                password=STAFF,
                role_codes=["sales_person"],
            )

    def test_the_lockout_threshold_is_the_business_s(
        self,
        access: AccessControlService,
        admin: Actor,
        identity: IdentityService,
        seller: uuid.UUID,
    ) -> None:
        access.set_policy(admin, policy(lockout_threshold=3), reason="Shared counter")
        assert admin.business_id is not None
        for _ in range(3):
            with pytest.raises(AuthenticationError):
                identity.sign_in("counter1", "Wrong4Password", business_id=admin.business_id)
        with pytest.raises(AccountLockedError):
            identity.sign_in("counter1", STAFF, business_id=admin.business_id)

    def test_an_idle_session_expires_at_the_business_s_limit(
        self,
        access: AccessControlService,
        admin: Actor,
        identity: IdentityService,
        seller: uuid.UUID,
        clock: FixedClock,
    ) -> None:
        access.set_policy(admin, policy(idle_minutes=5), reason="Counter left unattended")
        assert admin.business_id is not None
        token = identity.sign_in("counter1", STAFF, business_id=admin.business_id).token
        clock.advance(4 * 60)
        identity.resolve_session(token)
        clock.advance(6 * 60)
        with pytest.raises(AuthenticationError, match="expired"):
            identity.resolve_session(token)

    def test_an_unsafe_policy_is_refused(self, access: AccessControlService, admin: Actor) -> None:
        with pytest.raises(ValidationError, match="8 and 64"):
            access.set_policy(admin, policy(minimum_length=6), reason="Shorter")
        with pytest.raises(ValidationError, match="outlast"):
            access.set_policy(admin, policy(idle_minutes=180, absolute_hours=2), reason="Long idle")
        with pytest.raises(ValidationError, match="reason"):
            access.set_policy(admin, policy(), reason=" ")
