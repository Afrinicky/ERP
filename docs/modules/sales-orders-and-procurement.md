# Sales Orders and Procurement

Phase 7. Satisfies PRD §6.8, §13 Phase 5, AC-07. Written at the commencement of the phase,
as the [module index](README.md) requires.

## Purpose

A quotation the customer has accepted becomes an order without anybody typing it again,
and the order drives what is bought. What the supplier then delivers, invoices and is paid
is matched back to what was ordered, so a quantity or a price that moved between the three
documents is seen and approved rather than paid. The supplier's invoice raises a payable
through the ledger; paying it reduces the payable and the cash and recognises no second
expense, per PRD §30.

## Entities

| Table | Holds |
| --- | --- |
| `customer_acceptances` | The evidence that a quotation was accepted: the customer's purchase order reference or other confirmation, how it arrived, when, and the attached evidence |
| `sales_orders`, `sales_order_lines` | The order converted from the quotation, with the quoted figures carried across and a fulfilment position per line: ordered, procured, received, cancelled |
| `purchase_orders`, `purchase_order_lines` | What is bought from one supplier, raised against sales order lines (several purchase orders may serve one customer order) or for replenishment, with the supplier quotation it was sourced from |
| `goods_receipts`, `goods_receipt_lines` | What arrived against a purchase order: received, accepted, rejected, with batch, expiry, serial and manufacturer where configured |
| `supplier_invoices`, `supplier_invoice_lines` | The supplier's bill against a purchase order, matched three ways: order, receipt, invoice |
| `match_variances` | A quantity or price difference between the three documents, with its approval |
| `supplier_credit_notes` | A supplier's credit against an invoice, with its reason |
| `supplier_payments`, `supplier_payment_allocations` | Money paid to a supplier, allocated across invoices, with withholding tax where it applies and the evidence attached |
| `attachments` | Already present. Gains a service: evidence is stored under its own identifier, hashed, and marked immutable |

Every monetary column is `Money` and every quantity `Quantity`, as elsewhere. A purchase
order is in the supplier's currency with an exchange rate; the ledger is posted in base
currency at the rate on the document.

## State machines

**Sales order**: `open → partially_fulfilled → fulfilled`, and `open | partially_fulfilled →
cancelled`. The state is derived from the lines, never set by hand: a line is fulfilled when
what was received against it (through its purchase orders) covers what was ordered less what
was cancelled. Phase 8 adds stock; until then a line sourced from stock is fulfilled from the
moment it is ordered, which is what "available stock" means to a reader of the order today.

**Sales order line**: `open → backordered` when a purchase order is short, `→ fulfilled`,
`→ cancelled`. A substitution replaces the product on the line with an approved alternative
and records who approved it and why; the quantity and price are unchanged unless amended.

**Purchase order**: `draft → approved → issued → partially_received → received → closed`,
with `cancelled` reachable from `draft`, `approved` and `issued`. Approval is a separate
permission from raising, per the registry. Issue allocates the number and freezes the lines.

**Goods receipt**: `draft → posted`. Posting writes the inventory accrual and advances the
purchase order; a receipt is never amended after posting, only reversed by a further receipt
with negative quantities, which Phase 8 provides with the stock ledger.

**Supplier invoice**: `draft → matched | variance` on recording, `variance → matched` when
every variance is approved, `matched → posted` on posting, `posted → partially_paid → paid`
as allocations arrive, and `cancelled` from `draft` and `variance`. A rejected variance
returns the invoice to `draft` for correction.

**Variance**: `pending → approved | rejected`. Approval requires `match_variance.approve`,
which the registry marks sensitive.

## Three-way match

For each invoice line, against its purchase order line and the receipts posted against it:

| Check | Variance raised when |
| --- | --- |
| Quantity | Invoiced quantity exceeds the quantity accepted on receipt, or exceeds the quantity ordered |
| Price | Invoiced unit cost differs from the ordered unit cost by more than the configured tolerance (absolute or percentage, business-wide, default zero) |

