# Data Model

Satisfies PRD §9.5, §10. Governed by [ADR-0003](decisions/adr-0003-tenancy-schema-ready.md),
[ADR-0004](decisions/adr-0004-inventory-valuation.md) and [ADR-0006](decisions/adr-0006-currency.md).

## Conventions

| Concern | Rule |
| --- | --- |
| Identifiers | UUIDv7 primary keys. Sequential within a time window, so index locality is preserved without exposing a countable business volume. |
| Business scoping | Every business-owned table carries `business_id`. Branch-relevant tables additionally carry `branch_id`. |
| Monetary amounts | `NUMERIC(18,4)`. Binary floating-point is prohibited in schema, domain and transport. |
| Rates and percentages | `NUMERIC(12,6)`. |
| Quantities | `NUMERIC(18,6)`, accommodating fractional units of measure. |
| Currency | Every monetary column is accompanied by `currency_code`, `exchange_rate` and a base-currency value. |
| Timestamps | `TIMESTAMP WITH TIME ZONE`, stored in UTC, presented in the business timezone. |
| Soft deletion | Not used on transactional data. Master data carries `status`; posted records are never removed. |
| Naming | Tables in the plural; columns in `snake_case`; foreign keys as `<entity>_id`. |
| Enumerations | Stored as constrained text, not integers, so database contents remain legible. |

## Integrity obligations

1. Foreign keys are declared and enforced on every relationship.
2. Unique constraints cover document numbers per business, branch and type; SKU and barcode per
   business; account code per business; and tax rule code per business and effective period.
3. Check constraints enforce non-negative quantities where applicable, valid state values,
   and the equality of debits and credits on journal entries.
4. Indexes cover SKU, product name, barcode, document number, party name, posting date,
   due date and every foreign key participating in a hot query path, per PRD §11.
5. Append-only tables — the stock ledger, journal entries, audit events and tax snapshots —
   carry no update or delete path. Correction is by reversing entry.
6. Attachments are stored in a managed store addressed by database reference. No uncontrolled
   filesystem path is persisted in a record, per PRD §9.5.

## Entity groups

### Organisation and identity

| Table | Notes |
| --- | --- |
| `businesses` | Identity, logo, contacts, tax identifiers, base currency, timezone |
| `branches` | Belongs to a business; address, contacts |
| `warehouses` | Belongs to a branch; locations and optional bins |
| `users` | Argon2id hash, status, failed-attempt counter, lockout |
| `roles`, `permissions`, `role_permissions`, `user_roles` | Eight seeded roles per PRD §3 |
| `sessions` | Token reference, device, issued and expiry timestamps |
| `settings` | Keyed configuration per business, typed and versioned |
| `number_sequences` | Per business, branch and document type; prefix, pattern, next value |
| `custom_field_definitions`, `custom_field_values` | Target entity, data type, validation, display order |
| `audit_events` | Append-only; actor, timestamp, session, action, entity, reason, before, after |
| `theme_profiles` | Token overrides per business; preset reference; validation state |

### Catalogue

| Table | Notes |
| --- | --- |
| `products` | Item class — stocked, non-stock, service, bundle; batch, serial and expiry policy; valuation policy override |
| `product_categories` | Hierarchical |
| `product_variants`, `product_attributes`, `product_attribute_values` | |
| `units_of_measure`, `uom_conversions` | Per product where conversions are product-specific |
| `barcodes` | Several per product and per unit of measure |
| `product_images` | Attachment references |
| `bundle_components` | For kit and bundle items |
| `price_lists`, `price_list_lines` | Effective-dated |
| `customer_prices` | Customer-specific pricing |
| `supplier_products` | Supplier code, price, currency, lead time, minimum order quantity |

### Parties

| Table | Notes |
| --- | --- |
| `customers` | Terms, credit limit, tax treatment, responsible officer |
| `suppliers` | Terms, tax treatment, withholding applicability |
| `affiliates` | Payment details, default commission scope |
| `contacts`, `addresses` | Polymorphic by party type |
| `party_tax_treatments` | Exemption and withholding status with effective dates and evidence |

### Ledger

| Table | Notes |
| --- | --- |
| `accounts` | Code, name, group, normal balance, statement mapping, control-account flag |
| `account_groups` | Hierarchical; statement section |
| `accounting_periods` | Status — open, closed, reopened; close and reopen audit reference |
| `journal_entries` | Append-only; posting date, period, source module, source reference, idempotency key, narration, state |
| `journal_lines` | Account, debit, credit, currency, rate, base amount, subledger reference, dimensions |
| `subledger_balances` | Materialised per party and account; reconciled to control accounts |
| `journal_approvals` | Maker, checker, timestamp, decision, reason |

Every `journal_entries` row satisfies `SUM(debit) = SUM(credit)` in base currency, asserted
before commit.

### Taxation

