"""Contracts for budgets, forecasts, profitability and the charts. PRD §20.7, §29.6, §29.8.

Every figure crosses the boundary as a string, and every one is accompanied by its basis:
actual, budget, forecast or estimate.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import Field

from nickland.interface.finance_schemas import _Figures
from nickland.interface.schemas import Contract, Reply


class AnalysisCodeContract(Contract):
    kind: str = Field(pattern="^(department|project|cost_centre)$")
    code: str = Field(min_length=1, max_length=24)
    name: str = Field(min_length=1, max_length=120)


class AmendAnalysisCodeContract(Contract):
    name: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None


class AnalysisCodeReply(Reply):
    id: uuid.UUID
    kind: str
    kind_label: str
    code: str
    name: str
    is_active: bool


class BudgetContract(Contract):
    name: str = Field(min_length=1, max_length=120)
    period_kind: str = Field(pattern="^(annual|quarterly|monthly|custom)$")
    starts_on: date
    ends_on: date
    branch_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    assumptions: str | None = Field(default=None, max_length=4000)


class BudgetLineContract(_Figures):
    account_code: str | None = Field(default=None, max_length=8)
    group_code: str | None = Field(default=None, max_length=8)
    branch_id: uuid.UUID | None = None
    analysis_code_id: uuid.UUID | None = None
    amounts: dict[str, Decimal] = Field(default_factory=dict)
    total: Decimal | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)


class BudgetLinesContract(Contract):
    lines: list[BudgetLineContract]


class NoteContract(Contract):
    note: str | None = Field(default=None, max_length=1000)


class ReasonContract(Contract):
    reason: str = Field(min_length=1, max_length=1000)


class BudgetRequestContract(_Figures):
    kind: str = Field(pattern="^(transfer|overspend)$")
    line_id: uuid.UUID
    from_line_id: uuid.UUID | None = None
    period_key: str = Field(min_length=3, max_length=8)
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=1, max_length=1000)


class DecisionContract(Contract):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


class ToggleContract(Contract):
    enabled: bool


class ToggleReply(Reply):
    enabled: bool


class PeriodReply(Reply):
    key: str
    label: str
    starts_on: date
    ends_on: date


class BudgetLineReply(Reply):
    id: uuid.UUID
    position: int
    account_code: str | None
    group_code: str | None
    label: str
    branch_id: uuid.UUID | None
    analysis_code_id: uuid.UUID | None
    analysis: str | None
    amounts: dict[str, Decimal]
    total: Decimal
    note: str | None


class DecisionReply(Reply):
    action: str
    actor: str | None
    note: str | None
    occurred_at: datetime
    version: int


class BudgetRequestReply(Reply):
    id: uuid.UUID
    budget_id: uuid.UUID
    kind: str
    line_id: uuid.UUID
    from_line_id: uuid.UUID | None
    period_key: str
    amount: Decimal
    reason: str
    state: str
    expense_id: uuid.UUID | None
    requested_by: str | None
    decided_by: str | None
    decision_note: str | None


class BudgetSummaryReply(Reply):
    id: uuid.UUID
    name: str
    period_kind: str
    starts_on: date
    ends_on: date
    state: str
    state_label: str
    version: int
    total: Decimal
    lines: int
    owner: str | None
    approved_by: str | None


class BudgetDetailReply(BudgetSummaryReply):
    branch_id: uuid.UUID | None
    owner_id: uuid.UUID | None
    assumptions: str | None
    periods: list[PeriodReply]
    budget_lines: list[BudgetLineReply]
    decisions: list[DecisionReply]
    versions: list[BudgetSummaryReply]
    requests: list[BudgetRequestReply]


class VarianceReply(Reply):
    absolute: Decimal
    percent: Decimal | None
    favourable: bool | None
    utilisation: Decimal | None


class CellReply(Reply):
    period_key: str
    original: Decimal
    budget: Decimal
    actual: Decimal
    prior: Decimal
    variance: VarianceReply


class LineComparisonReply(Reply):
    line_id: uuid.UUID
    label: str
    nature: str
    analysis: str | None
    cells: list[CellReply]
    total: CellReply
    to_date: CellReply
    forecast: Decimal


class ComparisonReply(Reply):
    budget_id: uuid.UUID
    as_of: date
    periods: list[PeriodReply]
    lines: list[LineComparisonReply]
    income: CellReply
    costs: CellReply
    income_to_date: CellReply
    costs_to_date: CellReply
    bases: dict[str, str]


class PostingReply(Reply):
    posting_date: date
    entry_id: uuid.UUID
    number: str | None
    account_code: str
    account_name: str
    narration: str | None
    amount: Decimal


# -- forecast ------------------------------------------------------------------------------


class FlowContract(_Figures):
    on: date
    amount: Decimal = Field(gt=0)
    inward: bool
    label: str = Field(min_length=1, max_length=200)


class AssumptionsContract(_Figures):
    collection_delay_days: int = Field(default=0, ge=-90, le=365)
    collection_rate: Decimal = Field(default=Decimal(100), ge=0, le=100)
    payment_delay_days: int = Field(default=0, ge=-90, le=365)
    include_recurring: bool = True
    include_overdue_payables: bool = True
    extra_flows: list[FlowContract] = Field(default_factory=list)
    settle_loan_id: uuid.UUID | None = None
    settle_on: date | None = None


class ScenarioContract(Contract):
    name: str = Field(min_length=1, max_length=80)
    assumptions: AssumptionsContract
    notes: str | None = Field(default=None, max_length=1000)


class ScenarioReply(Reply):
    id: uuid.UUID
    name: str
    kind: str
    kind_label: str
    is_default: bool
    assumptions: dict[str, object]
    notes: str | None


class WhatIfContract(Contract):
    horizon_days: int = Field(default=30, ge=1, le=366)
    scenario_id: uuid.UUID | None = None
    assumptions: AssumptionsContract


class PlannedContract(_Figures):
    expected_on: date
    amount: Decimal = Field(gt=0)
    inward: bool
    label: str = Field(min_length=1, max_length=200)
    scenario_id: uuid.UUID | None = None


class PlannedReply(Reply):
    id: uuid.UUID
    expected_on: date
    amount: Decimal
    inward: bool
    label: str
    scenario_id: uuid.UUID | None


class ThresholdContract(_Figures):
    amount: Decimal | None = Field(default=None, ge=0)


class ThresholdReply(Reply):
    amount: Decimal | None


class ForecastRowReply(Reply):
    starts_on: date
    ends_on: date
    opening: Decimal
    inflow: Decimal
    outflow: Decimal
    closing: Decimal
    by_class: dict[str, Decimal]
    below_threshold: bool


class FlowItemReply(Reply):
    on: date
    cls: str
    cls_label: str
    inward: bool
    amount: Decimal
    reference: str
    counterparty: str | None
    source_type: str
    source_id: uuid.UUID | None


class ForecastReply(Reply):
    basis: str
    basis_label: str
    scenario: str
    scenario_kind: str
    assumptions: dict[str, object]
    starts_on: date
    ends_on: date
    opening_cash: Decimal
    closing_cash: Decimal
    lowest: Decimal
    lowest_on: date
    threshold: Decimal | None
    bucket_days: int
    rows: list[ForecastRowReply]
    totals: dict[str, Decimal]
    class_labels: dict[str, str]
    items: list[FlowItemReply]
    warnings: list[str]


class AlertReply(Reply):
    severity: str
    kind: str
    title: str
    detail: str
    amount: Decimal | None
    count: int
    view: str


# -- analytics -----------------------------------------------------------------------------


class ChartListReply(Reply):
    id: str
    title: str


class SeriesReply(Reply):
    key: str
    label: str
    basis: str
    basis_label: str
    role: str


class PointReply(Reply):
    key: str
    label: str
    values: dict[str, Decimal]
    drill: dict[str, str]


class ChartReply(Reply):
    id: str
    title: str
    kind: str
    unit: str
    starts_on: date
    ends_on: date
    interval: str | None
    series: list[SeriesReply]
    points: list[PointReply]
    note: str | None


class ProfitRowReply(Reply):
    key: str
    label: str
    lines: int
    revenue: Decimal
    cost: Decimal
    gross: Decimal
    commission: Decimal
    after_commission: Decimal
    direct: Decimal
    overheads: Decimal
    after_allocation: Decimal
    margin: Decimal | None
    pending_lines: int
    pending_revenue: Decimal


class ProfitabilityReply(Reply):
    basis: str
    basis_label: str
    dimension: str
    dimension_label: str
    starts_on: date
    ends_on: date
    rows: list[ProfitRowReply]
    total: ProfitRowReply
    allocated: bool
    direct_pool: Decimal
    overhead_pool: Decimal


class FactReply(Reply):
    source_type: str
    source_id: uuid.UUID
    reference: str
    on: date
    description: str
    channel: str
    quantity: Decimal
    revenue: Decimal
    cost: Decimal | None
    commission: Decimal


class ErosionRowReply(Reply):
    order_id: uuid.UUID
    order_number: str
    order_line_id: uuid.UUID
    customer: str
    description: str
    supplier: str
    quantity_quoted: Decimal
    quantity_invoiced: Decimal
    quoted_margin: Decimal
    realised_margin: Decimal
    difference: Decimal
    quoted_cost: Decimal
    actual_cost: Decimal | None
    causes: dict[str, Decimal]
    complete: bool


class ErosionReply(Reply):
    rows: list[ErosionRowReply]
    totals: dict[str, Decimal]
    cause_labels: dict[str, str]
    quoted_margin: Decimal
    realised_margin: Decimal
    pending: int
    bases: dict[str, str]
