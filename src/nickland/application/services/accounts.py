"""Chart of accounts and accounting periods."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.ledger import (
    AccountKind,
    CashFlowCategory,
    PeriodState,
    PeriodWindow,
    Statement,
    SubledgerKind,
    assert_code_matches_kind,
    months_in_year,
    validate_account_code,
)
from nickland.persistence.base import as_dict
from nickland.persistence.ledger_models import (
    Account,
    AccountGroup,
    AccountingPeriod,
    JournalLine,
)
from nickland.persistence.models import Business
from nickland.platform.clock import Clock, SystemClock


@dataclass(frozen=True, slots=True)
class SeededGroup:
    code: str
    name: str
    kind: AccountKind
    order: int


@dataclass(frozen=True, slots=True)
class SeededAccount:
    code: str
    name: str
    group: str
    cash_flow: CashFlowCategory = CashFlowCategory.NONE
    control: SubledgerKind | None = None
    manual: bool = True


SEEDED_GROUPS: tuple[SeededGroup, ...] = (
    SeededGroup("CA", "Current assets", AccountKind.ASSET, 10),
    SeededGroup("NCA", "Non-current assets", AccountKind.ASSET, 20),
    SeededGroup("CL", "Current liabilities", AccountKind.LIABILITY, 30),
    SeededGroup("NCL", "Non-current liabilities", AccountKind.LIABILITY, 40),
    SeededGroup("EQ", "Equity", AccountKind.EQUITY, 50),
    SeededGroup("REV", "Revenue", AccountKind.REVENUE, 60),
    SeededGroup("COS", "Cost of sales", AccountKind.EXPENSE, 70),
    SeededGroup("OPEX", "Operating expenses", AccountKind.EXPENSE, 80),
    SeededGroup("OTH", "Other income and expense", AccountKind.EXPENSE, 90),
    SeededGroup("TAX", "Taxation on profit", AccountKind.EXPENSE, 100),
)

SEEDED_ACCOUNTS: tuple[SeededAccount, ...] = (
    # Current assets
    SeededAccount("10100", "Cash on hand", "CA", CashFlowCategory.CASH),
    SeededAccount("10200", "Bank accounts", "CA", CashFlowCategory.CASH),
    SeededAccount("10300", "Mobile Money wallets", "CA", CashFlowCategory.CASH),
    SeededAccount("10400", "Petty cash", "CA", CashFlowCategory.CASH),
    SeededAccount(
        "11000",
        "Trade receivables",
        "CA",
        CashFlowCategory.OPERATING,
        SubledgerKind.CUSTOMER,
        manual=False,
    ),
    SeededAccount("11100", "Other receivables", "CA", CashFlowCategory.OPERATING),
    SeededAccount(
        "11200",
        "Loans and advances issued",
        "CA",
        CashFlowCategory.INVESTING,
        SubledgerKind.BORROWER,
        manual=False,
    ),
    SeededAccount(
        "11300",
        "Withholding tax receivable",
        "CA",
        CashFlowCategory.OPERATING,
        SubledgerKind.AUTHORITY,
        manual=False,
    ),
    SeededAccount("12000", "Inventory", "CA", CashFlowCategory.OPERATING),
    SeededAccount("12100", "Goods in quarantine", "CA", CashFlowCategory.OPERATING),
    SeededAccount("12200", "Goods in transit", "CA", CashFlowCategory.OPERATING),
    SeededAccount(
        "13000",
        "Recoverable input tax",
        "CA",
        CashFlowCategory.OPERATING,
        SubledgerKind.AUTHORITY,
        manual=False,
    ),
    SeededAccount("14000", "Prepaid expenses", "CA", CashFlowCategory.OPERATING),
    # Non-current assets
    SeededAccount("15000", "Property, plant and equipment", "NCA", CashFlowCategory.INVESTING),
    # Current liabilities
    SeededAccount(
        "20000",
        "Trade payables",
        "CL",
        CashFlowCategory.OPERATING,
        SubledgerKind.SUPPLIER,
        manual=False,
    ),
    SeededAccount("20100", "Other payables", "CL", CashFlowCategory.OPERATING),
    SeededAccount("20200", "Accrued expenses", "CL", CashFlowCategory.OPERATING),
    SeededAccount(
        "20300",
        "Affiliate commission payable",
        "CL",
        CashFlowCategory.OPERATING,
        SubledgerKind.AFFILIATE,
        manual=False,
    ),
    SeededAccount(
        "21000",
        "Output tax payable",
        "CL",
        CashFlowCategory.OPERATING,
        SubledgerKind.AUTHORITY,
        manual=False,
    ),
    SeededAccount(
        "21100",
        "Withholding tax payable",
        "CL",
        CashFlowCategory.OPERATING,
        SubledgerKind.AUTHORITY,
        manual=False,
    ),
    SeededAccount("21200", "Other statutory liabilities", "CL", CashFlowCategory.OPERATING),
    SeededAccount(
        "22000",
        "Customer deposits and advances",
        "CL",
        CashFlowCategory.OPERATING,
        SubledgerKind.CUSTOMER,
        manual=False,
    ),
    SeededAccount("22100", "Deferred revenue", "CL", CashFlowCategory.OPERATING),
    # Non-current liabilities
    SeededAccount(
        "23000",
        "Loans and borrowings",
        "NCL",
        CashFlowCategory.FINANCING,
        SubledgerKind.LENDER,
        manual=False,
    ),
    SeededAccount(
        "23100",
        "Accrued interest payable",
        "NCL",
        CashFlowCategory.FINANCING,
        SubledgerKind.LENDER,
        manual=False,
    ),
    # Equity
    SeededAccount("30000", "Owner's capital", "EQ", CashFlowCategory.FINANCING),
    SeededAccount("30100", "Retained earnings", "EQ", CashFlowCategory.FINANCING),
    # Reserves are financing: a drawing taken from them is a financing outflow, and
    # leaving the category unset would strand it in the unclassified line.
    SeededAccount("30200", "Current year result", "EQ"),
    # Revenue
    SeededAccount("40000", "Sales revenue — quotation-led", "REV", CashFlowCategory.OPERATING),
    SeededAccount("40100", "Sales revenue — point of sale", "REV", CashFlowCategory.OPERATING),
    SeededAccount("40200", "Service revenue", "REV", CashFlowCategory.OPERATING),
    SeededAccount("40900", "Sales discounts", "REV", CashFlowCategory.OPERATING),
    SeededAccount("40950", "Sales returns", "REV", CashFlowCategory.OPERATING),
    # Cost of sales
    SeededAccount("50000", "Cost of goods sold", "COS", CashFlowCategory.OPERATING),
    SeededAccount("50100", "Non-recoverable tax on purchases", "COS", CashFlowCategory.OPERATING),
    SeededAccount("50200", "Freight and clearing on sales", "COS", CashFlowCategory.OPERATING),
    SeededAccount("50300", "Affiliate commission expense", "COS", CashFlowCategory.OPERATING),
    SeededAccount("51000", "Inventory write-off and shrinkage", "COS", CashFlowCategory.OPERATING),
    # Operating expenses
    SeededAccount("60000", "Rent", "OPEX", CashFlowCategory.OPERATING),
    SeededAccount("60100", "Utilities", "OPEX", CashFlowCategory.OPERATING),
    SeededAccount("60200", "Salaries and wages", "OPEX", CashFlowCategory.OPERATING),
    SeededAccount("60300", "Transport and fuel", "OPEX", CashFlowCategory.OPERATING),
    SeededAccount("60400", "Repairs and maintenance", "OPEX", CashFlowCategory.OPERATING),
    SeededAccount("60500", "Marketing", "OPEX", CashFlowCategory.OPERATING),
    SeededAccount("60600", "Professional fees", "OPEX", CashFlowCategory.OPERATING),
    SeededAccount("60700", "Insurance", "OPEX", CashFlowCategory.OPERATING),
    SeededAccount("60800", "Office and administration", "OPEX", CashFlowCategory.OPERATING),
    # Other income and expense
    SeededAccount("70000", "Interest income", "OTH", CashFlowCategory.OPERATING),
    SeededAccount("70100", "Other income", "OTH", CashFlowCategory.OPERATING),
    SeededAccount("71000", "Interest expense", "OTH", CashFlowCategory.FINANCING),
    SeededAccount("71100", "Bank charges", "OTH", CashFlowCategory.OPERATING),
    SeededAccount("71200", "Exchange differences", "OTH", CashFlowCategory.OPERATING),
    SeededAccount("72000", "Bad debt expense", "OTH", CashFlowCategory.OPERATING),
    # Taxation on profit
    SeededAccount("80000", "Income tax expense", "TAX", CashFlowCategory.OPERATING),
)

RETAINED_EARNINGS = "30100"
CURRENT_YEAR_RESULT = "30200"


class AccountService:
    """The chart of accounts and the accounting calendar."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # -- seeding ----------------------------------------------------------------------

    def seed(self, business_id: uuid.UUID) -> tuple[int, int]:
        """Create the default chart. Run once, at provisioning."""
        existing = self._db.scalar(
            select(Account.id).where(Account.business_id == business_id).limit(1)
        )
        if existing is not None:
            return 0, 0

        groups: dict[str, AccountGroup] = {}
        for seeded in SEEDED_GROUPS:
            group = AccountGroup(
                business_id=business_id,
                code=seeded.code,
                name=seeded.name,
                kind=seeded.kind.value,
                statement=seeded.kind.statement.value,
                display_order=seeded.order,
            )
            self._db.add(group)
            groups[seeded.code] = group
        self._db.flush()

        business = self._db.get(Business, business_id)
        currency = business.base_currency if business else "GHS"

        for order, entry in enumerate(SEEDED_ACCOUNTS):
            kind = _kind_of(entry.code)
            self._db.add(
                Account(
                    business_id=business_id,
                    code=entry.code,
                    name=entry.name,
                    kind=kind.value,
                    statement=kind.statement.value,
                    group_fk=groups[entry.group].id,
                    cash_flow_category=entry.cash_flow.value,
                    control_subledger=entry.control.value if entry.control else None,
                    currency_code=currency,
                    is_system=True,
                    permits_manual_posting=entry.manual,
                    display_order=order,
                    search_text=f"{entry.code} {entry.name}".lower(),
                )
            )
        self._db.flush()
        return len(SEEDED_GROUPS), len(SEEDED_ACCOUNTS)

    def open_financial_year(
        self, business_id: uuid.UUID, year: int, start_month: int | None = None
    ) -> int:
        """Create the twelve periods of a financial year.

        The year begins in the month the business was configured with, unless the
        caller names another. Returns zero where the year is already open, so opening
        it twice is harmless.
        """
        if start_month is None:
            business = self._db.get(Business, business_id)
            start_month = business.financial_year_start_month if business else 1
        existing = self._db.scalar(
            select(AccountingPeriod.id).where(
                AccountingPeriod.business_id == business_id,
                AccountingPeriod.financial_year == year,
            )
        )
        if existing is not None:
            return 0

        for sequence, (month, offset) in enumerate(months_in_year(start_month), start=1):
            calendar_year = year + offset
            starts = date(calendar_year, month, 1)
            ends = _end_of_month(starts)
            self._db.add(
                AccountingPeriod(
                    business_id=business_id,
                    financial_year=year,
                    sequence=sequence,
                    label=PeriodWindow(starts, ends).label,
                    starts_on=starts,
                    ends_on=ends,
                    state=PeriodState.OPEN.value,
                    is_year_end=sequence == 12,
                )
            )
        self._db.flush()
        return 12

    # -- accounts ---------------------------------------------------------------------

    def groups(self, actor: AnyActor) -> list[AccountGroup]:
        actor.require("account.view")
        return list(
            self._db.scalars(
                select(AccountGroup)
                .where(AccountGroup.business_id == actor.business_id)
                .order_by(AccountGroup.display_order)
            )
        )

    def accounts(
        self,
        actor: AnyActor,
        *,
        term: str | None = None,
        kind: AccountKind | str | None = None,
        statement: Statement | str | None = None,
        include_inactive: bool = False,
        manual_only: bool = False,
    ) -> list[Account]:
        """The chart, in code order."""
        actor.require("account.view")
        statement_query = select(Account).where(Account.business_id == actor.business_id)
        if not include_inactive:
            statement_query = statement_query.where(Account.is_active.is_(True))
        if kind is not None:
            statement_query = statement_query.where(Account.kind == str(kind))
        if statement is not None:
            statement_query = statement_query.where(Account.statement == str(statement))
        if manual_only:
            statement_query = statement_query.where(Account.permits_manual_posting.is_(True))
        if term and term.strip():
            statement_query = statement_query.where(
                Account.search_text.like(f"%{term.strip().lower()}%")
            )
        return list(self._db.scalars(statement_query.order_by(Account.code)))

    def require(self, actor: AnyActor, account_id: uuid.UUID) -> Account:
        account = self._db.scalar(
            select(Account).where(
                Account.business_id == actor.business_id, Account.id == account_id
            )
        )
        if account is None:
            raise NotFoundError("Account not found.")
        return account

    def by_code(self, business_id: uuid.UUID, code: str) -> Account | None:
        return self._db.scalar(
            select(Account).where(Account.business_id == business_id, Account.code == code.strip())
        )

    def require_by_code(self, business_id: uuid.UUID, code: str) -> Account:
        account = self.by_code(business_id, code)
        if account is None:
            raise NotFoundError(f"No account carries the code {code}.")
        return account

    def create(
        self,
        actor: AnyActor,
        *,
        code: str,
        name: str,
        kind: AccountKind | str,
        group_id: uuid.UUID | None = None,
        description: str | None = None,
        cash_flow_category: CashFlowCategory | str = CashFlowCategory.NONE,
        control_subledger: SubledgerKind | str | None = None,
        permits_manual_posting: bool = True,
    ) -> Account:
        """Add an account to the chart."""
        actor.require("account.manage")
        account_code = validate_account_code(code)
        if not name.strip():
            raise ValidationError("An account name is required.", field="name")

        account_kind = AccountKind(str(kind))
        assert_code_matches_kind(account_code, account_kind)

        if self.by_code(actor.business_id, account_code) is not None:
            raise ConflictError(f"An account already carries the code {account_code}.")

        if group_id is not None:
            group = self._db.scalar(
                select(AccountGroup).where(
                    AccountGroup.business_id == actor.business_id,
                    AccountGroup.id == group_id,
                )
            )
            if group is None:
                raise NotFoundError("Account group not found.")
            if group.kind != account_kind.value:
                raise ValidationError(
                    f"{group.name} gathers {group.kind} accounts, so a "
                    f"{account_kind.value} account cannot sit under it.",
                    field="group_id",
                )

        business = self._db.get(Business, actor.business_id)
        account = Account(
            business_id=actor.business_id,
            code=account_code,
            name=name.strip(),
            description=description or None,
            kind=account_kind.value,
            statement=account_kind.statement.value,
            group_fk=group_id,
            cash_flow_category=CashFlowCategory(str(cash_flow_category)).value,
            control_subledger=str(control_subledger) if control_subledger else None,
            currency_code=business.base_currency if business else None,
            permits_manual_posting=permits_manual_posting,
            search_text=f"{account_code} {name.strip()}".lower(),
        )
        self._db.add(account)
        self._db.flush()

        self._audit.record(
            actor,
            "account_created",
            entity_type="account",
            entity_id=account.id,
            entity_label=f"{account.code} — {account.name}",
            after={"code": account.code, "name": account.name, "kind": account.kind},
        )
        return account

    def amend(self, actor: AnyActor, account_id: uuid.UUID, changes: dict[str, Any]) -> Account:
        """Amend an account. Its class and code are fixed once it carries entries."""
        actor.require("account.manage")
        account = self.require(actor, account_id)
        before = as_dict(account)

        permitted = {
            "name",
            "description",
            "group_id",
            "cash_flow_category",
            "permits_manual_posting",
            "is_active",
            "display_order",
        }
        unknown = set(changes) - permitted
        if unknown:
            raise ValidationError(
                f"These properties cannot be amended: {sorted(unknown)}. An account's "
                "code and class are fixed, because every posted entry refers to them."
            )

        if changes.get("is_active") is False and self._has_entries(account.id):
            raise ConflictError(
                f"{account.code} carries posted entries, so it cannot be withdrawn. "
                "An account in use remains, so its history stays readable."
            )

        for key, value in changes.items():
            setattr(account, "group_fk" if key == "group_id" else key, value)
        account.search_text = f"{account.code} {account.name}".lower()

        self._audit.record_change(
            actor,
            "account_amended",
            entity_type="account",
            entity_id=account.id,
            entity_label=f"{account.code} — {account.name}",
            before=before,
            after=as_dict(account),
        )
        return account

    # -- periods ----------------------------------------------------------------------

    def periods(
        self, actor: AnyActor, *, financial_year: int | None = None
    ) -> list[AccountingPeriod]:
        actor.require("account.view")
        query = select(AccountingPeriod).where(AccountingPeriod.business_id == actor.business_id)
        if financial_year is not None:
            query = query.where(AccountingPeriod.financial_year == financial_year)
        return list(
            self._db.scalars(
                query.order_by(AccountingPeriod.financial_year, AccountingPeriod.sequence)
            )
        )

    def period_for(self, business_id: uuid.UUID, on: date) -> AccountingPeriod | None:
        """The period a date falls in."""
        return self._db.scalar(
            select(AccountingPeriod).where(
                AccountingPeriod.business_id == business_id,
                AccountingPeriod.starts_on <= on,
                AccountingPeriod.ends_on >= on,
            )
        )

    def require_period_for(self, business_id: uuid.UUID, on: date) -> AccountingPeriod:
        period = self.period_for(business_id, on)
        if period is None:
            raise ConflictError(
                f"No accounting period covers {on:%d %B %Y}. Open the financial year "
                "before posting into it."
            )
        return period

    def _has_entries(self, account_id: uuid.UUID) -> bool:
        """Whether any journal line refers to an account."""
        found = self._db.scalar(
            select(JournalLine.id).where(JournalLine.account_fk == account_id).limit(1)
        )
        return found is not None

    def ensure_year_for(self, business_id: uuid.UUID, on: date) -> AccountingPeriod:
        """Open the financial year containing a date, where it is not yet open."""
        period = self.period_for(business_id, on)
        if period is not None:
            return period
        business = self._db.get(Business, business_id)
        start_month = business.financial_year_start_month if business else 1
        year = on.year if on.month >= start_month else on.year - 1
        self.open_financial_year(business_id, year, start_month)
        return self.require_period_for(business_id, on)


def _kind_of(code: str) -> AccountKind:
    from nickland.domain.ledger import kind_for_code

    return kind_for_code(code)


def _end_of_month(on: date) -> date:
    if on.month == 12:
        return on.replace(day=31)
    return on.replace(month=on.month + 1, day=1) - timedelta(days=1)
