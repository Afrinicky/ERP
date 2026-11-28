"""Budgets: plans drawn by account or category, branch and analysis code, approved,
versioned, and compared with what was posted. PRD §29.6.

A budget never posts. Its actual figures are read from posted journal lines at the moment
they are compared, so a variance is always against the ledger as it stands. A budget that
has been approved is kept as approved; changing it means a new version, which supersedes
the old one only when it is approved in turn.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.money import ZERO, money
from nickland.domain.planning import (
    AnalysisKind,
    Basis,
    BudgetPeriod,
    BudgetState,
    Nature,
    PeriodKind,
    RequestState,
    Variance,
    budget_periods,
    cents,
    outturn,
    spread,
    variance,
    year_before,
)
from nickland.persistence.ledger_models import Account, AccountGroup, JournalEntry, JournalLine
from nickland.persistence.models import Business, Setting
from nickland.persistence.planning_models import (
    AnalysisCode,
    Budget,
    BudgetDecision,
    BudgetLine,
    BudgetRequest,
)
from nickland.platform.clock import Clock, SystemClock, business_date

REPORTABLE = ("posted", "reversed")
CONTROL_KEY = "budgets.overspend_control"
_INCOME_CODES = frozenset({"70000", "70100", "70200"})


@dataclass(slots=True)
class BudgetDraft:
    name: str
    period_kind: PeriodKind | str
    starts_on: date
    ends_on: date
    branch_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    assumptions: str | None = None


@dataclass(slots=True)
class LineDraft:
    account_code: str | None = None
    group_code: str | None = None
    branch_id: uuid.UUID | None = None
    analysis_code_id: uuid.UUID | None = None
    amounts: dict[str, Decimal] = field(default_factory=dict)
    total: Decimal | None = None
    """Spread evenly across the periods when no amounts are given."""
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Cell:
    period_key: str
    original: Decimal
    budget: Decimal
    actual: Decimal
    prior: Decimal
    variance: Variance


@dataclass(frozen=True, slots=True)
class LineComparison:
    line_id: uuid.UUID
    label: str
    target: str
    nature: Nature
    analysis: str | None
    branch_id: uuid.UUID | None
    cells: list[Cell]
    total: Cell
    to_date: Cell
    """The periods begun so far: the budget for them against what has been posted."""
    forecast: Decimal
    """Actual to date plus the budget for the periods still to come."""


@dataclass(frozen=True, slots=True)
class Comparison:
    budget: Budget
    periods: list[BudgetPeriod]
    lines: list[LineComparison]
    income: Cell
    costs: Cell
    income_to_date: Cell
    costs_to_date: Cell
    as_of: date
    bases: dict[str, str]


@dataclass(frozen=True, slots=True)
class OverspendCheck:
    line: BudgetLine
    budget: Budget
    period: BudgetPeriod
    planned: Decimal
    spent: Decimal
    after: Decimal

    @property
    def excess(self) -> Decimal:
        return cents(self.after - self.planned)


class BudgetService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # -- analysis codes ---------------------------------------------------------------------

    def codes(self, actor: AnyActor, *, kind: str | None = None) -> list[AnalysisCode]:
        query = select(AnalysisCode).where(AnalysisCode.business_id == actor.business_id)
        if kind:
            query = query.where(AnalysisCode.kind == AnalysisKind(kind).value)
        return list(self._db.scalars(query.order_by(AnalysisCode.kind, AnalysisCode.code)))

    def create_code(self, actor: AnyActor, *, kind: str, code: str, name: str) -> AnalysisCode:
        actor.require("budget.manage")
        business_id = _business_of(actor)
        clean = code.strip().upper()
        if not clean or len(clean) > 24 or not clean.replace("-", "").isalnum():
            raise ValidationError("A code of letters, digits and hyphens, up to 24.", field="code")
        label = " ".join(name.split())
        if not label:
            raise ValidationError("Name it.", field="name")
        if self._db.scalar(
            select(AnalysisCode).where(
                AnalysisCode.business_id == business_id, AnalysisCode.code == clean
            )
        ):
            raise ConflictError(f"{clean} is already in use.")
        row = AnalysisCode(
            business_id=business_id, kind=AnalysisKind(kind).value, code=clean, name=label
        )
        self._db.add(row)
        self._db.flush()
        self._audit.record(
            actor,
            "analysis_code_created",
            entity_type="analysis_code",
            entity_id=row.id,
            entity_label=clean,
            after={"kind": row.kind, "name": label},
        )
        return row

    def amend_code(
        self, actor: AnyActor, code_id: uuid.UUID, *, name: str | None, is_active: bool | None
    ) -> AnalysisCode:
        actor.require("budget.manage")
        row = self._db.get(AnalysisCode, code_id)
        if row is None or row.business_id != actor.business_id:
            raise NotFoundError("Analysis code not found.")
        before = {"name": row.name, "is_active": row.is_active}
        if name is not None and name.strip():
            row.name = " ".join(name.split())
        if is_active is not None:
            row.is_active = is_active
        self._db.flush()
        self._audit.record(
            actor,
            "analysis_code_amended",
            entity_type="analysis_code",
            entity_id=row.id,
            entity_label=row.code,
            before=before,
            after={"name": row.name, "is_active": row.is_active},
        )
        return row

    # -- budgets ----------------------------------------------------------------------------

    def budgets(self, actor: AnyActor, *, include_superseded: bool = False) -> list[Budget]:
        _require_view(actor)
        query = select(Budget).where(Budget.business_id == actor.business_id)
        if not include_superseded:
            query = query.where(Budget.state != BudgetState.SUPERSEDED.value)
        return list(self._db.scalars(query.order_by(Budget.starts_on.desc(), Budget.name)))

    def versions(self, actor: AnyActor, budget_id: uuid.UUID) -> list[Budget]:
        budget = self.require(actor, budget_id)
        return list(
            self._db.scalars(
                select(Budget)
                .where(Budget.lineage_fk == (budget.lineage_fk or budget.id))
                .order_by(Budget.version)
            )
        )

    def require(self, actor: AnyActor, budget_id: uuid.UUID) -> Budget:
        _require_view(actor)
        row = self._db.get(Budget, budget_id)
        if row is None or row.business_id != actor.business_id:
            raise NotFoundError("Budget not found.")
        return row

    def decisions(self, actor: AnyActor, budget_id: uuid.UUID) -> list[BudgetDecision]:
        budget = self.require(actor, budget_id)
        ids = [version.id for version in self.versions(actor, budget.id)]
        return list(
            self._db.scalars(
                select(BudgetDecision)
                .where(BudgetDecision.budget_fk.in_(ids))
                .order_by(BudgetDecision.occurred_at)
            )
        )

    def periods(self, budget: Budget) -> list[BudgetPeriod]:
        return budget_periods(PeriodKind(budget.period_kind), budget.starts_on, budget.ends_on)

    def create(self, actor: AnyActor, draft: BudgetDraft) -> Budget:
        actor.require("budget.manage")
        business_id = _business_of(actor)
        name = " ".join((draft.name or "").split())
        if not name:
            raise ValidationError("Name the budget.", field="name")
        kind = PeriodKind(draft.period_kind)
        budget_periods(kind, draft.starts_on, draft.ends_on)
        budget = Budget(
            business_id=business_id,
            name=name,
            period_kind=kind.value,
            starts_on=draft.starts_on,
            ends_on=draft.ends_on,
            branch_fk=draft.branch_id,
            owner_id=draft.owner_id or actor.user_id,
            assumptions=(draft.assumptions or "").strip() or None,
            state=BudgetState.DRAFT.value,
            version=1,
            created_by_id=actor.user_id,
        )
        self._db.add(budget)
        self._db.flush()
        budget.lineage_fk = budget.id
        self._decide(actor, budget, "created", None)
        self._audit.record(
            actor,
            "budget_created",
            entity_type="budget",
            entity_id=budget.id,
            entity_label=name,
            after={"period_kind": kind.value, "starts_on": draft.starts_on.isoformat()},
        )
        return budget

    def amend(self, actor: AnyActor, budget_id: uuid.UUID, draft: BudgetDraft) -> Budget:
        actor.require("budget.manage")
        budget = self._editable(actor, budget_id)
        kind = PeriodKind(draft.period_kind)
        periods = budget_periods(kind, draft.starts_on, draft.ends_on)
        changed_columns = (
            kind.value != budget.period_kind
            or draft.starts_on != budget.starts_on
            or draft.ends_on != budget.ends_on
        )
        budget.name = " ".join((draft.name or budget.name).split())
        budget.period_kind = kind.value
        budget.starts_on = draft.starts_on
        budget.ends_on = draft.ends_on
        budget.branch_fk = draft.branch_id
        budget.owner_id = draft.owner_id or budget.owner_id
        budget.assumptions = (draft.assumptions or "").strip() or None
        if changed_columns:
            for line in budget.lines:
                line.amounts = {
                    key: str(value) for key, value in spread(line.total, periods).items()
                }
        self._db.flush()
        return budget

    def set_lines(
        self, actor: AnyActor, budget_id: uuid.UUID, drafts: Sequence[LineDraft]
    ) -> Budget:
        """Replace the lines of a draft. A line plans one account or one category."""
        actor.require("budget.manage")
        budget = self._editable(actor, budget_id)
        periods = self.periods(budget)
        keys = {period.key for period in periods}
        seen: set[tuple[Any, ...]] = set()
        for line in list(budget.lines):
            budget.lines.remove(line)
        self._db.flush()
        for position, draft in enumerate(drafts, start=1):
            account_code = (draft.account_code or "").strip() or None
            group_code = (draft.group_code or "").strip() or None
            if (account_code is None) == (group_code is None):
                raise ValidationError("Each line plans one account or one category.", field="lines")
            if account_code:
                self._account(budget.business_id, account_code)
            else:
                self._group(budget.business_id, group_code or "")
            if draft.analysis_code_id is not None:
                code = self._db.get(AnalysisCode, draft.analysis_code_id)
                if code is None or code.business_id != budget.business_id:
                    raise NotFoundError("Analysis code not found.")
            key = (account_code, group_code, draft.branch_id, draft.analysis_code_id)
            if key in seen:
                raise ConflictError(
                    f"{account_code or group_code} is planned twice for the same branch and "
                    "analysis code."
                )
            seen.add(key)
            if draft.amounts:
                unknown = set(draft.amounts) - keys
                if unknown:
                    raise ValidationError(
                        f"These are not periods of the budget: {', '.join(sorted(unknown))}.",
                        field="lines",
                    )
                amounts = {key: cents(money(draft.amounts.get(key, ZERO))) for key in keys}
            else:
                amounts = spread(money(draft.total or ZERO), periods)
            if any(value < ZERO for value in amounts.values()):
                raise ValidationError("A planned amount is not negative.", field="lines")
            budget.lines.append(
                BudgetLine(
                    business_id=budget.business_id,
                    position=position,
                    account_code=account_code,
                    group_code=group_code,
                    branch_fk=draft.branch_id,
                    analysis_code_fk=draft.analysis_code_id,
                    amounts={key: str(value) for key, value in amounts.items()},
                    total=cents(sum(amounts.values(), ZERO)),
                    note=(draft.note or "").strip() or None,
                )
            )
        self._db.flush()
        self._audit.record(
            actor,
            "budget_lines_set",
            entity_type="budget",
            entity_id=budget.id,
            entity_label=budget.name,
            after={"lines": len(budget.lines), "total": str(self.total(budget))},
        )
        return budget

    def total(self, budget: Budget) -> Decimal:
        return cents(sum((line.total for line in budget.lines), ZERO))

    def submit(self, actor: AnyActor, budget_id: uuid.UUID) -> Budget:
        actor.require("budget.manage")
        budget = self._editable(actor, budget_id)
        if not budget.lines:
            raise ConflictError("A budget with no lines plans nothing.")
        budget.state = BudgetState.IN_REVIEW.value
        budget.submitted_by_id = actor.user_id
        self._decide(actor, budget, "submitted", None)
        return budget

    def approve(self, actor: AnyActor, budget_id: uuid.UUID, *, note: str | None = None) -> Budget:
        """Approved by someone other than whoever submitted it. Approval supersedes the
        version before, which remains readable as it was."""
        actor.require("budget.approve")
        budget = self.require(actor, budget_id)
        if budget.state != BudgetState.IN_REVIEW.value:
            raise ConflictError(f"{budget.name} is not in review.")
        if budget.submitted_by_id == actor.user_id and not actor.is_administrator:
            raise ConflictError("A budget is approved by someone other than whoever submitted it.")
        for version in self.versions(actor, budget.id):
            if version.id != budget.id and version.state == BudgetState.APPROVED.value:
                version.state = BudgetState.SUPERSEDED.value
                self._decide(actor, version, "superseded", f"By version {budget.version}.")
        budget.state = BudgetState.APPROVED.value
        budget.approved_by_id = actor.user_id
        budget.approved_at = self._clock.now()
        self._decide(actor, budget, "approved", note)
        self._audit.record(
            actor,
            "budget_approved",
            entity_type="budget",
            entity_id=budget.id,
            entity_label=f"{budget.name} v{budget.version}",
            reason=note,
            after={"total": str(self.total(budget))},
        )
        return budget

    def reject(self, actor: AnyActor, budget_id: uuid.UUID, *, reason: str) -> Budget:
        actor.require("budget.approve")
        budget = self.require(actor, budget_id)
        if budget.state != BudgetState.IN_REVIEW.value:
            raise ConflictError(f"{budget.name} is not in review.")
        if not reason.strip():
            raise ValidationError("Returning a budget records why.", field="reason")
        budget.state = BudgetState.REJECTED.value
        self._decide(actor, budget, "returned", reason.strip())
        return budget

    def revise(self, actor: AnyActor, budget_id: uuid.UUID) -> Budget:
        """A new draft version of an approved budget, its lines copied. The approved
        version stands until the revision is approved."""
        actor.require("budget.manage")
        source = self.require(actor, budget_id)
        if source.state != BudgetState.APPROVED.value:
            raise ConflictError("Only an approved budget is revised.")
        lineage = source.lineage_fk or source.id
        open_draft = self._db.scalar(
            select(Budget).where(
                Budget.lineage_fk == lineage,
                Budget.state.in_(
                    [
                        BudgetState.DRAFT.value,
                        BudgetState.IN_REVIEW.value,
                        BudgetState.REJECTED.value,
                    ]
                ),
            )
        )
        if open_draft is not None:
            raise ConflictError(f"Version {open_draft.version} is already being revised.")
        latest = max(version.version for version in self.versions(actor, source.id))
        copy = Budget(
            business_id=source.business_id,
            name=source.name,
            period_kind=source.period_kind,
            starts_on=source.starts_on,
            ends_on=source.ends_on,
            branch_fk=source.branch_fk,
            owner_id=source.owner_id,
            assumptions=source.assumptions,
            state=BudgetState.DRAFT.value,
            version=latest + 1,
            lineage_fk=lineage,
            supersedes_fk=source.id,
            created_by_id=actor.user_id,
        )
        self._db.add(copy)
        self._db.flush()
        for line in source.lines:
            copy.lines.append(
                BudgetLine(
                    business_id=line.business_id,
                    position=line.position,
                    account_code=line.account_code,
                    group_code=line.group_code,
                    branch_fk=line.branch_fk,
                    analysis_code_fk=line.analysis_code_fk,
                    amounts=dict(self._effective(line)),
                    total=cents(sum((money(v) for v in self._effective(line).values()), ZERO)),
                    note=line.note,
                )
            )
        self._db.flush()
        self._decide(actor, copy, "revised", f"From version {source.version}.")
        return copy

    # -- comparison -------------------------------------------------------------------------

    def comparison(
        self, actor: AnyActor, budget_id: uuid.UUID, *, as_of: date | None = None
    ) -> Comparison:
        """Each line, period by period: budget, actual, the same period a year before, the
        variance, and a forecast outturn. Each kind of figure is labelled as what it is."""
        actor.require_any("report.view", "budget.manage", "budget.approve")
        budget = self.require(actor, budget_id)
        today = as_of or self._today(budget.business_id)
        periods = self.periods(budget)
        rows: list[LineComparison] = []
        begun: dict[Nature, dict[str, Decimal]] = {
            Nature.INCOME: {"o": ZERO, "b": ZERO, "a": ZERO, "p": ZERO},
            Nature.COST: {"o": ZERO, "b": ZERO, "a": ZERO, "p": ZERO},
        }
        totals: dict[Nature, dict[str, Decimal]] = {
            Nature.INCOME: {"o": ZERO, "b": ZERO, "a": ZERO, "p": ZERO},
            Nature.COST: {"o": ZERO, "b": ZERO, "a": ZERO, "p": ZERO},
        }
        for line in budget.lines:
            nature = self.nature_of(budget.business_id, line)
            effective = self._effective(line)
            cells: list[Cell] = []
            forecast = ZERO
            for period in periods:
                original = money(line.amounts.get(period.key, "0"))
                planned = money(effective.get(period.key, "0"))
                actual = self._actual(budget, line, period.starts_on, min(period.ends_on, today))
                if period.starts_on > today:
                    actual = ZERO
                prior_start, prior_end = year_before(period.starts_on, period.ends_on)
                prior = self._actual(budget, line, prior_start, prior_end)
                cells.append(
                    Cell(
                        period.key,
                        original,
                        planned,
                        actual,
                        prior,
                        variance(actual, planned, nature),
                    )
                )
                forecast += (
                    actual
                    if period.ends_on <= today
                    else (max(actual, planned) if period.starts_on <= today else planned)
                )
            sums = {
                "o": sum((cell.original for cell in cells), ZERO),
                "b": sum((cell.budget for cell in cells), ZERO),
                "a": sum((cell.actual for cell in cells), ZERO),
                "p": sum((cell.prior for cell in cells), ZERO),
            }
            for key, value in sums.items():
                totals[nature][key] += value
            started = [
                cell
                for cell, period in zip(cells, periods, strict=True)
                if period.starts_on <= today
            ]
            early = {
                "o": sum((cell.original for cell in started), ZERO),
                "b": sum((cell.budget for cell in started), ZERO),
                "a": sum((cell.actual for cell in started), ZERO),
                "p": sum((cell.prior for cell in started), ZERO),
            }
            for key, value in early.items():
                begun[nature][key] += value
            rows.append(
                LineComparison(
                    line_id=line.id,
                    label=self.label_of(budget.business_id, line),
                    target=line.account_code or f"group {line.group_code}",
                    nature=nature,
                    analysis=self.analysis_label(line.analysis_code_fk),
                    branch_id=line.branch_fk,
                    cells=cells,
                    total=Cell(
                        "total",
                        sums["o"],
                        sums["b"],
                        sums["a"],
                        sums["p"],
                        variance(sums["a"], sums["b"], nature),
                    ),
                    to_date=Cell(
                        "to_date",
                        early["o"],
                        early["b"],
                        early["a"],
                        early["p"],
                        variance(early["a"], early["b"], nature),
                    ),
                    forecast=outturn(forecast, ZERO),
                )
            )

        def summary(nature: Nature, source: dict[Nature, dict[str, Decimal]] = totals) -> Cell:
            figures = source[nature]
            return Cell(
                "total",
                figures["o"],
                figures["b"],
                figures["a"],
                figures["p"],
                variance(figures["a"], figures["b"], nature),
            )

        return Comparison(
            budget=budget,
            periods=periods,
            lines=rows,
            income=summary(Nature.INCOME),
            costs=summary(Nature.COST),
            income_to_date=summary(Nature.INCOME, begun),
            costs_to_date=summary(Nature.COST, begun),
            as_of=today,
            bases={
                "budget": Basis.BUDGET.label,
                "actual": Basis.ACTUAL.label,
                "prior": "Actual, the same period a year before",
                "forecast": "Forecast: actual to date, then the budget for what is to come",
            },
        )

    def drill(
        self, actor: AnyActor, budget_id: uuid.UUID, line_id: uuid.UUID, period_key: str
    ) -> list[tuple[JournalLine, JournalEntry, Account]]:
        """The postings an actual figure is made of."""
        actor.require_any("report.view", "budget.manage", "budget.approve")
        budget = self.require(actor, budget_id)
        line = next((row for row in budget.lines if row.id == line_id), None)
        if line is None:
            raise NotFoundError("Budget line not found.")
        period = next((row for row in self.periods(budget) if row.key == period_key), None)
        if period is None:
            raise NotFoundError("Period not found.")
        query = self._lines_query(budget, line, period.starts_on, period.ends_on)
        return [
            (row[0], row[1], row[2])
            for row in self._db.execute(
                query.add_columns(JournalEntry, Account)
                .join(JournalEntry, JournalEntry.id == JournalLine.entry_fk)
                .order_by(JournalLine.posting_date, JournalEntry.number)
                .limit(500)
            ).all()
        ]

    # -- control ----------------------------------------------------------------------------

    def control_enabled(self, business_id: uuid.UUID) -> bool:
        stored = self._db.scalar(
            select(Setting).where(Setting.business_id == business_id, Setting.key == CONTROL_KEY)
        )
        return True if stored is None or stored.value is None else str(stored.value) == "true"

    def set_control(self, actor: AnyActor, enabled: bool) -> bool:
        actor.require("budget.approve")
        business_id = _business_of(actor)
        stored = self._db.scalar(
            select(Setting).where(Setting.business_id == business_id, Setting.key == CONTROL_KEY)
        )
        value = "true" if enabled else "false"
        if stored is None:
            self._db.add(
                Setting(
                    business_id=business_id,
                    key=CONTROL_KEY,
                    value=value,
                    description=(
                        "An expense that would exceed its approved budget waits for approval."
                    ),
                )
            )
        else:
            stored.value = value
        self._db.flush()
        self._audit.record(
            actor,
            "budget_control_amended",
            entity_type="setting",
            entity_label=CONTROL_KEY,
            after={"value": value},
        )
        return enabled

    def check_spend(
        self,
        business_id: uuid.UUID,
        *,
        account_code: str,
        on: date,
        amount: Decimal,
        branch_id: uuid.UUID | None = None,
        analysis_code_id: uuid.UUID | None = None,
    ) -> OverspendCheck | None:
        """The approved budget line a cost would breach, if any. Leave already granted for
        the period counts as budget."""
        if not self.control_enabled(business_id):
            return None
        account = self._db.scalar(
            select(Account).where(Account.business_id == business_id, Account.code == account_code)
        )
        if account is None:
            return None
        group = self._db.get(AccountGroup, account.group_fk) if account.group_fk else None
        budgets = self._db.scalars(
            select(Budget).where(
                Budget.business_id == business_id,
                Budget.state == BudgetState.APPROVED.value,
                Budget.starts_on <= on,
                Budget.ends_on >= on,
            )
        )
        for budget in budgets:
            if budget.branch_fk is not None and budget.branch_fk != branch_id:
                continue
            for line in budget.lines:
                if line.account_code and line.account_code != account_code:
                    continue
                if line.group_code and (group is None or line.group_code != group.code):
                    continue
                if line.branch_fk is not None and line.branch_fk != branch_id:
                    continue
                if line.analysis_code_fk is not None and line.analysis_code_fk != analysis_code_id:
                    continue
                period = next(
                    (row for row in self.periods(budget) if row.starts_on <= on <= row.ends_on),
                    None,
                )
                if period is None:
                    continue
                planned = money(self._effective(line).get(period.key, "0")) + self._granted(
                    line.id, period.key
                )
                spent = self._actual(budget, line, period.starts_on, period.ends_on)
                if spent + amount > planned:
                    return OverspendCheck(line, budget, period, planned, spent, spent + amount)
        return None

    def hold_for(
        self, actor: AnyActor, check: OverspendCheck, expense_id: uuid.UUID, amount: Decimal
    ) -> BudgetRequest:
        """Ask leave to spend beyond the line, for an expense that waits on the answer."""
        request = BudgetRequest(
            business_id=check.budget.business_id,
            budget_fk=check.budget.id,
            kind="overspend",
            line_fk=check.line.id,
            period_key=check.period.key,
            amount=check.excess if check.excess > ZERO else amount,
            reason=(
                f"An expense of {cents(amount)} takes "
                f"{self.label_of(check.budget.business_id, check.line)} "
                f"to {cents(check.after)} against {cents(check.planned)} planned for "
                f"{check.period.label}."
            ),
            state=RequestState.PENDING.value,
            expense_fk=expense_id,
            requested_by_id=actor.user_id,
        )
        self._db.add(request)
        self._db.flush()
        return request

    def request(
        self,
        actor: AnyActor,
        budget_id: uuid.UUID,
        *,
        kind: str,
        line_id: uuid.UUID,
        period_key: str,
        amount: Decimal,
        reason: str,
        from_line_id: uuid.UUID | None = None,
    ) -> BudgetRequest:
        """A transfer between two lines of an approved budget, or leave to spend beyond one."""
        actor.require("budget.manage")
        budget = self.require(actor, budget_id)
        if budget.state != BudgetState.APPROVED.value:
            raise ConflictError("Transfers and leave to overspend apply to an approved budget.")
        if kind not in {"transfer", "overspend"}:
            raise ValidationError("A transfer or an overspend.", field="kind")
        value = cents(money(amount))
        if value <= ZERO:
            raise ValidationError("An amount above zero.", field="amount")
        if not reason.strip():
            raise ValidationError("A request records why.", field="reason")
        lines = {line.id: line for line in budget.lines}
        if line_id not in lines:
            raise NotFoundError("Budget line not found.")
        if period_key not in {period.key for period in self.periods(budget)}:
            raise NotFoundError("Period not found.")
        if kind == "transfer":
            if from_line_id is None or from_line_id not in lines or from_line_id == line_id:
                raise ValidationError(
                    "A transfer names the line it comes from.", field="from_line_id"
                )
            available = money(self._effective(lines[from_line_id]).get(period_key, "0"))
            if value > available:
                raise ConflictError(f"The line it comes from has {available} for the period.")
        row = BudgetRequest(
            business_id=budget.business_id,
            budget_fk=budget.id,
            kind=kind,
            line_fk=line_id,
            from_line_fk=from_line_id if kind == "transfer" else None,
            period_key=period_key,
            amount=value,
            reason=reason.strip(),
            state=RequestState.PENDING.value,
            requested_by_id=actor.user_id,
        )
        self._db.add(row)
        self._db.flush()
        self._audit.record(
            actor,
            f"budget_{kind}_requested",
            entity_type="budget",
            entity_id=budget.id,
            entity_label=budget.name,
            reason=reason.strip(),
            after={"amount": str(value), "period": period_key},
        )
        return row

    def requests(self, actor: AnyActor, budget_id: uuid.UUID | None = None) -> list[BudgetRequest]:
        _require_view(actor)
        query = select(BudgetRequest).where(BudgetRequest.business_id == actor.business_id)
        if budget_id:
            query = query.where(BudgetRequest.budget_fk == budget_id)
        return list(self._db.scalars(query.order_by(BudgetRequest.created_at.desc())))

    def decide(
        self, actor: AnyActor, request_id: uuid.UUID, *, approve: bool, note: str | None = None
    ) -> BudgetRequest:
        actor.require("budget.approve")
        row = self._db.get(BudgetRequest, request_id)
        if row is None or row.business_id != actor.business_id:
            raise NotFoundError("Request not found.")
        if row.state != RequestState.PENDING.value:
            raise ConflictError("That request has been decided.")
        if row.requested_by_id == actor.user_id and not actor.is_administrator:
            raise ConflictError("A request is decided by someone other than whoever made it.")
        text = (note or "").strip() or None
        if not approve and text is None:
            raise ValidationError("Declining records why.", field="note")
        row.state = (RequestState.APPROVED if approve else RequestState.REJECTED).value
        row.decided_by_id = actor.user_id
        row.decided_at = self._clock.now()
        row.decision_note = text
        self._db.flush()
        if row.expense_fk is not None:
            from nickland.application.services.expenses import ExpenseService

            expenses = ExpenseService(self._db, clock=self._clock)
            if approve:
                expenses.approve(actor, row.expense_fk, budget_cleared=True)
            else:
                expenses.reject(actor, row.expense_fk, reason=text or "Over budget")
        self._audit.record(
            actor,
            f"budget_{row.kind}_{'approved' if approve else 'declined'}",
            entity_type="budget",
            entity_id=row.budget_fk,
            reason=text,
            after={"amount": str(row.amount), "period": row.period_key},
        )
        return row

    # -- internals --------------------------------------------------------------------------

    def window_figures(
        self, budget: Budget, line: BudgetLine, start: date, end: date
    ) -> tuple[Decimal, Decimal]:
        """Budget and actual for a line over any window. A budget period partly inside the
        window counts in proportion to its days within it."""
        effective = self._effective(line)
        planned = ZERO
        for period in self.periods(budget):
            first, last = max(period.starts_on, start), min(period.ends_on, end)
            if last < first:
                continue
            amount = money(effective.get(period.key, "0"))
            planned += amount * ((last - first).days + 1) / period.days
        first, last = max(budget.starts_on, start), min(budget.ends_on, end)
        return cents(planned), self._actual(budget, line, first, last)

    def _effective(self, line: BudgetLine) -> dict[str, str]:
        """The planned amounts with approved transfers in and out."""
        amounts = {key: money(value) for key, value in line.amounts.items()}
        for request in self._db.scalars(
            select(BudgetRequest).where(
                BudgetRequest.kind == "transfer",
                BudgetRequest.state == RequestState.APPROVED.value,
                (BudgetRequest.line_fk == line.id) | (BudgetRequest.from_line_fk == line.id),
            )
        ):
            sign = 1 if request.line_fk == line.id else -1
            amounts[request.period_key] = (
                amounts.get(request.period_key, ZERO) + sign * request.amount
            )
        return {key: str(cents(value)) for key, value in amounts.items()}

    def _granted(self, line_id: uuid.UUID, period_key: str) -> Decimal:
        rows = self._db.scalars(
            select(BudgetRequest.amount).where(
                BudgetRequest.kind == "overspend",
                BudgetRequest.state == RequestState.APPROVED.value,
                BudgetRequest.line_fk == line_id,
                BudgetRequest.period_key == period_key,
            )
        )
        return sum(rows, ZERO)

    def _lines_query(self, budget: Budget, line: BudgetLine, start: date, end: date):  # type: ignore[no-untyped-def]
        query = (
            select(JournalLine)
            .join(Account, Account.id == JournalLine.account_fk)
            .where(
                JournalLine.business_id == budget.business_id,
                JournalLine.state.in_(REPORTABLE),
                JournalLine.posting_date >= start,
                JournalLine.posting_date <= end,
            )
        )
        if line.account_code:
            query = query.where(Account.code == line.account_code)
        else:
            group = self._group(budget.business_id, line.group_code or "")
            query = query.where(Account.group_fk == group.id)
        branch = line.branch_fk or budget.branch_fk
        if branch is not None:
            query = query.where(JournalLine.branch_id == branch)
        if line.analysis_code_fk is not None:
            query = query.where(JournalLine.analysis_code_fk == line.analysis_code_fk)
        return query

    def _actual(self, budget: Budget, line: BudgetLine, start: date, end: date) -> Decimal:
        if end < start:
            return ZERO
        nature = self.nature_of(budget.business_id, line)
        rows = self._db.execute(
            self._lines_query(budget, line, start, end).with_only_columns(
                JournalLine.base_debit, JournalLine.base_credit
            )
        ).all()
        debit = sum((row[0] for row in rows), ZERO)
        credit = sum((row[1] for row in rows), ZERO)
        return cents(credit - debit if nature is Nature.INCOME else debit - credit)

    def nature_of(self, business_id: uuid.UUID, line: BudgetLine) -> Nature:
        if line.account_code:
            account = self._account(business_id, line.account_code)
            income = account.kind == "revenue" or account.code in _INCOME_CODES
        else:
            group = self._group(business_id, line.group_code or "")
            income = group.kind == "revenue"
        return Nature.INCOME if income else Nature.COST

    def label_of(self, business_id: uuid.UUID, line: BudgetLine) -> str:
        if line.account_code:
            account = self._account(business_id, line.account_code)
            return f"{account.code} {account.name}"
        group = self._group(business_id, line.group_code or "")
        return f"{group.name} (all accounts)"

    def analysis_label(self, code_id: uuid.UUID | None) -> str | None:
        if code_id is None:
            return None
        row = self._db.get(AnalysisCode, code_id)
        return f"{row.code} {row.name}" if row else None

    def _account(self, business_id: uuid.UUID, code: str) -> Account:
        row = self._db.scalar(
            select(Account).where(Account.business_id == business_id, Account.code == code)
        )
        if row is None:
            raise NotFoundError(f"No account {code} is in the chart.")
        return row

    def _group(self, business_id: uuid.UUID, code: str) -> AccountGroup:
        row = self._db.scalar(
            select(AccountGroup).where(
                AccountGroup.business_id == business_id, AccountGroup.code == code
            )
        )
        if row is None:
            raise NotFoundError(f"No category {code} is in the chart.")
        return row

    def _editable(self, actor: AnyActor, budget_id: uuid.UUID) -> Budget:
        budget = self.require(actor, budget_id)
        if not BudgetState(budget.state).editable:
            raise ConflictError(
                f"{budget.name} is {BudgetState(budget.state).label.lower()}. An approved "
                "budget changes only by a new version."
            )
        return budget

    def _decide(self, actor: AnyActor, budget: Budget, action: str, note: str | None) -> None:
        self._db.add(
            BudgetDecision(
                business_id=budget.business_id,
                budget_fk=budget.id,
                action=action,
                actor_id=actor.user_id,
                note=note,
                occurred_at=self._clock.now(),
            )
        )
        self._db.flush()

    def _today(self, business_id: uuid.UUID) -> date:
        business = self._db.get(Business, business_id)
        return business_date(self._clock.now(), business.timezone if business else "UTC")


def _require_view(actor: AnyActor) -> None:
    actor.require_any("budget.manage", "budget.approve", "report.view")


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
