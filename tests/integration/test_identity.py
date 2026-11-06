"""Authentication, sessions, lockout and user administration. PRD 9.6, PRD 10."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nickland.application.audit import AuditAction
from nickland.application.context import Actor
from nickland.application.services.identity import IdentityService
from nickland.domain.errors import (
    AccountLockedError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from nickland.domain.identity import LockoutPolicy
from nickland.persistence.models import AuditEvent, User
from nickland.persistence.models import Session as SessionRecord
from nickland.platform.clock import FixedClock
from tests.conftest import ADMIN_PASSWORD, make_actor

STAFF_PASSWORD = "Tamale4Harbour"


def _service(db: Session, clock: FixedClock, **kwargs: object) -> IdentityService:
    return IdentityService(db, clock=clock, **kwargs)  # type: ignore[arg-type]


class TestSignIn:
    def test_succeeds_with_correct_credentials(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        result = _service(db, clock).sign_in(
            "proprietor", ADMIN_PASSWORD, business_id=provisioned["business_id"]
        )
        assert result.actor.username == "proprietor"
        assert result.actor.is_administrator
        assert result.token
        assert not result.must_change_password

    def test_the_administrator_receives_every_permission(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        from nickland.domain.permissions import ALL_CODES

        result = _service(db, clock).sign_in(
            "proprietor", ADMIN_PASSWORD, business_id=provisioned["business_id"]
        )
        assert result.actor.permissions == ALL_CODES

    def test_username_is_matched_case_insensitively(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        result = _service(db, clock).sign_in(
            "  PROPRIETOR ", ADMIN_PASSWORD, business_id=provisioned["business_id"]
        )
        assert result.actor.username == "proprietor"

    @pytest.mark.parametrize(
        ("username", "password"),
        [("proprietor", "WrongPassword9"), ("nobody", ADMIN_PASSWORD), ("nobody", "wrong")],
    )
    def test_every_failure_returns_the_same_message(
        self,
        db: Session,
        clock: FixedClock,
        provisioned: dict[str, uuid.UUID],
        username: str,
        password: str,
    ) -> None:
        """The response must not disclose whether a username exists."""
        with pytest.raises(AuthenticationError) as caught:
            _service(db, clock).sign_in(username, password, business_id=provisioned["business_id"])
        assert str(caught.value) == "Username or password is incorrect."

    def test_a_failed_attempt_is_audited(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        with pytest.raises(AuthenticationError):
            _service(db, clock).sign_in(
                "proprietor", "WrongPassword9", business_id=provisioned["business_id"]
            )
        db.flush()
        events = db.scalars(
            select(AuditEvent).where(AuditEvent.action == AuditAction.SIGN_IN_FAILED.value)
        ).all()
        assert len(events) == 1
        assert events[0].actor_id is None
        assert events[0].entity_label == "proprietor"

    def test_a_successful_sign_in_is_audited(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        _service(db, clock).sign_in(
            "proprietor",
            ADMIN_PASSWORD,
            business_id=provisioned["business_id"],
            device="till-1",
        )
        db.flush()
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == AuditAction.SIGN_IN.value))
        assert event is not None
        assert event.device == "till-1"


class TestLockout:
    def test_locks_after_the_configured_threshold(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock, lockout_policy=LockoutPolicy(threshold=3))
        for _ in range(3):
            with pytest.raises(AuthenticationError):
                service.sign_in(
                    "proprietor", "WrongPassword9", business_id=provisioned["business_id"]
                )
        with pytest.raises(AccountLockedError) as caught:
            service.sign_in("proprietor", ADMIN_PASSWORD, business_id=provisioned["business_id"])
        assert caught.value.minutes_remaining == 15

    def test_the_lock_expires(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock, lockout_policy=LockoutPolicy(threshold=2))
        for _ in range(2):
            with pytest.raises(AuthenticationError):
                service.sign_in(
                    "proprietor", "WrongPassword9", business_id=provisioned["business_id"]
                )
        clock.advance(timedelta(minutes=16).total_seconds())
        result = service.sign_in(
            "proprietor", ADMIN_PASSWORD, business_id=provisioned["business_id"]
        )
        assert result.actor.username == "proprietor"

    def test_a_correct_password_clears_the_counter(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock, lockout_policy=LockoutPolicy(threshold=3))
        with pytest.raises(AuthenticationError):
            service.sign_in("proprietor", "WrongPassword9", business_id=provisioned["business_id"])
        service.sign_in("proprietor", ADMIN_PASSWORD, business_id=provisioned["business_id"])
        user = db.scalar(select(User).where(User.username == "proprietor"))
        assert user is not None
        assert user.failed_attempts == 0

    def test_locking_is_audited(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock, lockout_policy=LockoutPolicy(threshold=2))
        for _ in range(2):
            with pytest.raises(AuthenticationError):
                service.sign_in(
                    "proprietor", "WrongPassword9", business_id=provisioned["business_id"]
                )
        db.flush()
        assert (
            db.scalar(
                select(AuditEvent).where(AuditEvent.action == AuditAction.ACCOUNT_LOCKED.value)
            )
            is not None
        )


class TestSessions:
    def test_a_token_resolves_to_its_actor(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock)
        result = service.sign_in(
            "proprietor", ADMIN_PASSWORD, business_id=provisioned["business_id"]
        )
        actor = service.resolve_session(result.token)
        assert actor.user_id == result.actor.user_id

    def test_the_raw_token_is_never_stored(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        result = _service(db, clock).sign_in(
            "proprietor", ADMIN_PASSWORD, business_id=provisioned["business_id"]
        )
        record = db.scalar(select(SessionRecord))
        assert record is not None
        assert record.token_hash != result.token
        assert result.token not in record.token_hash

    def test_an_unknown_token_is_refused(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        with pytest.raises(AuthenticationError, match="not valid"):
            _service(db, clock).resolve_session("not-a-real-token")

    def test_an_idle_session_expires(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock)
        result = service.sign_in(
            "proprietor", ADMIN_PASSWORD, business_id=provisioned["business_id"]
        )
        clock.advance(timedelta(minutes=31).total_seconds())
        with pytest.raises(AuthenticationError, match="expired"):
            service.resolve_session(result.token)

    def test_activity_refreshes_the_idle_deadline(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock)
        result = service.sign_in(
            "proprietor", ADMIN_PASSWORD, business_id=provisioned["business_id"]
        )
        for _ in range(4):
            clock.advance(timedelta(minutes=20).total_seconds())
            service.resolve_session(result.token)

    def test_the_absolute_limit_is_enforced_despite_activity(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock)
        result = service.sign_in(
            "proprietor", ADMIN_PASSWORD, business_id=provisioned["business_id"]
        )
        for _ in range(40):
            clock.advance(timedelta(minutes=20).total_seconds())
            try:
                service.resolve_session(result.token)
            except AuthenticationError:
                return
        pytest.fail("the absolute session limit was not enforced")

    def test_sign_out_revokes_the_session(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock)
        result = service.sign_in(
            "proprietor", ADMIN_PASSWORD, business_id=provisioned["business_id"]
        )
        service.sign_out(result.actor)
        with pytest.raises(AuthenticationError):
            service.resolve_session(result.token)


class TestPasswordChange:
    def test_changes_the_password_and_revokes_sessions(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock)
        result = service.sign_in(
            "proprietor", ADMIN_PASSWORD, business_id=provisioned["business_id"]
        )
        service.change_password(
            result.actor, current_password=ADMIN_PASSWORD, new_password="Kumasi8Ledger"
        )
        with pytest.raises(AuthenticationError):
            service.resolve_session(result.token)
        assert service.sign_in(
            "proprietor", "Kumasi8Ledger", business_id=provisioned["business_id"]
        )

    def test_refuses_an_incorrect_current_password(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        with pytest.raises(AuthenticationError, match="Current password"):
            _service(db, clock).change_password(
                admin, current_password="WrongPassword9", new_password="Kumasi8Ledger"
            )

    def test_refuses_reuse_of_a_recent_password(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        service = _service(db, clock)
        service.change_password(
            admin, current_password=ADMIN_PASSWORD, new_password="Kumasi8Ledger"
        )
        with pytest.raises(ValidationError, match="used recently"):
            service.change_password(
                admin, current_password="Kumasi8Ledger", new_password=ADMIN_PASSWORD
            )

    def test_a_reset_requires_a_reason_and_forces_a_change(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        service = _service(db, clock)
        staff = service.create_user(
            admin,
            username="seller1",
            full_name="A. Cashier",
            password=STAFF_PASSWORD,
            role_codes=["sales_person"],
        )
        with pytest.raises(ValidationError, match="reason is required"):
            service.reset_password(admin, staff.id, "Kumasi8Ledger", reason="  ")
        service.reset_password(admin, staff.id, "Kumasi8Ledger", reason="Forgotten password")
        assert staff.must_change_password


class TestUserAdministration:
    def test_creates_a_user_with_the_permissions_of_its_role(
        self, db: Session, clock: FixedClock, admin: Actor, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock)
        service.create_user(
            admin,
            username="seller1",
            full_name="A. Cashier",
            password=STAFF_PASSWORD,
            role_codes=["sales_person"],
        )
        result = service.sign_in("seller1", STAFF_PASSWORD, business_id=provisioned["business_id"])
        assert result.must_change_password
        assert result.actor.has("pos.operate")
        assert not result.actor.has("pos.refund")
        assert not result.actor.has("costing.view")

    def test_creating_a_user_requires_the_permission(
        self, db: Session, clock: FixedClock, provisioned: dict[str, uuid.UUID]
    ) -> None:
        """PRD 10: permissions are enforced in the service, not by hiding controls."""
        weak = make_actor(provisioned["business_id"], permissions={"user.view"})
        with pytest.raises(PermissionDeniedError, match=r"user\.manage"):
            _service(db, clock).create_user(
                weak,
                username="intruder",
                full_name="Intruder",
                password=STAFF_PASSWORD,
                role_codes=["sales_person"],
            )

    def test_refuses_a_duplicate_username(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        with pytest.raises(ConflictError, match="already in use"):
            _service(db, clock).create_user(
                admin,
                username="proprietor",
                full_name="Impostor",
                password=STAFF_PASSWORD,
                role_codes=["sales_person"],
            )

    def test_requires_at_least_one_role(self, db: Session, clock: FixedClock, admin: Actor) -> None:
        with pytest.raises(ValidationError, match="At least one role"):
            _service(db, clock).create_user(
                admin,
                username="roleless",
                full_name="No Role",
                password=STAFF_PASSWORD,
                role_codes=[],
            )

    def test_deactivation_requires_a_reason(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        service = _service(db, clock)
        staff = service.create_user(
            admin,
            username="seller1",
            full_name="A. Cashier",
            password=STAFF_PASSWORD,
            role_codes=["sales_person"],
        )
        with pytest.raises(ValidationError, match="reason is required"):
            service.set_active(admin, staff.id, active=False, reason="")

    def test_deactivation_revokes_live_sessions(
        self, db: Session, clock: FixedClock, admin: Actor, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock)
        staff = service.create_user(
            admin,
            username="seller1",
            full_name="A. Cashier",
            password=STAFF_PASSWORD,
            role_codes=["sales_person"],
        )
        signed_in = service.sign_in(
            "seller1", STAFF_PASSWORD, business_id=provisioned["business_id"]
        )
        service.set_active(admin, staff.id, active=False, reason="Left employment")
        with pytest.raises(AuthenticationError):
            service.resolve_session(signed_in.token)

    def test_an_inactive_user_cannot_sign_in(
        self, db: Session, clock: FixedClock, admin: Actor, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock)
        staff = service.create_user(
            admin,
            username="seller1",
            full_name="A. Cashier",
            password=STAFF_PASSWORD,
            role_codes=["sales_person"],
        )
        service.set_active(admin, staff.id, active=False, reason="Left employment")
        with pytest.raises(AuthenticationError):
            service.sign_in("seller1", STAFF_PASSWORD, business_id=provisioned["business_id"])

    def test_cannot_deactivate_own_account(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        with pytest.raises(ConflictError, match="your own account"):
            _service(db, clock).set_active(admin, admin.user_id, active=False, reason="Mistake")

    def test_cannot_withdraw_the_last_administrator_role(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        """A business must never be left without an administrator."""
        with pytest.raises(ConflictError, match="only active administrator"):
            _service(db, clock).withdraw_role(admin, admin.user_id, "administrator")

    def test_the_last_role_cannot_be_withdrawn(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        service = _service(db, clock)
        staff = service.create_user(
            admin,
            username="seller1",
            full_name="A. Cashier",
            password=STAFF_PASSWORD,
            role_codes=["sales_person"],
        )
        with pytest.raises(ConflictError, match="at least one role"):
            service.withdraw_role(admin, staff.id, "sales_person")

    def test_assigning_a_second_role_widens_permissions(
        self, db: Session, clock: FixedClock, admin: Actor, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock)
        staff = service.create_user(
            admin,
            username="seller1",
            full_name="A. Cashier",
            password=STAFF_PASSWORD,
            role_codes=["sales_person"],
        )
        service.assign_role(admin, staff.id, "sales_person")
        result = service.sign_in("seller1", STAFF_PASSWORD, business_id=provisioned["business_id"])
        assert result.actor.has("pos.operate")
        assert result.actor.has("quotation.issue")

    def test_unlocking_clears_the_lock(
        self, db: Session, clock: FixedClock, admin: Actor, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock, lockout_policy=LockoutPolicy(threshold=1))
        staff = service.create_user(
            admin,
            username="seller1",
            full_name="A. Cashier",
            password=STAFF_PASSWORD,
            role_codes=["sales_person"],
        )
        with pytest.raises(AuthenticationError):
            service.sign_in("seller1", "Wrong9Password", business_id=provisioned["business_id"])
        with pytest.raises(AccountLockedError):
            service.sign_in("seller1", STAFF_PASSWORD, business_id=provisioned["business_id"])
        service.unlock(admin, staff.id)
        assert service.sign_in("seller1", STAFF_PASSWORD, business_id=provisioned["business_id"])

    def test_an_unknown_user_is_not_found(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        with pytest.raises(NotFoundError):
            _service(db, clock).amend_user(admin, uuid.uuid4(), full_name="Nobody")


class TestRolePermissions:
    def test_amending_a_role_requires_a_reason(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        with pytest.raises(ValidationError, match="reason is required"):
            _service(db, clock).set_role_permissions(
                admin, "sales_person", {"pos.operate"}, reason=""
            )

    def test_an_unregistered_permission_is_refused(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        with pytest.raises(KeyError, match="Unregistered"):
            _service(db, clock).set_role_permissions(
                admin, "sales_person", {"pos.operate", "made.up"}, reason="Test"
            )

    def test_permissions_can_be_widened(
        self, db: Session, clock: FixedClock, admin: Actor, provisioned: dict[str, uuid.UUID]
    ) -> None:
        service = _service(db, clock)
        service.set_role_permissions(
            admin,
            "sales_person",
            {"pos.operate", "pos.refund", "business.view", "product.view"},
            reason="Till supervisor duties",
        )
        staff = service.create_user(
            admin,
            username="seller1",
            full_name="A. Cashier",
            password=STAFF_PASSWORD,
            role_codes=["sales_person"],
        )
        result = service.sign_in("seller1", STAFF_PASSWORD, business_id=provisioned["business_id"])
        assert result.actor.has("pos.refund")
        assert staff.id

    def test_the_administrator_role_cannot_be_narrowed(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        with pytest.raises(ConflictError, match="cannot have permissions withdrawn"):
            _service(db, clock).set_role_permissions(
                admin, "administrator", {"business.view"}, reason="Test"
            )
