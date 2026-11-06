"""Permission registry and seeded roles.

A permission is a verb against a resource. The registry is explicit: no permission is
implied by another, per PRD 10. Permissions are enforced in the application layer;
hiding an interface control is a convenience and never a control.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class PermissionGroup(StrEnum):
    """Grouping used only for presentation in the permissions administration screen."""

    ADMINISTRATION = "Administration"
    MASTER_DATA = "Master data"
    SALES = "Sales"
    POINT_OF_SALE = "Point of sale"
    PURCHASING = "Purchasing"
    INVENTORY = "Inventory"
    DISPATCH = "Dispatch"
    TAXATION = "Taxation"
    FINANCE = "Finance"
    FINANCING = "Debt and financing"
    DOCUMENTS = "Documents"
    REPORTING = "Reporting"


@dataclass(frozen=True, slots=True)
class Permission:
    """One capability."""

    code: str
    name: str
    group: PermissionGroup
    description: str
    sensitive: bool = False
    """Marks a permission that discloses commercial confidence or bypasses a control."""


def _permission(
    code: str,
    name: str,
    group: PermissionGroup,
    description: str,
    *,
    sensitive: bool = False,
) -> Permission:
    return Permission(
        code=code, name=name, group=group, description=description, sensitive=sensitive
    )


_A = PermissionGroup.ADMINISTRATION
_M = PermissionGroup.MASTER_DATA
_S = PermissionGroup.SALES
_P = PermissionGroup.POINT_OF_SALE
_U = PermissionGroup.PURCHASING
_I = PermissionGroup.INVENTORY
_D = PermissionGroup.DISPATCH
_T = PermissionGroup.TAXATION
_F = PermissionGroup.FINANCE
_G = PermissionGroup.FINANCING
_C = PermissionGroup.DOCUMENTS
_R = PermissionGroup.REPORTING

REGISTRY: Final[tuple[Permission, ...]] = (
    # Administration
    _permission(
        "business.view", "View business settings", _A, "Read business identity and configuration."
    ),
    _permission(
        "business.manage",
        "Manage business settings",
        _A,
        "Amend business identity, branches and warehouses.",
    ),
    _permission("user.view", "View users", _A, "List users and their roles."),
    _permission(
        "user.manage", "Manage users", _A, "Create, amend and deactivate users.", sensitive=True
    ),
    _permission(
        "role.manage", "Manage roles and permissions", _A, "Amend role definitions.", sensitive=True
    ),
    _permission(
        "theme.manage", "Manage appearance", _A, "Select a preset and amend brand colours."
    ),
    _permission("numbering.manage", "Manage document numbering", _A, "Amend numbering sequences."),
    _permission(
        "custom_field.manage", "Manage custom fields", _A, "Define custom fields on records."
    ),
    _permission("audit.view", "View audit trail", _A, "Read the audit trail."),
    _permission(
        "backup.manage",
        "Manage backup and restore",
        _A,
        "Run, configure and restore backups.",
        sensitive=True,
    ),
    _permission(
        "health.view", "View system health", _A, "Read service, database and backup health."
    ),
    # Master data
    _permission("product.view", "View catalogue", _M, "Read products, services and categories."),
    _permission("product.manage", "Manage catalogue", _M, "Create and amend catalogue records."),
    _permission("customer.view", "View customers", _M, "Read customer records and balances."),
    _permission("customer.manage", "Manage customers", _M, "Create and amend customer records."),
    _permission("supplier.view", "View suppliers", _M, "Read supplier records and balances."),
    _permission("supplier.manage", "Manage suppliers", _M, "Create and amend supplier records."),
    _permission("affiliate.view", "View affiliates", _M, "Read referrer records and accruals."),
    _permission(
        "affiliate.manage",
        "Manage affiliates",
        _M,
        "Create and amend referrer records and rates.",
        sensitive=True,
    ),
    _permission(
        "price_list.manage", "Manage price lists", _M, "Amend price lists and customer pricing."
    ),
    # Sales
    _permission("rfq.view", "View requests for quotation", _S, "Read customer requests."),
    _permission(
        "rfq.manage", "Manage requests for quotation", _S, "Capture and amend customer requests."
    ),
    _permission("quotation.view", "View quotations", _S, "Read quotations and pro formas."),
    _permission("quotation.manage", "Manage quotations", _S, "Create and amend draft quotations."),
    _permission("quotation.issue", "Issue quotations", _S, "Issue a quotation to a customer."),
    _permission(
        "quotation.approve", "Approve quotations", _S, "Approve a quotation pending approval."
    ),
    _permission(
        "costing.view",
        "View internal costing",
        _S,
        "Read supplier cost, margin and commission.",
        sensitive=True,
    ),
    _permission(
        "discount.approve",
        "Approve discounts",
        _S,
        "Approve a discount beyond the actor's own limit.",
    ),
    _permission("sales_order.manage", "Manage sales orders", _S, "Create and amend sales orders."),
    _permission("sales_invoice.issue", "Issue sales invoices", _S, "Issue a sales invoice."),
    _permission(
        "sales_return.manage", "Manage sales returns", _S, "Record returns and credit notes."
    ),
    # Point of sale
    _permission("pos.operate", "Operate the register", _P, "Build a cart and complete a sale."),
    _permission(
        "pos.discount", "Apply register discounts", _P, "Apply a discount at the register."
    ),
    _permission(
        "pos.price_override",
        "Override register prices",
        _P,
        "Amend a price at the register.",
        sensitive=True,
    ),
    _permission(
        "pos.refund",
        "Authorise refunds and voids",
        _P,
        "Authorise a refund, void or exchange.",
        sensitive=True,
    ),
    _permission("pos.shift_manage", "Manage shifts", _P, "Open and close a cashier shift."),
    _permission(
        "pos.shift_approve",
        "Approve shift variance",
        _P,
        "Sign off a drawer variance.",
        sensitive=True,
    ),
    # Purchasing
    _permission(
        "supplier_rfq.manage",
        "Manage supplier requests",
        _U,
        "Issue supplier requests for quotation.",
    ),
    _permission(
        "supplier_quotation.manage",
        "Manage supplier quotations",
        _U,
        "Record and compare supplier quotations.",
    ),
    _permission(
        "purchase_order.manage", "Manage purchase orders", _U, "Create and amend purchase orders."
    ),
    _permission(
        "purchase_order.approve", "Approve purchase orders", _U, "Approve a purchase order."
    ),
    _permission(
        "supplier_invoice.manage",
        "Manage supplier invoices",
        _U,
        "Record supplier invoices and credit notes.",
    ),
    _permission(
        "match_variance.approve",
        "Approve matching variance",
        _U,
        "Approve a quantity or price variance.",
        sensitive=True,
    ),
    # Inventory
    _permission("stock.view", "View stock", _I, "Read stock balances and movements."),
    _permission(
        "goods_receipt.manage",
        "Record goods receipt",
        _I,
        "Record receipt against a purchase order.",
    ),
    _permission(
        "inspection.manage", "Record inspection", _I, "Record inspection outcomes and quarantine."
    ),
    _permission(
        "stock.adjust",
        "Adjust stock",
        _I,
        "Record an adjustment, write-off or transfer.",
        sensitive=True,
    ),
    _permission("stock_count.manage", "Manage stock counts", _I, "Conduct a stock count."),
    _permission(
        "stock_count.approve",
        "Approve count variance",
        _I,
        "Approve a count variance.",
        sensitive=True,
    ),
    _permission(
        "expiry.override",
        "Override expiry block",
        _I,
        "Issue stock past its expiry date.",
        sensitive=True,
    ),
    _permission(
        "assembly.manage", "Manage assembly", _I, "Assemble an order from stock and receipts."
    ),
    # Dispatch
    _permission(
        "dispatch.view", "View dispatch", _D, "Read picking, packing and delivery records."
    ),
    _permission("dispatch.manage", "Manage dispatch", _D, "Pick, pack and dispatch an order."),
    _permission("waybill.issue", "Issue waybills", _D, "Issue a waybill or delivery note."),
    _permission(
        "proof_of_delivery.capture",
        "Capture proof of delivery",
        _D,
        "Record acknowledgement of receipt.",
    ),
    # Taxation
    _permission("tax.view", "View taxation", _T, "Read tax rules, obligations and registers."),
    _permission(
        "tax_rule.manage",
        "Manage tax rules",
        _T,
        "Create, amend and withdraw tax rules.",
        sensitive=True,
    ),
    _permission(
        "tax_rule.override",
        "Override tax on a transaction",
        _T,
        "Record a reasoned tax override.",
        sensitive=True,
    ),
    _permission(
        "tax_payment.manage", "Record tax payments", _T, "Record a payment to an authority."
    ),
    _permission("tax_filing.manage", "Manage filings", _T, "Record a return or filing."),
    # Finance
    _permission("account.view", "View chart of accounts", _F, "Read accounts and balances."),
    _permission(
        "account.manage",
        "Manage chart of accounts",
        _F,
        "Create and amend accounts.",
        sensitive=True,
    ),
    _permission(
        "journal.view", "View journals", _F, "Read journal entries and the general ledger."
    ),
    _permission("journal.post", "Post journals", _F, "Post a journal entry.", sensitive=True),
    _permission(
        "journal.approve",
        "Approve journals",
        _F,
        "Approve a journal entry as checker.",
        sensitive=True,
    ),
    _permission(
        "journal.reverse", "Reverse journals", _F, "Reverse a posted entry.", sensitive=True
    ),
    _permission("period.close", "Close accounting periods", _F, "Close a period.", sensitive=True),
    _permission(
        "period.reopen", "Reopen accounting periods", _F, "Reopen a closed period.", sensitive=True
    ),
    _permission("expense.manage", "Manage expenses", _F, "Record and amend expenses."),
    _permission(
        "expense.approve", "Approve expenses", _F, "Approve an expense beyond its threshold."
    ),
    _permission(
        "receipt.manage", "Record customer receipts", _F, "Record and allocate a customer receipt."
    ),
    _permission("payment.manage", "Record payments", _F, "Record and allocate a payment."),
    _permission(
        "payment.approve",
        "Approve payments",
        _F,
        "Approve a payment beyond its threshold.",
        sensitive=True,
    ),
    _permission(
        "transfer.manage", "Record transfers", _F, "Transfer between the business's own accounts."
    ),
    _permission(
        "financial_account.manage",
        "Manage cash and bank accounts",
        _F,
        "Create and amend accounts.",
        sensitive=True,
    ),
    _permission(
        "reconciliation.manage", "Reconcile accounts", _F, "Match and reconcile account activity."
    ),
    _permission("receivable.view", "View receivables", _F, "Read customer balances and ageing."),
    _permission("payable.view", "View payables", _F, "Read supplier balances and ageing."),
    _permission(
        "debt.write_off",
        "Write off debt",
        _F,
        "Write off a balance or raise a provision.",
        sensitive=True,
    ),
    _permission("budget.manage", "Manage budgets", _F, "Create and amend budgets."),
    _permission("budget.approve", "Approve budgets", _F, "Approve a budget version."),
    # Debt and financing
    _permission(
        "loan.view", "View loans and financing", _G, "Read agreements, schedules and balances."
    ),
    _permission(
        "loan.manage",
        "Manage loans and financing",
        _G,
        "Create and amend agreements.",
        sensitive=True,
    ),
    _permission(
        "loan.approve", "Approve borrowing and lending", _G, "Approve an agreement.", sensitive=True
    ),
    _permission(
        "loan.restructure",
        "Restructure agreements",
        _G,
        "Reschedule or refinance an agreement.",
        sensitive=True,
    ),
    _permission("repayment.manage", "Record repayments", _G, "Record a repayment or drawdown."),
    # Documents
    _permission("document.view", "View documents", _C, "Read generated and uploaded documents."),
    _permission("document.issue", "Issue documents", _C, "Issue a document from a transaction."),
    _permission("document.reprint", "Reprint documents", _C, "Reprint an issued document."),
    _permission("template.view", "View templates", _C, "Read document templates."),
    _permission(
        "template.manage", "Design templates", _C, "Amend templates and create document types."
    ),
    _permission(
        "template.publish",
        "Publish template versions",
        _C,
        "Publish a template version.",
        sensitive=True,
    ),
    _permission("attachment.upload", "Upload attachments", _C, "Attach a file to a record."),
    # Reporting
    _permission("report.view", "View reports", _R, "Read operational and management reports."),
    _permission("report.export", "Export reports", _R, "Export a report to file."),
    _permission(
        "report.export_sensitive",
        "Export sensitive reports",
        _R,
        "Export reports disclosing cost or margin.",
        sensitive=True,
    ),
    _permission(
        "dashboard.configure", "Configure dashboards", _R, "Arrange and save dashboard layouts."
    ),
)

BY_CODE: Final[dict[str, Permission]] = {item.code: item for item in REGISTRY}

ALL_CODES: Final[frozenset[str]] = frozenset(BY_CODE)


def permission(code: str) -> Permission:
    """Look up a permission, raising if it is not registered."""
    try:
        return BY_CODE[code]
    except KeyError as error:
        raise KeyError(f"Unregistered permission: {code!r}") from error


def require_registered(codes: frozenset[str] | set[str]) -> None:
    """Assert every code is registered. Guards role seeding against typographical error."""
    unknown = set(codes) - ALL_CODES
    if unknown:
        raise KeyError(f"Unregistered permissions: {sorted(unknown)}")


# --------------------------------------------------------------------------------------
# Seeded roles — PRD 3. Each is editable after installation.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    """A seeded role."""

    code: str
    name: str
    description: str
    permissions: frozenset[str]
    is_administrator: bool = False
    is_read_only: bool = False


_VIEW_ONLY: Final[frozenset[str]] = frozenset(
    code for code in ALL_CODES if code.endswith((".view", ".export"))
)

SEEDED_ROLES: Final[tuple[RoleDefinition, ...]] = (
    RoleDefinition(
        code="administrator",
        name="Administrator",
        description=(
            "Business setup, branding, document templates, pricing, tax settings, "
            "permissions and approvals. Holds every permission."
        ),
        permissions=ALL_CODES,
        is_administrator=True,
    ),
    RoleDefinition(
        code="sales_person",
        name="Sales Person",
        description=(
            "Customers, quotations, sales orders, the counter, and the documents each produces."
        ),
        permissions=frozenset(
            {
                "business.view",
                "product.view",
                "customer.view",
                "customer.manage",
                "supplier.view",
                "affiliate.view",
                "rfq.view",
                "rfq.manage",
                "quotation.view",
                "quotation.manage",
                "quotation.issue",
                "sales_order.manage",
                "sales_invoice.issue",
                "sales_return.manage",
                "stock.view",
                "dispatch.view",
                "receivable.view",
                # The counter. There is no separate cashier role, so a sales person
                # who could not operate the till would leave the point of sale usable
                # by the proprietor alone.
                "pos.operate",
                "pos.shift_manage",
                "receipt.manage",
                "document.view",
                "document.issue",
                "document.reprint",
                "attachment.upload",
                "template.view",
                "report.view",
                "health.view",
            }
        ),
    ),
)
"""The roles a new installation starts with.

Two, and deliberately so: the proprietor, who holds everything, and the person who
serves customers. A larger business would want the usual separation — purchasing,
stores, dispatch, cash, accounts, audit — and the permission registry still carries
every code those roles would need, so they can be created under Settings without a
code change. Seeding roles nobody fills only makes the user list harder to read.

``pos.discount`` is withheld from the sales person: discretion over price is the
proprietor's, and an administrator can grant it to an individual.
"""

ROLES_BY_CODE: Final[dict[str, RoleDefinition]] = {role.code: role for role in SEEDED_ROLES}

for _role in SEEDED_ROLES:
    require_registered(_role.permissions)
