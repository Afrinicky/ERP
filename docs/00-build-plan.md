# Build Plan

Requirements baseline: PRD v1.4. Supersedes the ten-phase list in PRD §13, which was
authored against v1.0 and omits the Tax & Statutory module (§19), the Financial Management
module (§20, §29) and Debt & Financing (§25). See [ADR-0005](decisions/adr-0005-phase-recut.md).

## Governing principles

1. **Ledger before postings.** The double-entry engine is delivered before any module that
   posts financial consequence. PRD §13 placed finance after the point of sale; that ordering
   would require every POS transaction to be reposted once the ledger arrived.
2. **Taxation before pricing.** Tax rules are an input to the cost build-up, not a decoration
   applied afterwards. The tax engine precedes the pricing engine.
3. **One service, two deployments.** No phase introduces a business rule that exists only in
   standalone mode or only in LAN mode.
4. **Each phase ships.** Every phase terminates in an installable, demonstrable increment with
   passing automated tests. No phase exists solely to produce internal scaffolding.
5. **No phase closes with a known accounting defect.** Reconciliation is an exit criterion, not
   a later correction.

## Progress

| Phase | State | Evidence |
| --- | --- | --- |
| 1 — Platform, Identity and Design System | **Delivered** | Every exit criterion below verified by `tools/verify_phase_1.py`; 269 tests pass against SQLite and PostgreSQL |
| 2 — Master Data | Next | |
| 3 to 16 | Not started | |

The two criteria that cannot be verified in this environment are marked in Phase 1
below: the signed installer on a clean Windows 11 machine, and operation with the
network interface disabled rather than merely absent. Both require Windows hardware.

## Stage summary

| Stage | Phases | Outcome |
| --- | --- | --- |
| A — Platform Foundation | 1–2 | Secure, themed, installable shell with master data |
| B — Fiscal Core | 3–4 | Balanced ledger and a configurable tax engine |
| C — Commercial Core | 5–7 | Quotation, document production and procurement |
| D — Operations | 8–10 | Inventory, dispatch and the point of sale |
| E — Financial Command | 11–14 | Treasury, debt, forecasting and management reporting |
| F — Deployment Editions | 15–16 | Multi-user LAN edition and cloud readiness |

---

## Stage A — Platform Foundation

### Phase 1 — Platform, Identity and Design System

Satisfies PRD §6.1, §7, §9.3, §9.4, §9.7, §10, §28, §31, §13 Phase 1.

| Workstream | Scope |
| --- | --- |
| Repository | Package structure, dependency pinning, linting, formatting, type checking, pre-commit controls, continuous integration |
| Desktop container | pywebview host window, local FastAPI service lifecycle, single-instance enforcement, splash and health reporting |
| Persistence | SQLAlchemy 2.x base, Alembic baseline, SQLite engine configuration, decimal type adapters, transaction helpers |
| Identity | User records, password hashing with Argon2id, session tokens, lockout, password policy, first-run administrator provisioning |
| Authorisation | Role and permission registry, service-level enforcement decorator, eight seeded roles per PRD §3 |
| Audit | Append-only audit event store recording actor, timestamp, action, entity, reason and before/after values |
| Business configuration | Business identity, branches, warehouses, addresses, tax identifiers, currency, timezone, numbering sequences |
| Custom fields | Definition registry and value storage for products, parties and transactions |
| Design system | Token schema, the Charcoal and Navy presets, theme editor with contrast validation, preview and reset, component library, application shell, navigation |
| Backup | Scheduled database and attachment backup, retention, manual restore, restore verification |
| Health | Service, database, storage, backup and worker-queue health checks |

**Exit criteria**

