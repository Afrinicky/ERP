# Budgets, Forecasting, Profitability and Analytics

Phase 13. PRD §20.7, §29.6, §29.8.

Four kinds of figure meet here: what was **posted** (actual), what was **planned** (budget),
what is **expected** (forecast) and what a quotation **estimated**. Every figure the module
returns carries its basis (`Basis` in `domain/planning.py`), and every screen and export
names it. Nothing in this module posts to the ledger.

## Analysis codes

Departments, projects and cost centres (`AnalysisCode`). A journal line and an expense may
carry one; a budget line may be drawn for one. Actuals for such a line are the postings that
carry the code.

## Budgets

| Concept | Rule |
| --- | --- |
| Columns | Annual, quarterly, monthly or one custom period, from `budget_periods` |
| Lines | One account or one account category, optionally for a branch and an analysis code; each line holds an amount per column |
| States | Draft → in review → approved; an approved version is superseded only by the approval of a later version; a version returned goes back to draft |
| Approval | `budget.approve`, by someone other than the submitter; every step is a `BudgetDecision` (the approval history) |
| Revision | `revise` copies an approved version into a new draft version; the approved one stands until the revision is approved |
| Transfers | Between two lines of an approved budget, for a period, approved by a second person; the budget shown includes them, the original stays beside it |
| Overspend | With the control on (the default), an expense that would take an approved line beyond its period's budget waits for approval, with the reason recorded on the expense and a request on the budget; approving the request approves the expense |

**Comparison.** For each line and period: budget, actual (posted lines on the account or
category, branch and analysis code), the same period a year before, the variance (absolute,
percentage, favourable or adverse by whether the line is income or cost) and utilisation.
"To date" compares the periods begun so far. The forecast outturn is actual for the periods
past, the greater of actual and budget for the period in progress, and the budget for those
to come; it is labelled forecast. Any actual opens onto its postings.

## Cash forecast

Starts from the cash and bank balance posted today and lays out, by date:

| In | Out |
| --- | --- |
| Customer invoices, by due date or the customer's promised date | Supplier invoices due |
| Loan repayments owed to the business | Unpaid expenses and staff claims |
| Planned receipts and new borrowing | Recurring costs as they fall due |
| | Tax obligations by due date |
| | Commissions payable |
| | Loan instalments |
| | Planned purchases and payments |

Daily rows up to 31 days, weekly beyond, over 7, 30, 60, 90, 180 or any horizon to a year.

**Scenarios.** Best, base and downside are seeded; more may be saved. Assumptions: days
customers pay early or late, the share collected, days the business pays early or late,
whether recurring costs are included, extra flows (new borrowing, a large purchase), and an
early loan settlement on a date (using the agreement's own settlement quotation). A what-if
runs any assumptions without saving them.

**Low cash.** A threshold (`forecast.low_cash_threshold`) the business keeps in hand. A
forecast that falls below it, or below nothing, carries a warning.

## Alerts

Low cash on the base forecast; customer invoices overdue; payment promises missed; supplier
invoices, tax and loan instalments due within 14 days or overdue; borrowers in arrears;
statements not reconciled; budget lines overspent in the current period. Each names the
screen it opens.

## Profitability

Facts are realised sale lines: invoice lines from quotation-led orders and register sale
lines (net of refunds). Cost of goods is actual: the stock layers consumed when the goods
were delivered or sold, else the supplier's invoiced cost on the order line. A line with no
actual cost yet is counted in revenue and left out of contribution, and reported as awaiting
cost; it is never given an estimated cost.

| Dimension | From |
| --- | --- |
| Product, category | The line's product |
| Order | The sales order, or the register sale |
| Customer | The invoice's or sale's customer; walk-in otherwise |
| Supplier or source | The quotation line's first source; "From stock" otherwise |
| Branch | The invoice's or sale's branch |
| Sales person | Who issued the quotation, else the invoice; the cashier at the register |
| Affiliate | The quotation's affiliate |
| Register | The register the shift ran on |
| Channel | Quotation-led or point of sale |

Measures: revenue, cost of goods, gross contribution, commission (the accrual for the
quotation line, by the share invoiced), contribution after commission, margin. Optionally,
freight and non-recoverable tax posted straight to cost of sales (50100, 50200) and operating
overheads are allocated by share of revenue, in their own columns.

## Margin erosion

For each quoted order line invoiced in the window, on the quantity invoiced:

| Cause | Measured as |
| --- | --- |
| Price | Realised unit revenue against the quoted unit net price |
| Further discount | Any reduction between the quotation and the order |
| Supplier cost | Actual unit cost against the quoted unit cost |
| Freight and landed charges | Landed charges on the receipts for the line against those quoted |
| Non-recoverable tax | Invoiced against quoted |
| Returns | Credit notes on the line |
| Commission | Earned against quoted |

The causes sum exactly to realised margin less quoted margin. A line bought in for the order
compares supplier invoice, freight and tax separately; a line from stock compares the layer
cost with the quoted cost in full, since the layer already carries its freight and tax.

## Charts

| Chart | Form | Drill to |
| --- | --- | --- |
| Revenue and net profit, with the year before and the budget | Line | Postings |
| Income against expenses | Columns | Postings |
| Cash in, cash out, and the balance (its own chart) | Columns and a line | Postings |
| Where the money goes | Ranked bars | The account's postings |
| Contribution by product, by category | Ranked bars | Sale lines |
| Sales by channel | Columns | Sale lines |
| Tax position, with the net owed (its own chart) | Columns and a line | Postings |
| Receivables and payables by age | Columns | Register lines |
| Quoted cost against actual cost, by supplier | Grouped bars | Order lines |
| This month against last month and budget | Grouped bars | Postings |

Drawn as inline SVG by `ui/src/components/charts.tsx`; nothing is fetched. One axis per
chart: a level (a balance) is drawn on its own chart beneath the flows. Colour follows the
series in the order given, from the theme's chart tokens; each preset's four chart colours
are at least 3:1 against the surface and separate under every colour-vision deficiency.
Budgets, forecasts and estimates are drawn dashed or hatched and named in the legend. Every
chart has a crosshair or per-bar tooltip, a table view, CSV and SVG export, and opens the
records behind a selected point.

## Permissions

`report.view` reads everything here. `budget.manage` draws budgets, analysis codes,
scenarios, planned items and the low-cash threshold, and asks for transfers.
`budget.approve` approves budgets and requests, and turns the overspend control on or off.

## Verification

`tools/verify_phase_13.py` checks the five exit criteria. `tests/integration/test_budgets.py`,
`test_profitability.py`, `test_erosion.py` and `tests/e2e/test_planning_api.py` run on SQLite
and PostgreSQL.
