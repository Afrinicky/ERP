# Management Reporting and Period Close

Phase 14. PRD §8, §20.6, §29.8, §29.9, AC-22.

A report is data before it is a page (`domain/reporting.py`): a **header**, a few
**headline figures**, one or more **tables**, the **checks** that tie it to the ledger, and
any **commentary**. The same payload is drawn on screen, kept in a saved run and written to
PDF, Excel and CSV. Every figure is rounded once (`normalise`), so no format can disagree
with another.

## The header

Every report states its reporting period, currency, accounting basis (accrual or cash for
ledger reports; "Operational records" for the rest), when it was generated and by whom,
each filter and grouping applied, and the window it is compared with.

## Windows and comparison

| Window | Rule |
| --- | --- |
| Day, week, month, quarter | The one containing the chosen day; weeks run Monday to Sunday |
| Year | The financial year containing the chosen day |
| Custom | Any range up to ten years |

| Comparison | Window |
| --- | --- |
| Prior period | The calendar month, quarter or year before; for a day, week or custom range, the same length immediately before |
| Same period last year | The window a year earlier |
| Budget | The approved budgets covering the window, prorated by days for a period partly inside it (Financial Summary) |

A compared column gains the prior figure, the change and the percentage. No percentage is
shown across a change of sign or from nothing. A line present only in the earlier window is
kept, at nothing now, beside the line it followed.

## The twelve operational reports

| Report | Contents | Groupings and filters |
| --- | --- | --- |
| Quotation Performance | Requests, issued, accepted, rejected, expired, open; value issued and accepted; conversion | Sales person, customer, branch, month |
| Order Profitability | Revenue, quoted cost, quoted charges and tax, actual cost, variance, affiliate cost, contribution, margin | Branch, customer |
| Supplier Comparison | Quotations, availability, lead time, orders, receipts on time; price history with the cheapest latest price as the mark | Supplier, product |
| Purchasing | Orders, goods received, supplier invoices, paid, outstanding; by supplier and by order | Branch, supplier |
| Sales | Realised sales, net of credit notes and refunds | Customer, product, category, month, channel, sales person, branch |
| Point of Sale Shifts | Each shift's cashier, float, takings, refunds, cash movements, expected and counted cash, variance; tender split | Branch, register, cashier |
| Instalment Collections | Due, collected, collection rate, overdue and outstanding; each plan and each instalment due | Branch, customer |
| Affiliate Commission | Eligible sales, effective rate, accrued, approved, paid, outstanding | Affiliate |
| Inventory Position | On hand, quarantine, reserved, available, incoming, reorder level, unit cost, value; batches expiring within 90 days | Warehouse, product |
| Stock Movements | By kind of movement and line by line, reservations aside | Warehouse, product |
| Delivery | Lines awaiting assembly, shipments awaiting dispatch, deliveries not yet signed for, failed deliveries and exceptions | Branch, customer |
| Financial Summary | Headline figures, profit and loss, cash flow, operating expenses | Accounting basis |

## The management pack

`ManagementPackService.build`, for a month by default and compared with the month before:

1. Executive summary: sales including tax, net revenue, gross profit, operating expenses,
   net profit, cash movement, closing cash, owed by customers and to suppliers, with a
   sentence on each movement.
2. Profit and loss, with drill to each account's postings.
3. Statement of financial position, at the window's end against the prior window's end.
4. Cash flow by operating, investing and financing activity.
5. Trial balance.
6. Sales by channel, category, leading products and customers, and sales person; instalment
   collection.
7. Profitability: highest and lowest products, orders and channels.
8. Purchasing by supplier; quoted against actual cost by supplier.
9. Expense analysis, largest first, naming any line that rose by a quarter and at least
   500.
10. Tax position: assessed and paid in the window; outstanding, overdue, recoverable and
    withheld today.
11. Receivables and payables by age.
12. Debt and loan schedule.
13. Inventory: value, turnover, slow-moving lines, expiring batches and adjustments.
14. Budget variance, favourable or adverse.
15. Key risks: the alerts of Phase 13, and any check that fails.

**Reconciliation.** The pack carries its checks, and says on its face whether all agree:

| Check | Reported | Against |
| --- | --- | --- |
| Trial balance | Debits | Credits |
| Net profit | The profit and loss | Income less expenses posted in the window |
| Position | Assets | Liabilities and equity |
| Opening and closing cash | The cash flow | The cash and bank accounts |
| Customers | Unpaid customer invoices | The customer control account |
| Suppliers | Unpaid supplier invoices | The supplier control account |
| Loans | Principal outstanding by agreement | The loan accounts, borrowing and lending |
| Stock | The cost layers | Each inventory account |

## Sensitivity

`report.view` opens the reports. A report that discloses cost or margin (Order
Profitability, Supplier Comparison, Purchasing, Financial Summary and the management pack)
opens only to those holding `report.export_sensitive`; in any other report, cost and value
columns are withheld from them, and the report says so. Exporting needs `report.export`.
Saved runs and deliveries follow the same rule.

## Exports

From the same payload (`documents/report_export.py`):

| Format | Figures |
| --- | --- |
| CSV | Each figure as its exact decimal; header lines above, a section at a time |
| Excel | A workbook written without a library: numbers as numbers, money formatted with negatives in parentheses, dates as dates |
| PDF | WeasyPrint, A4 (landscape for wide tables), each figure written as the screen writes it: grouped with a space, two places, negatives in parentheses |

## Saved runs and notes

A run is kept exactly as generated (`ReportRun`); reopening it shows the figures of that
day, whatever has been posted since. A note may be written beneath any section, and each is
audited. Runs are listed, opened, exported and removed from the Saved tab.

## Schedules

A schedule (`ReportSchedule`) runs a report daily, weekly or monthly at an hour of the
business's local day, for the day, week or month just ended. A background task in the
service runs due schedules once a minute, as the system, keeps each run and delivers it to
each recipient's inbox (`ReportDelivery`). A recipient must be able to read the report;
one who cannot is refused when the schedule is made and skipped if their access later
changes.

## Period close

A period closes only when every gate passes, each recorded with its measurements:

| Measured | Confirmed by a person |
| --- | --- |
| Earlier periods closed | Accruals, prepayments and recurring charges reviewed |
| No draft or pending entries | Stock counted or confirmed against the ledger |
| Every entry balances | Tax obligations for the period reviewed |
| Trial balance balances | Management report for the period reviewed |
| Control accounts agree with their registers | |
| Till shifts counted and closed | |
| Expenses decided | |
| Bank statements reconciled | |

A closed period refuses every posting. **Reopening** is asked for with a reason of at least
ten characters (`request_reopen`) and decided by someone else holding `period.reopen`
(`decide_reopen`). The administrator may approve their own request; it is permitted and
recorded as self-approved. Periods reopen in reverse order. The audit entry names the
requester, the approver and the reason.

## Permissions

| Action | Permission |
| --- | --- |
| Run and read reports | `report.view` |
| Cost and margin reports, cost columns | `report.export_sensitive` |
| Export, save notes, schedule, remove runs | `report.export` |
| Confirm checklist steps, close, request reopening | `period.close` |
| Decide a reopening | `period.reopen` |

## Verification

`tools/verify_phase_14.py` checks the four exit criteria and the twelve reports and
scheduling. `tests/unit/test_reporting_domain.py`, `tests/integration/test_reporting.py`
and `tests/e2e/test_reporting_api.py` run on SQLite and PostgreSQL.