| Criterion | State |
| --- | --- |
| Signed Windows installer produces a working application on a clean Windows 11 machine | Outstanding — requires Windows hardware and a code-signing certificate |
| Application starts, operates and shuts down with no network interface available | Partly verified — the absence of a network is reported as unavailable rather than failure, and no workflow depends on it; a Windows machine with the interface disabled remains to be tested |
| An administrator completes first-run setup, selects a preset, edits brand colours against contrast validation, and restores the defaults | Verified |
| A permission denial is raised by the application service when the interface control is bypassed by direct API call | Verified |
| Backup and restore are demonstrated against a populated database | Verified |
| Every mutation recorded in Phase 1 appears in the audit store with before and after values | Verified |

Run `tools/verify_phase_1.py` to reproduce the verified criteria against a throwaway
database.

### Phase 2 — Master Data

Satisfies PRD §6.1, §13 Phase 2.

| Workstream | Scope |
| --- | --- |
| Catalogue | Products and services, categories, variants, attributes, SKU, barcodes, images, units of measure and conversions |
| Item policies | Stocked, non-stock procurement-on-demand, service and bundle classifications; batch, serial and expiry policy per product |
| Parties | Customers, suppliers, contacts, addresses, payment terms, credit limits, tax treatment |
| Affiliates | Referrer profiles, payment details, eligibility scope |
| Commercial data | Price lists, customer-specific pricing, supplier product links, supplier lead times |
| Data loading | Validated spreadsheet import with dry-run preview, error reporting and rollback |

**Exit criteria**

- A product carrying variants, attributes, two units of measure, a barcode and three custom
  fields is created, amended and retrieved without source-code change.
- Non-stock items require no stock balance anywhere in the system.
- Import of a 1,000-line catalogue completes with a pre-commit error report and a clean rollback
  on rejection.
- Search by SKU, name, barcode and party returns in under 200 ms against 50,000 catalogue rows.

---

## Stage B — Fiscal Core

### Phase 3 — Accounting Core and General Ledger

Satisfies PRD §6.12, §29.2, §29.8 in part, §10. Advanced ahead of PRD §13 Phase 8.

| Workstream | Scope |
| --- | --- |
| Chart of accounts | Account groups, codes, normal balances, statement mapping, seeded default chart |
| Posting engine | Balanced journal entries, idempotency keys, reversal entries, posting-period validation |
| Journals | Manual journals with maker-checker approval, narration, effective date, attachments |
| Subledgers | Customer, supplier, employee, agent, authority, lender and borrower subledgers reconciled to control accounts |
| Periods | Accounting periods, locking, controlled reopening with reason and approval, year-end close, retained earnings |
| Reporting | Trial balance, general ledger, journal register, account activity, profit and loss, balance sheet, cash-flow statement |
| Bases | Accrual and cash-basis presentation, with the basis stated on every report |

**Exit criteria**

- An unbalanced journal cannot be posted.
- A posted entry cannot be edited; correction is only possible by reversal or audited adjustment.
- Posting into a closed period is rejected; reopening requires permission, reason and approval,
  and is recorded in the audit store.
- A repeated posting carrying the same idempotency key creates exactly one journal entry.
- Trial balance sums to zero, and the profit and loss, balance sheet and cash-flow statement
  reconcile to it for any selected period.
- Every subledger balance equals its control account balance.

### Phase 3.5 — Brand Identity and Document Rendering

Satisfies PRD §6.6 in part, §6.7, §7.1, §28.1, AC-05, AC-06.
Specification: [document designer](06-document-designer.md).

Brought forward from Phase 6 at the pilot's request, and built against two of their own
pro forma invoices. Sales and the point of sale can then issue through a renderer that
exists rather than each growing a print path that would later have to be unpicked.

