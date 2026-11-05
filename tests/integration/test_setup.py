"""First-run provisioning. PRD 9.6 forces administrator provisioning before data entry."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nickland.application.services.setup import SetupRequest, SetupService
from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.organisation import DocumentKind
from nickland.domain.permissions import SEEDED_ROLES
from nickland.persistence.models import (
    Branch,
    Business,
    NumberSequence,
    Role,
    RolePermission,
    ThemeProfile,
    User,
    Warehouse,
)
from nickland.platform.clock import FixedClock
from tests.conftest import ADMIN_PASSWORD


def _request(**overrides: object) -> SetupRequest:
    values: dict[str, object] = {
        "business_name": "Nicholas Nickland Enterprise",
        "administrator_username": "proprietor",
        "administrator_full_name": "N. Nickland",
        "administrator_password": ADMIN_PASSWORD,
        "theme_preset": "charcoal",
    }
    values.update(overrides)
    return SetupRequest(**values)  # type: ignore[arg-type]


def test_setup_is_required_on_a_fresh_installation(db: Session, clock: FixedClock) -> None:
    assert SetupService(db, clock=clock).is_required()


def test_both_presets_are_offered_with_no_default(db: Session, clock: FixedClock) -> None:
    """ADR-0002: the administrator chooses; the product does not impose one."""
    presets = SetupService(db, clock=clock).available_presets()
    assert {item["code"] for item in presets} == {"charcoal", "navy"}
    assert all("is_default" not in item for item in presets)


class TestProvisioning:
    def test_creates_the_business(self, db: Session, clock: FixedClock) -> None:
        result = SetupService(db, clock=clock).provision(_request())
        business = db.get(Business, result.business_id)
        assert business is not None
        assert business.name == "Nicholas Nickland Enterprise"
        assert business.base_currency == "GHS"
        assert business.timezone == "Africa/Accra"
        assert business.setup_completed_at == clock.now()

    def test_creates_a_primary_branch_and_default_warehouse(
        self, db: Session, clock: FixedClock
    ) -> None:
        result = SetupService(db, clock=clock).provision(_request())
        branch = db.get(Branch, result.branch_id)
        warehouse = db.get(Warehouse, result.warehouse_id)
        assert branch is not None and branch.is_primary
        assert warehouse is not None and warehouse.is_default
        assert warehouse.branch_fk == branch.id

    def test_seeds_the_eight_roles_of_prd_3(self, db: Session, clock: FixedClock) -> None:
        result = SetupService(db, clock=clock).provision(_request())
        assert result.roles_created == 8
        roles = db.scalars(select(Role).where(Role.business_id == result.business_id)).all()
        assert {role.code for role in roles} == {item.code for item in SEEDED_ROLES}
        assert all(role.is_system for role in roles)

    def test_grants_each_role_its_permissions(self, db: Session, clock: FixedClock) -> None:
        result = SetupService(db, clock=clock).provision(_request())
        for definition in SEEDED_ROLES:
            role = db.scalar(
                select(Role).where(
                    Role.business_id == result.business_id, Role.code == definition.code
                )
            )
            assert role is not None
            granted = {item.permission_code for item in role.permissions}
            assert granted == definition.permissions

    def test_seeds_a_numbering_sequence_per_document_kind(
        self, db: Session, clock: FixedClock
    ) -> None:
        result = SetupService(db, clock=clock).provision(_request())
        assert result.sequences_created == len(DocumentKind)
        sequences = db.scalars(
            select(NumberSequence).where(NumberSequence.business_id == result.business_id)
        ).all()
        assert {item.document_kind for item in sequences} == {k.value for k in DocumentKind}
        assert all(item.next_value == 1 for item in sequences)

    def test_records_the_chosen_theme(self, db: Session, clock: FixedClock) -> None:
        result = SetupService(db, clock=clock).provision(_request(theme_preset="navy"))
        profile = db.scalar(
            select(ThemeProfile).where(ThemeProfile.business_id == result.business_id)
        )
        assert profile is not None
        assert profile.preset_code == "navy"
        assert profile.overrides == {}

    def test_creates_an_administrator_who_need_not_change_the_password(
        self, db: Session, clock: FixedClock
    ) -> None:
        result = SetupService(db, clock=clock).provision(_request())
        administrator = db.get(User, result.administrator_id)
        assert administrator is not None
        assert administrator.username == "proprietor"
        assert administrator.is_active
        assert not administrator.must_change_password
        assert any(role.is_administrator for role in administrator.roles)

    def test_the_password_is_never_stored_in_the_clear(
        self, db: Session, clock: FixedClock
    ) -> None:
        result = SetupService(db, clock=clock).provision(_request())
        administrator = db.get(User, result.administrator_id)
        assert administrator is not None
        assert ADMIN_PASSWORD not in administrator.password_hash
        assert administrator.password_hash.startswith("$argon2id$")

    def test_extra_branches_are_created(self, db: Session, clock: FixedClock) -> None:
        result = SetupService(db, clock=clock).provision(
            _request(extra_branches=[("tamale", "Tamale depot"), ("takoradi", "Takoradi depot")])
        )
        branches = db.scalars(select(Branch).where(Branch.business_id == result.business_id)).all()
        assert len(branches) == 3
        assert sum(1 for branch in branches if branch.is_primary) == 1

    def test_runs_only_once(self, db: Session, clock: FixedClock) -> None:
        service = SetupService(db, clock=clock)
        service.provision(_request())
        db.flush()
        assert not service.is_required()
        with pytest.raises(ConflictError, match="already been set up"):
            service.provision(_request())


class TestValidation:
    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"business_name": "   "}, "Business name is required"),
            ({"theme_preset": "teal"}, "Unknown theme preset"),
            ({"administrator_username": "x"}, "Username must be"),
            ({"administrator_password": "short"}, "at least 10"),
            ({"base_currency": "GHANA"}, "three-letter code"),
            ({"currency_precision": 9}, "between 0 and 6"),
            ({"financial_year_start_month": 13}, "between 1 and 12"),
            ({"branch_code": "!!"}, "Code must be"),
        ],
    )
    def test_rejects_invalid_input(
        self, db: Session, clock: FixedClock, overrides: dict[str, object], message: str
    ) -> None:
        with pytest.raises(ValidationError, match=message):
            SetupService(db, clock=clock).provision(_request(**overrides))

    def test_a_rejected_setup_leaves_nothing_behind(self, db: Session, clock: FixedClock) -> None:
        """A failed provisioning must not leave a half-built business."""
        with pytest.raises(ValidationError):
            SetupService(db, clock=clock).provision(_request(administrator_password="short"))
        db.rollback()
        assert db.scalar(select(func.count()).select_from(Business)) == 0
        assert db.scalar(select(func.count()).select_from(RolePermission)) == 0