| Table | Notes |
| --- | --- |
| `tax_rules` | Name, category, authority, jurisdiction, identifier, method, rate or amount, currency, effective dates, filing frequency, due-date rule, account mappings, state |
| `tax_rule_versions` | Effective-dated versions; a rule used in a posted transaction is never deleted |
| `tax_rule_conditions` | Applicability by product, category, supplier, customer, transaction type, branch, import flag, jurisdiction |
| `tax_rule_tiers` | Tiered and threshold-based bands |
| `transaction_tax_lines` | Per source line; rule version, basis, computed amount, treatment, evaluation result |
| `tax_evaluation_log` | Applied, Not applicable, Exempt, Needs review, Overridden — per PRD §24 |
| `tax_overrides` | Actor, reason, timestamp, evidence, approval reference |
| `tax_obligations` | Authority, period, assessed, accrued, paid, outstanding, due date, state |
| `tax_payments`, `tax_payment_allocations` | Partial payment and allocation across obligations |
| `tax_filings` | Period, status, submission date, acknowledgement, attachments |
| `withholding_certificates` | Counterparty, amount, certificate reference, remittance state |

### Pricing and quotation

| Table | Notes |
| --- | --- |
| `rfqs`, `rfq_lines` | Customer request; requested product, quantity, specification, due date |
| `supplier_rfqs`, `supplier_quotations`, `supplier_quotation_lines` | Price, availability, lead time, freight, tax, terms, validity |
| `quotations` | Revision, validity, state, payment plan, totals, issued snapshot reference |
| `quotation_lines` | Source — stock or named supplier; quantity, unit price, discount, line total |
| `quotation_line_costs` | Supplier cost, allocated transport, allocated other costs, margin, commission, financing uplift — each held separately |
| `cost_allocation_rules` | Allocation method per expense category |
| `margin_policies` | Markup on cost and target gross margin as distinct methods, by product, category or business |
| `commission_rules` | Scope, basis, fixed or percentage, effective dates |
| `commission_accruals` | State — accrued, approved, payable, paid; source line; amount; payout trigger evidence |
| `payment_plans` | Outright and instalment; uplift method, duration bands, deposit rule |
| `instalment_schedules`, `instalment_lines` | Due date, principal, finance charge, tax, total, paid, balance, state |
| `quotation_snapshots` | Costing inputs preserved at issue; immutable |

### Sales and procurement

| Table | Notes |
| --- | --- |
| `sales_orders`, `sales_order_lines` | Converted from quotation without re-entry; fulfilment state per line |
| `customer_acceptances` | Customer purchase order reference, evidence attachment, timestamp |
| `sales_invoices`, `sales_invoice_lines` | Revenue recognition state |
| `sales_returns`, `credit_notes` | |
| `purchase_orders`, `purchase_order_lines` | Linked to sales order lines where procurement is demand-driven |
| `supplier_invoices`, `supplier_invoice_lines` | Three-way match state and variance |
| `match_variances` | Quantity and price variance with approval reference |
| `debit_notes` | |

### Inventory

| Table | Notes |
| --- | --- |
| `stock_ledger` | Append-only; movement type, product, warehouse, location, bin, batch, serial, quantity signed, unit cost, total cost, source module, source reference |
| `stock_balances` | Materialised on hand, available, reserved, quarantined, incoming, in transit — reconciled to the ledger |
| `cost_layers` | Receipt layers with quantity remaining and unit cost, supporting weighted average and FIFO |
| `batches` | Batch or lot code, manufacturer, manufacture and expiry dates, state |
| `serials` | Serial number, state, current location |
| `reservations` | Source order line, quantity, state, expiry |
| `goods_receipts`, `goods_receipt_lines` | Ordered, received, accepted, rejected, outstanding quantities |
| `inspections` | Outcome, inspector, criteria, evidence |
| `landed_cost_allocations` | Freight, duty, clearing, port, handling and insurance allocated to receipt lines |
| `stock_counts`, `stock_count_lines` | Counted, system, variance, reason, approval |
| `stock_transfers`, `stock_adjustments` | Reason, approval, resulting ledger entries |
| `assemblies`, `assembly_lines` | Required, available, reserved, incoming, assembled, dispatched |

### Dispatch

| Table | Notes |
| --- | --- |
| `pick_lists`, `pick_list_lines` | |
| `packing_lists`, `packages` | Package count, contents, weight, dimensions |
| `waybills`, `waybill_lines` | Several per order permitted |
| `delivery_notes` | |
| `dispatches` | Mode, carrier, driver, vehicle, destination, tracking reference, dispatch timestamp |
| `proofs_of_delivery` | Recipient, signature or scan attachment, timestamp, location, comments; immutable |
| `delivery_exceptions` | Failed, short, damaged, returned; resulting stock and financial consequence |

### Point of sale