| Workstream | Scope |
| --- | --- |
| Brand | The supplied mark as vector paths, a palette derived from it, offered first at setup |
| Page model | A4, A5, Letter, Legal, and the 80mm and 58mm thermal rolls, with their true printable widths |
| Render model | Issuer, parties, lines, totals, tax summary, delivery, terms, banking, signatures, amount in words |
| Rendering | HTML and CSS composition, PDF by WeasyPrint, and plain text for a printer driven directly |
| Templates | A stored, versioned definition the renderer reads and the Phase 6 editor will write |
| Defaults | The twelve document types of PRD §6.6, seeded at provisioning with one published version each |
| Preview | Sample data drawn from the installation's own identity, watermarked and never issuable |
| Issue | Render, then keep the bytes with a content hash; a reprint serves the snapshot |

**Exit criteria**

- Every one of the twelve default types renders to a PDF without error.
- A receipt renders on both the 80mm and the 58mm roll, and as plain text at 42 and 32 columns.
- A waybill, delivery note and packing list disclose no price, on any line or in any total.
- The same context renders identical markup twice, so a stored snapshot is worth storing.
- A reprint after a template amendment returns the bytes that were issued, not a re-render.
- A document number is never reused.
- The brand palette passes the contrast audit on every audited pairing.

---

### Phase 4 — Tax and Statutory Engine

Satisfies PRD §19, §21, §24, AC-13 to AC-17.

| Workstream | Scope |
| --- | --- |
| Rule configuration | Name, category, authority, jurisdiction, identifier, calculation method, rate, currency, effective dates, applicability conditions, filing frequency, due-date rule, account mapping |
| Calculation methods | Percentage, fixed amount, per unit, tiered, threshold-based, formula-based; configured cascading where legally applicable |
| Treatments | Inclusive, exclusive, exempt, zero-rated, withholding, recoverable, non-recoverable |
| Applicability | Evaluation by product, service, supplier, customer, transaction type, branch, import and jurisdiction |
| Evaluation result | Applied, Not applicable, Exempt or relieved, Needs review, Overridden — visible on every transaction |
| Effective dating | Prospective-only rate changes; posted transactions retain their original tax snapshot |
| Overrides | Permission-controlled, reasoned, timestamped, evidence-backed, optionally approval-gated |
| Registers | Overview, Outstanding, Paid, Withheld, Recoverable, Import and Procurement Charges, Returns and Filings, Tax Rules, Reconciliation |
| Settlement | Full and partial payment, allocation across obligations, unapplied payments, evidence attachment, credit carry-forward |

**Exit criteria**

- A newly configured mandatory rule applies automatically to every subsequent matching
  transaction and to none that preceded its effective date.
- Withdrawing a rule leaves all posted documents numerically unchanged.
- A transaction missing required tax data is blocked from posting unless an authorised exception
  is recorded with a reason.
- The Outstanding, Paid, Withheld and Recoverable registers each reconcile to their source entries
  and to the general ledger.
- A rule used in a posted transaction cannot be deleted.
- Every tax amount traces to its source line and is counted once only when the supplier invoice
  or import charge is subsequently entered.

---

## Stage C — Commercial Core

### Phase 5 — Quotation, Costing and Pricing

Satisfies PRD §6.2, §6.3, §6.4, §6.5, §13 Phase 3, AC-01 to AC-04.
Specification: [pricing engine](04-pricing-engine.md).

| Workstream | Scope |
| --- | --- |
| Customer request | RFQ capture — reference, customer, contact, due date, delivery address, requested lines, specifications |
| Supplier sourcing | Supplier RFQ, supplier quotation capture, side-by-side comparison on price, availability, lead time, freight, tax and terms |
| Source selection | Per-line source selection across existing stock and multiple suppliers in one customer quotation |
| Cost build-up | Supplier cost, transport allocation by seven configurable methods, other cost categories, tax components held separately |
| Margin | Markup on cost and target gross margin as distinct methods |
| Affiliate commission | Eligibility by product, category, selected lines or all eligible lines; fixed or percentage; effective-dated rates |
| Payment plans | Outright and instalment pricing; fixed or percentage uplift configurable by repayment duration |
| Quotation lifecycle | Draft, pending approval, issued, accepted, rejected, expired, superseded; revisions and validity dates |
| Cost preservation | Estimated cost at issue preserved; actual cost recorded after procurement; variance exposed |
| Confidentiality | Internal costing, margin and commission segregated from customer-facing values |

