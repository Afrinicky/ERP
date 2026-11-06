# Traceability Matrix

Every PRD section and every acceptance criterion mapped to the phase that satisfies it.
Phases are defined in the [build plan](00-build-plan.md).

## Sections

| PRD | Subject | Phase |
| --- | --- | --- |
| §1 | Product vision | — |
| §2 | Product goals | All |
| §3 | Users and roles | 1 |
| §4.1 | Quote-led procurement and fulfilment | 5, 7, 8, 9 |
| §4.2 | Stock-holding sales | 8 |
| §4.3 | Point of sale | 10 |
| §5.1 | Quote-led workflow | 5, 6, 7, 8, 9, 11 |
| §5.2 | POS workflow | 10 |
| §6.1 | General business configuration | 1, 2 |
| §6.2 | RFQ and quotation management | 5 |
| §6.3 | Costing and pricing engine | 5 |
| §6.4 | Affiliate commission | 5 |
| §6.5 | Outright and instalment plans | 5 |
| §6.6 | Document designer | 3.5, 6 |
| §6.7 | Document generation and acknowledgement | 3.5, 9 |
| §6.8 | Procurement, receiving and assembly | 7, 8 |
| §6.9 | Dispatch and delivery | 9 |
| §6.10 | Point of sale | 10 |
| §6.11 | Inventory | 8 |
| §6.12 | Finance and accounting | 3, 11 |
| §7.1 | Visual direction | 1 |
| §7.2 | Usability | 1, and every phase |
| §7.3 | Main navigation | 1 |
| §8 | Reporting | 14 |
| §9.1 | Confirmed technology direction | 1 |
| §9.2 | Recommended Python stack | 1 |
| §9.3 | Application architecture | 1, 15 |
| §9.4 | Offline-first and LAN deployment | 1, 15 |
| §9.5 | Database, integrity and migrations | 1, 3, 15 |
| §9.6 | Security and integration design | 1, 10, 15 |
| §9.7 | Interface and reporting implementation | 1, 13 |
| §9.8 | Testing and release engineering | Every phase |
| §10 | Audit, security and integrity | 1, 3, and every phase |
| §11 | Non-functional requirements | Every phase |
| §12 | Acceptance criteria AC-01 to AC-12 | See below |
| §13 | Delivery phases | Superseded; see below |
| §14 | Risks and controls | Every phase |
| §15 | Open decisions | Closed or assigned; see [conflict register](09-conflict-register.md) C-10 |
| §16 | Success measures | 5, 6, 9, 10, 11, 15 |
| §17 | Source documents reviewed | 6 |
| §18 | Product statement | — |
| §19.1 | Tax and fee configuration | 4 |
| §19.2 | Tax application and continuity | 4 |
| §19.3 | Tax lifecycle and registers | 4 |
| §19.4 | Tax dashboards and alerts | 4, 13 |
| §19.5 | Tax payment and reconciliation | 4, 11 |
| §19.6 | Tax controls and accounting treatment | 3, 4 |
| §20.1 | Financial overview dashboard | 11 |
| §20.2 | Income and revenue management | 11 |
| §20.3 | Expense and outflow management | 11 |
| §20.4 | Cash, bank and liquidity | 11 |
| §20.5 | Profitability and performance | 13 |
| §20.6 | Periodic management reports | 14 |
| §20.7 | Visual analytics and charts | 13 |
| §20.8 | Financial controls and integrity | 3, 11 |
| §21 | Cross-module integration | 4, 7, 8, 10, 11, 12 |
| §22 | Acceptance criteria AC-13 to AC-22 | See below |
| §23 | Additional navigation | 1, 4, 11 |
| §24 | Tax implementation safeguards | 4 |
| §25.1 | Financial Management navigation | 11 |
| §25.2 | Debts owed to the business | 12 |
| §25.3 | Debts owed by the business | 12 |
| §25.4 | Loans and financing agreements | 12 |
| §25.5 | Repayment plan management | 12 |
| §25.6 | Debt and loan dashboards | 12, 13 |
| §25.7 | Accounting and cash-flow treatment | 3, 12 |
| §25.8 | Permissions, audit and safeguards | 12 |
| §26 | Acceptance criteria AC-23 to AC-30 | See below |
| §27 | Updated Financial Management definition | 11, 12, 13, 14 |
| §28.1 | Core colour system | 1 |
| §28.2 | Visual and interaction requirements | 1, 13 |
| §29.1 | Financial command centre | 11 |
| §29.2 | Accounting foundation and general ledger | 3 |
| §29.3 | Revenue, sales and receivables | 11 |
| §29.4 | Expenses, procurement and payables | 7, 11 |
| §29.5 | Cash, bank and Mobile Money treasury | 11 |
| §29.6 | Budgeting, forecasting, scenarios | 13 |
| §29.7 | Debt, loans and repayment | 12 |
| §29.8 | Financial analytics and reporting | 13, 14 |
| §29.9 | Financial controls, security and audit | 1, 3, 11 |
| §29.10 | Financial workspace navigation | 11 |
| §30 | Financial Management acceptance criteria | See below |
| §31 | Revised visual design acceptance criteria | 1, 10, 13 |