| Table | Notes |
| --- | --- |
| `pos_terminals` | Branch, printer configuration, display configuration |
| `cashier_shifts` | Opening float, cash movements, expected, counted, variance, reason, supervisor sign-off, state |
| `pos_sales`, `pos_sale_lines` | Customer or walk-in, discounts, tax lines, totals |
| `pos_tenders` | Method, amount, confirmation state — pending, confirmed, failed — provider reference, idempotency key |
| `pos_suspended_sales` | Parked cart state |
| `pos_reversals` | Return, exchange, refund, void; authorisation reference; original sale reference |
| `cash_movements` | Cash in, cash out, drawer, reason, approval |
| `denomination_counts` | Per shift close |

### Finance

| Table | Notes |
| --- | --- |
| `financial_accounts` | Cash drawer, petty cash, bank account, Mobile Money wallet; custodian, branch, currency, opening balance |
| `receipts`, `receipt_allocations` | Customer receipts and allocation across invoices; unapplied balance |
| `payments`, `payment_allocations` | Supplier and other payments with allocation |
| `transfers` | Between the business's own accounts; never income or expense |
| `expenses`, `expense_lines` | Category, payee, branch, department, project, tax treatment, recurrence, approval, attachments |
| `expense_claims` | Staff claims with approval chain |
| `recurring_expense_schedules` | |
| `bank_statements`, `bank_statement_lines` | Imported; matched, unmatched, duplicate-suspect states |
| `reconciliations`, `reconciliation_matches` | Manual and suggested matches |
| `receivables`, `payables` | Materialised registers with ageing buckets and source references |
| `collection_activities` | Contact history, promised payment date, responsible officer |
| `write_offs`, `bad_debt_provisions` | Approval, reason, evidence |

### Financing

| Table | Notes |
| --- | --- |
| `loan_agreements` | Direction — borrowing or lending; counterparty, agreement number, type, principal, currency, dates, purpose, interest model, rate, compounding, fees, penalties, grace period |
| `loan_drawdowns` | Multiple disbursements permitted |
| `repayment_schedules` | Version, approval, state; the original signed schedule is never overwritten |
| `repayment_schedule_lines` | Opening principal, interest, fees, principal repayment, total instalment, closing balance, paid, state |
| `loan_repayments`, `repayment_allocations` | Configurable allocation order across fees, interest and principal |
| `loan_restructures` | Reason, approval, superseded schedule version, new schedule version |
| `collateral`, `guarantors`, `covenants` | Attachments and review dates |

### Documents

| Table | Notes |
| --- | --- |
| `document_types` | Title, purpose, data source, numbering sequence, internal or customer-facing classification |
| `document_templates` | Version, state, page configuration, style configuration |
| `template_sections`, `template_elements` | Position, size, binding, format, visibility condition |
| `template_tables`, `template_columns` | Label, width, alignment, format, visibility, order |
| `template_expressions` | Constrained expression source and parsed form |
| `generated_documents` | Document number, source transaction, template version, rendered snapshot reference, issue timestamp |
| `document_attachments` | Uploaded and received documents linked to source records |
| `attachments` | Managed store reference, content hash, media type, size, access scope |

### Analytics

| Table | Notes |
| --- | --- |
| `budgets`, `budget_versions`, `budget_lines` | Period, dimension, amount, assumptions, approval history |
| `forecast_scenarios`, `forecast_assumptions`, `forecast_lines` | Best, base and downside; clearly labelled as forecast |
| `report_definitions`, `report_schedules`, `report_runs` | Parameters, recipients, generation timestamp, output reference |
| `dashboard_layouts` | Per user and role; widget placement, size and visibility |

## Tenancy

Every business-owned table carries `business_id` from the first migration. Branch-relevant
tables additionally carry `branch_id`. Application services derive both from the actor's
scoping context and apply them to every query without exception. The interface exposes a
single business in the initial release; multi-business operation is activated by configuration
in Phase 16 and requires no migration. See [ADR-0003](decisions/adr-0003-tenancy-schema-ready.md).

## Currency

Each monetary column is accompanied by `currency_code`, `exchange_rate` and a base-currency
value. Only the base currency is configured at release. Foreign-currency supplier invoicing,
revaluation and exchange differences are activated by configuration. See
[ADR-0006](decisions/adr-0006-currency.md).

## Migration discipline

1. Every schema change is an Alembic revision with a tested downgrade path.
2. The database is backed up before any upgrade, and migration success is verified before the
   application is permitted to start, per PRD §9.5.
3. Data migrations are separated from schema migrations and are idempotent.
4. Migrations are tested against both SQLite and PostgreSQL before release.
5. A migration that cannot be reversed is accompanied by a documented recovery procedure.

## Engine differences

| Concern | SQLite | PostgreSQL |
| --- | --- | --- |
| Concurrency | Write-ahead logging, single writer | Row-level locking, advisory locks |
| Numeric type | Decimal adapter over text storage | Native `NUMERIC` |
| Sequences | Table-based allocation under transaction | Native sequences with advisory locking |
| Isolation | Serialisable in practice | `REPEATABLE READ` for financial posting |

Behavioural differences are identified by running the integration suite against both engines.
PostgreSQL tests are mandatory before the LAN release, per PRD §9.8.