**Exit criteria**

- One customer quotation sources lines from stock and from three suppliers, and each line
  retains its source and cost internally while the customer document shows one coherent offer.
- The cost build-up is reproducible from stored inputs and every component is separately visible.
- Commission applies only within its configured scope.
- Instalment pricing follows the selected duration and displays deposit, instalment amount,
  frequency, first and final due dates and total payable.
- A supplier price change after issue does not alter the issued quotation, and the resulting
  variance is reported before procurement.
- Pricing calculations are exact to four decimal places with no binary floating-point arithmetic
  anywhere in the path.

### Phase 6 — Document Designer and Rendering

Satisfies PRD §6.6, §6.7, §13 Phase 4, AC-05, AC-06.
Specification: [document designer](06-document-designer.md).

| Workstream | Scope |
| --- | --- |
| Template model | Document types, data sources, numbering sequences, versioning, internal and customer-facing classification |
| Visual editor | Drag-and-drop section, field, text, image, logo, signature and QR placement with an accessible keyboard-driven equivalent |
| Table editor | Column addition, removal, reordering, renaming, resizing, labels, widths, alignment, formats and visibility |
| Field palette | Fields grouped by business, customer, supplier, transaction, product, payment, delivery, tax and custom |
| Conditional logic | Visibility conditions and calculated fields through a constrained, non-executing expression grammar |
| Page composition | Page size, orientation, margins, typography, colour, borders, table styles, page breaks, repeating headers, carried-forward totals, pagination |
| Preview | Live preview against sample data and against a real draft transaction |
| Rendering | PDF generation, printing, reprinting, attachment to source transaction |
| Preservation | Rendered snapshot stored at issue; issued documents reproducible after template amendment |
| Portability | Authorised template export, import and duplication |
| Defaults | Professional default templates for pro forma, quotation, invoice, receipt, waybill, delivery note, packing list, purchase order, GRN, statement, payment voucher and credit note |
| Acknowledgement | Capture of signed or scanned proof of receipt with recipient, signature, timestamp, location and comments, preserved unaltered |

**Exit criteria**

- An administrator adds, removes and reorders columns and sections, and previews the result,
  without developer intervention.
- An administrator creates a wholly new document type with its own fields and numbering sequence.
- A customer-facing template cannot be configured to expose supplier cost, margin or commission;
  an automated test asserts their absence from rendered customer output.
- A document issued under template version 1 renders identically after the template advances
  to version 2.
- Expression evaluation is proven unable to execute arbitrary code.
- Document numbers are unique per business, branch and document type under concurrent issue.

### Phase 7 — Sales Orders and Procurement

Satisfies PRD §6.8, §13 Phase 5, AC-07.

| Workstream | Scope |
| --- | --- |
| Acceptance | Customer purchase order or formal confirmation evidence recorded against the quotation |
| Conversion | Accepted quotation converted to sales order without re-entry |
| Purchase orders | Supplier purchase orders generated from sales orders and from replenishment need; several orders may serve one customer order |
| Partial handling | Partial orders, partial receipts, backorders and approved substitutions |
| Supplier billing | Supplier invoices, credit notes and three-way matching against purchase order and goods received note with variance approval |
| Payables | Supplier payables raised through the ledger, with ageing and payment evidence |
| Evidence | Supplier quotations, invoices, receipts and payment evidence attached to source records |

**Exit criteria**

- An accepted order links to its supplier purchase orders and to the stock it draws on.
- Quantity and price variance between purchase order, receipt and invoice is detected and
  requires approval.
- A supplier credit purchase creates a payable; subsequent payment reduces the payable and cash
  without creating a second expense.
- Cost variance between quoted and actual procurement cost is reported per line and per order.