## Acceptance criteria

### §12 — AC-01 to AC-12

| ID | Scenario | Phase | Verified by |
| --- | --- | --- | --- |
| AC-01 | Multi-supplier quotation | 5 | Integration test: one pro forma, per-line source and cost retained internally |
| AC-02 | Cost build-up | 5 | Unit tests across the canonical sequence; build-up panel exposes every component |
| AC-03 | Affiliate selection | 5 | Unit tests for every scope, in and out |
| AC-04 | Instalment pricing | 5 | Unit tests per duration band; schedule disclosure asserted |
| AC-05 | Redesign pro forma | 6 | End-to-end test: columns and sections added, removed, reordered and previewed |
| AC-06 | New document type | 6 | End-to-end test: custom type, fields and numbering sequence created |
| AC-07 | Order procurement | 7 | Integration test: accepted order linked to supplier POs and available stock |
| AC-08 | Delivery acknowledgement | 9 | Integration test: waybill and signed proof linked to the order |
| AC-09 | POS cash sale | 10 | End-to-end test: receipt issued; stock, cash and change recorded |
| AC-10 | Split and electronic tender | 10 | Integration test: tender amounts and confirmation states accurate |
| AC-11 | Offline mode | 1, 10 | Staging test with no network interface available |
| AC-12 | LAN mode | 15 | Concurrency test: multiple users against one authoritative dataset |

### §22 — AC-13 to AC-22

| ID | Scenario | Phase | Verified by |
| --- | --- | --- | --- |
| AC-13 | Configure a new tax rule | 4 | Integration test: rule applied to matching new transactions only |
| AC-14 | Change or withdraw a rule | 4 | Integration test: posted documents numerically unchanged |
| AC-15 | Mandatory tax missing | 4 | Integration test: posting blocked, or an authorised exception recorded |
| AC-16 | Track tax obligations | 4 | Reconciliation test: every register agrees with source entries and the ledger |
| AC-17 | Partial tax payment | 4 | Integration test: balance reduced, evidence and residue retained |
| AC-18 | Monthly financial report | 14 | End-to-end test: every figure reconciles to the ledger |
| AC-19 | Drill into expense chart | 13 | Presentation test: segment selection opens the underlying records |
| AC-20 | Distinguish cash from profit | 3, 11 | Posting tests: receipt, payment, transfer and expense each post correctly |
| AC-21 | Import costs | 8 | Integration test: duty, port and clearing allocated once to landed cost |
| AC-22 | Period close | 3 | Integration test: posting rejected; reopening authorised and audited |

### §26 — AC-23 to AC-30

| ID | Scenario | Phase | Verified by |
| --- | --- | --- | --- |
| AC-23 | Record customer debt | 12 | Integration test: receivable linked to invoice, with collections and balance |
| AC-24 | Record business borrowing | 12 | Integration test: principal, interest, schedule, payments and liability |
| AC-25 | Record loan issued | 12 | Posting test: receivable asset created, not expensed |
| AC-26 | Partial repayment | 12 | Unit test: allocation order, schedule and balance |
| AC-27 | Reschedule loan | 12 | Integration test: revised version created; original and reason retained |
| AC-28 | Review debt dashboard | 12, 13 | Presentation test: every metric drills to source records |
| AC-29 | Cash-flow report | 14 | Report test: principal, interest, trade flows and transfers classified distinctly |
| AC-30 | Prevent duplicate debt | 12 | Integration test: a linked invoice or agreement counted once |