A variance is a record, not a refusal. The invoice is held in `variance` until each one is
approved; an invoice with no variance is matched at once. The amount at stake is shown with
the variance, so the approver sees GH₵340 of difference on a GH₵18,000 invoice rather than a
flag.

## Posting rules

Per the [accounting model](../05-accounting-model.md), with one addition: the accrual
between receipt and invoice. Goods received and not yet invoiced sit in **20150 Goods received
not invoiced**, a current liability, so inventory is recognised when it arrives and the
supplier's own figure replaces the accrual when the invoice is recorded.

| Event | Debit | Credit |
| --- | --- | --- |
| Goods receipt posted | Inventory (12000), or goods in quarantine (12100) where inspection is pending | Goods received not invoiced (20150), at order cost × accepted quantity |
| Supplier invoice posted | Goods received not invoiced (20150) at order cost; Inventory (12000) for an approved price variance; Recoverable input tax (13000); Inventory for non-recoverable tax, which capitalises into landed cost | Trade payables (20000), supplier subledger, for the invoice total |
| Supplier credit note posted | Trade payables (20000) | Inventory (12000) |
| Supplier payment posted | Trade payables (20000) for the amount allocated | Withholding tax payable (21100) for the amount withheld; cash, bank or Mobile Money for the amount paid |

The supplier payment recognises no expense: the purchase was recognised at receipt. That is
the third exit criterion, and the posting rule is the proof.

## Payables

The register is derived from supplier invoices — total less allocated payments and credits —
and ages by the invoice due date, which comes from the supplier's payment terms. It reconciles
to the trade payables control account by construction, because every movement on the one is
a posting to the other. Buckets: current, 1–30, 31–60, 61–90, over 90.

## Cost variance

Each sales order line carries the landed unit cost the quotation expected. Once the supplier
invoice is posted the actual unit cost is known, and the report shows expected, actual and the
difference per line, summed per order. The fourth exit criterion.

## Permissions

| Action | Permission |
| --- | --- |
| Read orders, purchase orders, receipts and invoices | `sales_order.view`, `purchase_order.view`, `supplier_invoice.view` (added) |
| Record acceptance, convert, amend, cancel, substitute | `sales_order.manage` |
| Raise and amend a purchase order | `purchase_order.manage` |
| Approve and issue a purchase order | `purchase_order.approve` |
| Record a goods receipt | `goods_receipt.manage` |
| Record a supplier invoice or credit note | `supplier_invoice.manage` |
| Approve a variance | `match_variance.approve` |
| Record a supplier payment | `payment.manage` |
| Read the payables register | `payable.view` |
| Attach evidence | `attachment.upload` |

Posting goes through the journal service, which requires `journal.post` of the actor doing
the business action. The shipped administrator holds it; the sales role does not record
receipts or invoices.

## Interface

**Sales › Orders** — the orders converted from quotations, their fulfilment position, the
purchase orders serving each, and the cost variance once known. Acceptance is recorded from
the quotation row with the customer's reference and the evidence file.

**Purchases** — four views: Orders (raise from a sales order's chosen sources or for
replenishment, approve, issue), Receipts (record what arrived, line by line, with the stepper
for quantities), Invoices (record, see variances, approve them, post), Payables (the register,
and payment with allocation across invoices). Quantities at the receiving bench use the
transactional stepper tier; the payment screen uses suggested amounts and one primary action,
as the design system requires.

## Test obligations

- Acceptance evidence is required to convert; conversion carries every quoted figure across.
- Several purchase orders serve one order; the order's fulfilment position reflects each.
- A short receipt backorders the line; a further receipt fulfils it.
- A price moved between order and invoice raises a variance with the amount at stake; the
  invoice posts only once it is approved.
- A supplier invoice raises a payable in the ledger and the register; a payment reduces both
  and the cash account and posts no expense.
- Cost variance is reported per line and per order.
- Evidence attached to an acceptance or a payment is stored, hashed and immutable.