---

## Stage D — Operations

### Phase 8 — Inventory, Receiving and Assembly

Satisfies PRD §6.11, §13 Phase 6 in part. See [ADR-0004](decisions/adr-0004-inventory-valuation.md).

| Workstream | Scope |
| --- | --- |
| Stock ledger | Append-only movement ledger for receipt, issue, transfer, reservation, return, damage, expiry, count and adjustment |
| Quantity states | On hand, available, reserved, quarantined, incoming and in transit |
| Traceability | Batch and lot, serial and expiry tracking by product policy; manufacturer and inspection data |
| Goods receipt | GRN against purchase order, inspection, quarantine, acceptance and rejection; rejected stock never available |
| Valuation | Moving weighted average over a cost-layer-capable ledger; FIFO selectable per product policy |
| Cost of sales | Cost of goods sold posted to the ledger on issue under the configured policy |
| Assembly | Workspace reporting required, available, reserved, incoming, assembled and dispatched quantities per order |
| Locations | Warehouse, location and optional bin; transfers between them |
| Controls | Stock count, variance review, low-stock, expiry and valuation reporting |

**Exit criteria**

- The stock ledger is append-only; corrections are reversals, never amendments.
- Quantity states reconcile to the ledger at every instant.
- Inventory valuation reconciles to the inventory control account in the general ledger.
- Rejected and quarantined stock is excluded from availability and from reservation.
- A batch-tracked item cannot be issued without a batch, and an expired batch is blocked
  unless an authorised override is recorded.
- Concurrent reservation of the final available unit succeeds exactly once.

### Phase 9 — Dispatch and Proof of Delivery

Satisfies PRD §6.9, §13 Phase 6 in part, AC-08.

| Workstream | Scope |
| --- | --- |
| Preparation | Pick lists and packing lists generated from assembled orders |
| Documents | Waybills and delivery notes, with support for several waybills per order |
| Modes | Carrier, own vehicle, courier and customer collection |
| Dispatch record | Date, carrier, driver, vehicle, destination, package count, tracking reference |
| Acknowledgement | Signed or scanned proof of delivery with recipient details, preserved unaltered |
| Exceptions | Failed delivery, short delivery, damage and returns, with financial consequence posted |

**Exit criteria**

- A waybill and its signed proof of receipt are linked to the originating order and reproducible.
- Partial delivery leaves the correct residual quantity outstanding.
- A delivery exception posts its stock and financial consequence through the ledger.

### Phase 10 — Point of Sale

Satisfies PRD §4.3, §5.2, §6.10, §13 Phase 7, AC-09, AC-10.
See [ADR-0009](decisions/adr-0009-pos-payment-confirmation.md).

| Workstream | Scope |
| --- | --- |
| Register | Searchable product cards, categories, image, SKU, barcode, price and stock status |
| Input | Barcode scanner support and a complete keyboard shortcut map |
| Cart | Persistent cart, quantity adjustment, line removal, permitted discounts, customer-specific pricing with permission-controlled override |
| Customer | Walk-in or registered customer selection |
| Tender | Cash, Mobile Money, card, bank transfer, on-account and split tender; cash received, change due, denomination buttons |
| Confirmation | Pending, confirmed and failed states for electronic tender; a prompt is never treated as payment |
| Suspension | Parked sales and retrieval |
| Shifts | Cashier shift, opening float, cash in and out, denomination count, drawer reconciliation, variance reason, supervisor sign-off |
| Output | Thermal and A4 receipts, optional customer display |
| Reversals | Returns, exchanges, refunds and voids under authorisation with full audit trail |

**Exit criteria**

- A cash sale completes in under five seconds from scan to receipt and posts revenue, tax,
  tender movement and cost of goods sold through the ledger.
