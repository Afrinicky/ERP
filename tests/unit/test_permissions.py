"""Permission registry and seeded roles. PRD 3 and PRD 10."""

from __future__ import annotations

import pytest

from nickland.domain.permissions import (
    ALL_CODES,
    REGISTRY,
    ROLES_BY_CODE,
    SEEDED_ROLES,
    permission,
    require_registered,
)


def test_codes_are_unique() -> None:
    assert len({item.code for item in REGISTRY}) == len(REGISTRY)


def test_every_code_is_verb_against_resource() -> None:
    for item in REGISTRY:
        assert "." in item.code, item.code
        resource, _, verb = item.code.rpartition(".")
        assert resource and verb


def test_two_roles_are_seeded() -> None:
    """The two this business has. Further roles are created under Settings."""
    assert len(SEEDED_ROLES) == 2
    assert set(ROLES_BY_CODE) == {
        "administrator",
        "sales_person",
    }


def test_administrator_holds_every_permission() -> None:
    assert ROLES_BY_CODE["administrator"].permissions == ALL_CODES
    assert ROLES_BY_CODE["administrator"].is_administrator


def test_the_registry_still_covers_the_roles_that_are_not_seeded() -> None:
    """PRD 3 names eight job functions; two are seeded and six are left to Settings.

    Not seeding them must not mean they cannot be built, so the permissions each
    would need are still registered.
    """
    for code in (
        "purchase_order.manage",
        "goods_receipt.manage",
        "dispatch.manage",
        "pos.refund",
        "journal.post",
        "audit.view",
    ):
        assert permission(code)


@pytest.mark.parametrize(
    "code",
    [
        "journal.post",
        "period.close",
        "debt.write_off",
        "loan.restructure",
        "tax_rule.override",
        "pos.refund",
        "stock.adjust",
        "costing.view",
        "report.export_sensitive",
        "template.publish",
    ],
)
def test_sensitive_permissions_are_marked(code: str) -> None:
    """PRD 29.9 requires separate control of capabilities that disclose or bypass."""
    assert permission(code).sensitive


def test_costing_view_is_withheld_from_the_sales_person() -> None:
    """PRD 10: supplier cost and margin are protected from unauthorised roles."""
    assert "costing.view" not in ROLES_BY_CODE["sales_person"].permissions
    assert "report.export_sensitive" not in ROLES_BY_CODE["sales_person"].permissions


def test_the_sales_person_operates_the_counter_but_authorises_nothing() -> None:
    """PRD 6.10: refunds, voids and price overrides require authorisation.

    With no separate cashier role the sales person works the till, so the question
    of what they may do there without the proprietor is the one that matters.
    """
    sales = ROLES_BY_CODE["sales_person"]
    assert "pos.operate" in sales.permissions
    for withheld in ("pos.refund", "pos.price_override", "pos.shift_approve", "pos.discount"):
        assert withheld not in sales.permissions


def test_no_role_but_the_administrator_can_manage_roles() -> None:
    for role in SEEDED_ROLES:
        if not role.is_administrator:
            assert "role.manage" not in role.permissions


def test_unknown_permission_raises() -> None:
    with pytest.raises(KeyError, match="Unregistered permission"):
        permission("nonexistent.permission")
    with pytest.raises(KeyError, match="Unregistered permissions"):
        require_registered({"journal.post", "made.up"})


def test_every_role_permission_is_registered() -> None:
    for role in SEEDED_ROLES:
        require_registered(role.permissions)