### §30 — Financial Management criteria

| Criterion | Phase | Verified by |
| --- | --- | --- |
| POS sale posts revenue, tax, tender and cost of sales | 10 | Posting test |
| Credit sale creates a receivable; collection does not duplicate revenue | 11 | Posting test |
| Credit purchase creates a payable; payment does not duplicate expense | 7, 11 | Posting test |
| Loan drawdown increases cash and liability, not revenue | 12 | Posting test |
| Principal repayment reduces liability, not expense; interest separate | 12 | Posting test |
| Transfer affects balances only | 11 | Posting test |
| Deposit not recognised as revenue before its condition | 11 | Posting test |
| Partial payment allocated without altering the agreement | 12 | Unit test |
| Amended schedule versioned; original reproducible | 12 | Integration test |
| Closed period rejects posting; reopening audited | 3 | Integration test |
| Every dashboard metric traceable and reconciled | 11, 13 | Reconciliation test |
| Balanced trial balance and consistent statements | 3, 14 | Report test |

### §31 — Visual design criteria

| Criterion | Phase | Verified by |
| --- | --- | --- |
| Default theme uses charcoal, orange, beige and off-white in their §28 roles | 1 | Visual regression against the Charcoal preset |
| Primary actions and active navigation use the accent; text remains legible | 1 | Contrast audit across every token pairing |
| Administrator previews, saves and resets custom brand colours | 1 | Presentation test of the theme editor |
| Financial dashboards show meaningful measures and charts with filters, comparisons, legends and drill-down | 11, 13 | Presentation test per dashboard |
| POS remains fast and uncluttered with clear cart, payment, change and shift controls | 10 | Performance and presentation tests |

## §13 phase mapping

The original ten phases, each mapped to the phase of the re-cut plan that delivers it. See
[ADR-0005](decisions/adr-0005-phase-recut.md).

| §13 phase | Scope | Re-cut phase |
| --- | --- | --- |
| 1 Foundation | Shell, business setup, auth, roles, audit, SQLite, backup, design system | 1 |
| 2 Master data | Customers, suppliers, products, units, categories, attributes | 2 |
| 3 Quote and pricing | RFQ, supplier quotes, comparison, cost build-up, affiliates, payment plans | 5 |
| 4 Document designer | Default pro forma, visual editor, custom types, PDF output | 6 |
| 5 Order and procurement | Acceptance, sales order, supplier PO, invoice, payables | 7 |
| 6 Receiving and fulfilment | GRN, inspection, assembly, inventory, waybill, proof of delivery | 8, 9 |
| 7 POS | Register, cart, tender, receipt, shifts, refunds, reconciliation | 10 |
| 8 Finance and reports | Receivables, payables, expenses, journals, tax, profitability | 3, 4, 11, 13, 14 |
| 9 LAN edition | PostgreSQL host, clients, TLS, concurrency, backup | 15 |
| 10 Cloud edition | Managed database, API, object storage, identity, sync | 16 |

Original Phase 8 divides across five re-cut phases, because it conflated the accounting
foundation, the tax engine, the financial registers, the analytics layer and the reporting
layer. The accounting foundation and the tax engine move ahead of POS, since POS posts
through both.

## Modules absent from §13

| Module | PRD | Re-cut phase |
| --- | --- | --- |
| Accounting foundation and general ledger | §29.2 | 3 |
| Tax and statutory obligations | §19 | 4 |
| Financial Management core | §20, §29.1, §29.3 to §29.5 | 11 |
| Debt, loans and financing | §25, §29.7 | 12 |
| Budgets, forecasting and profitability | §29.6, §29.8 | 13 |
| Management reporting and period close | §20.6, §29.8 | 14 |