- Cash sales complete with no network interface available.
- An initiated but unconfirmed electronic payment leaves the sale incomplete and the tender pending.
- A duplicated payment callback posts once only.
- Shift close reconciles expected to counted tender, and a variance requires a reason and sign-off.
- A refund is impossible without authorisation and is fully traceable to the original sale.

---

## Stage E — Financial Command

### Phase 11 — Financial Management Core

Satisfies PRD §20.1 to §20.5, §29.1, §29.3 to §29.5, §13 Phase 8, AC-18 to AC-20.

| Workstream | Scope |
| --- | --- |
| Command centre | Role-configurable dashboards for owner, finance manager, accountant and branch manager, with rearrangeable, resizable, saveable widgets and drill-down from every measure |
| Headline measures | Gross sales, net sales, recognised revenue, cost of goods sold, gross profit, operating expenses, net profit, cash and bank balance, receivables, payables, loan balances, tax liabilities, net working capital |
| Income and revenue | Revenue by channel, product, category, customer, branch and salesperson; gross against net; deposits held as liability until the configured recognition condition is met |
| Expenses | Operating, administrative and procurement expenses; recurring and one-off; petty cash; staff claims; approval thresholds; duplicate prevention; capital against operating classification |
| Treasury | Cash drawers, petty cash, bank accounts and Mobile Money wallets with custodians; receipts, payments, transfers, charges, adjustments; daily cashbook and bank book |
| Reconciliation centre | Bank statement import, transaction matching, duplicate detection, suggested matches, unmatched queue, manual reconciliation |
| Receivables | Invoice and customer balances, credit limits, terms, ageing, disputes, collection worklist, reminders, promised payment dates, statements, write-offs and provisions |
| Payables | Supplier balances, ageing, payment priorities, batches, partial settlement, debit and credit notes, statements, vouchers |
| Allocation | Payment allocation across invoices, unapplied receipts, credit notes and refunds |

**Exit criteria**

- A credit sale creates a receivable; its later collection reduces the receivable and increases
  cash without recognising revenue a second time.
- A transfer between two business accounts moves balances and affects neither income nor expense.
- A customer deposit is not recognised as earned revenue before its configured condition is met.
- Every dashboard measure drills down to its supporting transactions and reconciles to the ledger.
- Cash received is never equated with revenue, nor cash paid with expense.
- POS shift, bank, Mobile Money, customer receipt, supplier payment and tax payment reconciliation
  all complete against source records.

### Phase 12 — Debt, Loans and Financing

Satisfies PRD §25, §29.7, AC-23 to AC-30.

| Workstream | Scope |
| --- | --- |
| Receivable register | Customer debts, on-account credit, instalment balances, loans and advances issued, refundable deposits; debtor, source, original amount, charges, received, balance, dates, responsible officer |
| Payable register | Supplier balances, unpaid expenses, accruals, taxes payable, commissions payable, refunds due, borrowings |
| Duplicate prevention | A balance already represented by a linked invoice, agreement or obligation is never counted twice |
| Loan agreements | Lender or borrower, agreement number, type, principal, currency, agreement, disbursement and maturity dates, purpose |
| Interest models | No interest, fixed, flat rate, reducing balance, interest-only, balloon and custom structures; rate, compounding, fees, penalties, grace period |
| Schedules | Amortisation showing opening principal, interest, fees, principal repayment, total instalment and closing balance; daily to custom frequency |
| Lifecycle | Multiple drawdowns, partial repayment, overpayment, early settlement quotation, deferment, payment holiday, refinancing, restructuring |
| Allocation | Configurable repayment allocation order per the signed agreement |
| Versioning | Original signed schedule and every approved revision retained as distinct versions; no silent recalculation |
| Security | Collateral, guarantors, covenants, agreement documents, approval evidence, maturity and renewal dates |
| Analysis | Repayment calendar, maturity profile, debt-service commitments, overdue exposure, largest counterparties, ageing buckets |

**Exit criteria**

