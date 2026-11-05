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


def test_eight_roles_are_seeded_per_prd_3() -> None:
    assert len(SEEDED_ROLES) == 8
    assert set(ROLES_BY_CODE) == {
        "owner_administrator", "sales_officer", "purchasing_officer", "store_officer",
        "dispatch_officer", "cashier", "accountant", "auditor",
    }


def test_administrator_holds_every_permission() -> None:
    assert ROLES_BY_CODE["owner_administrator"].permissions == ALL_CODES


def test_auditor_is_read_only() -> None:
    """PRD 3: the auditor has read-only access to records, documents and the audit trail."""
    auditor = ROLES_BY_CODE["auditor"]
    assert auditor.is_read_only
    assert all(code.endswith((".view", ".export")) for code in auditor.permissions)
    assert "audit.view" in auditor.permissions


@pytest.mark.parametrize(
    "code",
    ["journal.post", "period.close", "debt.write_off", "loan.restructure",
     "tax_rule.override", "pos.refund", "stock.adjust", "costing.view",
     "report.export_sensitive", "template.publish"],
)
def test_sensitive_permissions_are_marked(code: str) -> None:
    """PRD 29.9 requires separate control of capabilities that disclose or bypass."""
    assert permission(code).sensitive


def test_costing_view_is_withheld_from_customer_facing_roles() -> None:
    """PRD 10: supplier cost and margin are protected from unauthorised roles."""
    for role_code in ("sales_officer", "cashier", "dispatch_officer", "store_officer"):
        assert "costing.view" not in ROLES_BY_CODE[role_code].permissions


def test_cashier_cannot_authorise_its_own_refund() -> None:
    """PRD 6.10: refunds and voids require authorisation."""
    cashier = ROLES_BY_CODE["cashier"]
    assert "pos.operate" in cashier.permissions
    assert "pos.refund" not in cashier.permissions
    assert "pos.price_override" not in cashier.permissions
    assert "pos.shift_approve" not in cashier.permissions


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