- A loan drawdown increases cash and loan liability and increases neither revenue nor profit.
- A principal repayment reduces cash and liability and is not an operating expense; interest and
  fees are recognised separately.
- A loan or advance issued creates a receivable asset and is not expensed.
- A partial repayment allocates per the configured order and updates schedule and balance without
  altering the original agreement.
- An amended schedule is versioned; the original remains reproducible, with reason and approval
  in the audit store.
- Receivables and payables are presented separately; no net position implies legal set-off.

### Phase 13 — Budgets, Forecasting and Profitability

Satisfies PRD §20.7, §29.6, §29.8 in part.

| Workstream | Scope |
| --- | --- |
| Budgets | Annual, quarterly, monthly and custom budgets by account, category, branch, department, project or cost centre; versions, states, owner, assumptions, approval history |
| Variance | Actual against budget, prior period and forecast; absolute and percentage variance with drill-down; utilisation tracking; overspend approval |
| Cash forecast | Rolling forecast from current cash, expected collections, payable due dates, planned purchases, tax obligations, loan instalments and recurring costs, over 7, 30, 60, 90-day and custom horizons |
| Scenarios | Best, base and downside cases with editable assumptions; sensitivity to delayed collections, new borrowing, large purchases and early settlement |
| Alerts | Low-cash warnings against configurable thresholds; overdue and due-soon alerts across debts, taxes, loans and collections |
| Profitability | Contribution by product, category, order, customer, supplier source, branch, salesperson, affiliate, POS terminal and channel; before and after commission, non-recoverable tax, freight and allocated cost |
| Variance analysis | Quoted cost against actual landed cost; margin erosion attributed to freight, tax, price change, discount, return and commission |
| Analytics | Charts per PRD §20.7 and §29.8, each with readable axes, units, legends, date range, empty and loading states, an accessible table alternative, and click-through to filtered records |

**Exit criteria**

- Forecast, budget, quotation-estimate and actual posted values are visually and structurally
  distinct, and no report presents one as another.
- Selecting any chart segment opens the underlying filtered records.
- Margin erosion between quotation and realised outcome is attributable to named causes.
- Charts remain legible, exportable and fully functional with no network interface available.
- Every chart satisfies the contrast and non-colour-dependence requirements of PRD §28.2.

### Phase 14 — Management Reporting and Period Close

Satisfies PRD §8, §20.6, §29.8, §29.9, AC-22.

| Workstream | Scope |
| --- | --- |
| Management pack | Executive summary, profit and loss, balance sheet, cash flow, trial balance, sales, procurement, expenses, tax position, receivable and payable ageing, debt and loan schedule, inventory valuation, budget variance, key risks |
| Operational reports | The twelve reports of PRD §8, each filterable and drillable |
| Comparison | Day, week, month, quarter, year and custom range; current against prior period; actual against budget; year over year |
| Presentation | Every report states its reporting period, currency, accounting basis, generation time and applied filters |
| Export | PDF, Excel and CSV |
| Scheduling | Scheduled generation and delivery to authorised recipients |
| Period close | Month-end and year-end close checklist, reconciliation gates, locking, controlled reopening |

**Exit criteria**

- A selected month produces a complete management pack in which every figure reconciles to the
  general ledger.
- A closed period rejects ordinary posting; reopening is authorised, reasoned and audited.
- Sensitive reports are unavailable to roles without the export permission.
- Exported figures match on-screen figures exactly.

---

## Stage F — Deployment Editions

### Phase 15 — LAN Edition

Satisfies PRD §9.4, §9.5, §13 Phase 9, AC-12.

| Workstream | Scope |
| --- | --- |
| Host service | PostgreSQL 16 host, FastAPI service owning the sole database connection, service installation and supervision |
| Clients | Authenticated client access over the local network; clients never address the database directly |
| Transport | TLS with certificate provisioning for the local network |
| Concurrency | Advisory locking and sequence strategies for stock reservation, document numbering, payment posting, period close and shift operations |
| Migration | Verified migration of a standalone SQLite deployment to PostgreSQL |
| Multi-terminal | Several POS terminals against one authoritative dataset |
| Operations | Scheduled backup, retention, restore drill, monitoring and health reporting |

**Exit criteria**

- Behaviour under PostgreSQL is identical to behaviour under SQLite for the whole automated suite.
- Ten concurrent users sustain mixed sales, POS, procurement and posting activity with no
  duplicate document numbers and no lost stock reservations.
- A client cannot write to the database other than through the authenticated service.
- Interrupted transactions, duplicated payment callbacks, client disconnection and host restart
  all leave no partial stock or accounting posting.
- A restore drill reproduces the dataset and its attachments.

### Phase 16 — Cloud Readiness

Satisfies PRD §9.3, §13 Phase 10. Scope is readiness, not a public deployment.

| Workstream | Scope |
| --- | --- |
| API | Versioned external API contract with a published schema |
| Tenancy | Activation of the tenancy already present in the schema per [ADR-0003](decisions/adr-0003-tenancy-schema-ready.md) |
| Storage | Storage abstraction permitting object storage for attachments and rendered documents |
| Identity | Pluggable identity provider interface |
| Idempotency | Idempotent operation guarantees across the external API surface |
| Synchronisation | Explicit queue, conflict resolution, idempotency and audit trail, should offline client operation be commissioned |

**Exit criteria**

- The API is versioned, documented and covered by contract tests.
- Multi-business operation is enabled by configuration and requires no schema migration.
- Attachment storage is relocated to object storage without domain code change.
- No synchronisation path permits a disconnected client to write to the authoritative database
  outside the sanctioned queue.

---

## Critical path

```
P1 Platform ─ P2 Master data ─┬─ P3 Ledger ─ P4 Tax ─┬─ P5 Pricing ─ P6 Documents ─ P7 Procurement ─ P8 Inventory ─ P9 Dispatch
                              │                      │
                              │                      └─ P10 Point of sale
                              │
                              └────────────────────────── P11 Financial core ─ P12 Debt and loans ─ P13 Budgets ─ P14 Reporting
                                                                                                                       │
                                                                                                        P15 LAN ─ P16 Cloud
```

Phase 10 requires Phases 3, 4 and 8. Phase 11 requires Phases 3, 4, 7 and 10. Phases 15 and 16
require the whole of Stages A to E.

## Deferred and excluded

| Item | Disposition |
| --- | --- |
| Payroll | PRD refers to payroll conditionally. Excluded from Phases 1–16; the expense and subledger model accommodates it without schema change. |
| Depreciation and fixed assets | Conditional in PRD §29.2. The chart of accounts and journal model accommodate it; no module is built. |
| Mobile and web clients | Enabled by the Phase 16 API. Not built. |
| Live payment provider integration | Adapter interface defined in Phase 10; no vendor implementation. See [ADR-0009](decisions/adr-0009-pos-payment-confirmation.md). |
| Full multi-currency operation | Schema-ready from Phase 1; activation is a later configuration exercise. See [ADR-0006](decisions/adr-0006-currency.md). |

## Standing obligations

These apply to every phase and are verified at every exit review.

1. Business rules reside in domain and application services, never in the presentation layer or
   in database-specific SQL.
2. Monetary arithmetic uses fixed-precision decimal types exclusively.
3. Multi-record financial events are atomic.
4. Permissions are enforced in the application service, not by hiding interface controls.
5. Posted financial and stock records are never deleted or overwritten; correction is by reversal.
6. Supplier cost, margin and affiliate commission are never exposed to customer-facing output
   or to unauthorised roles.
7. Estimated, forecast, budgeted and actual posted values are never conflated.
8. An initiated external request is never reported as a completed one.
9. Core workflows operate with no network interface available.
10. Every phase closes with its automated suite passing against both SQLite and PostgreSQL.
