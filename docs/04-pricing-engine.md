# Pricing Engine

Satisfies PRD §6.3, §6.4, §6.5, AC-01 to AC-04. Governed by
[ADR-0007](decisions/adr-0007-pricing-sequence.md) and
[ADR-0008](decisions/adr-0008-affiliate-commission.md).

## Canonical sequence

The shipped default. Every step is configurable; the sequence itself is fixed, so that any
two quotations are comparable.

```
1  Supplier cost            unit price, currency, supplier discount, supplier tax treatment
2  Cost allocation          transport and other cost categories apportioned to lines
   ────────────────────────────────────────────────────────────────────────
   = Landed cost base
3  Business margin          markup on cost, or target gross margin
4  Affiliate commission     within its configured scope
   ────────────────────────────────────────────────────────────────────────
   = List price
5  Discount                 line and document, within approval limits
   ────────────────────────────────────────────────────────────────────────
   = Net price
6  Instalment uplift        where the instalment plan is selected
   ────────────────────────────────────────────────────────────────────────
   = Payable before tax
7  Taxation                 rules evaluated against the taxable base
   ────────────────────────────────────────────────────────────────────────
   = Total payable
```

### Consequences of this order

| Position | Consequence |
| --- | --- |
| Commission before discount | A discount granted by the sales officer does not reduce the affiliate's entitlement. |
| Discount before uplift | The customer is not charged financing on an amount they were discounted. |
| Tax last | Tax is computed on the amount actually payable, and never on an amount later discounted away. |
| Allocation before margin | Margin is earned on the true landed cost, not on the supplier invoice alone. |

## Step 1 — Supplier cost

| Input | Treatment |
| --- | --- |
| Unit price | As quoted by the selected supplier |
| Currency | Recorded with the applicable rate and base-currency value |
| Supplier discount | Deducted before allocation |
| Supplier tax | Recoverable tax excluded from the cost base; non-recoverable tax included |

Where a line is sourced from existing stock rather than a supplier, the cost base is the
inventory unit cost under the configured valuation policy. See
[ADR-0004](decisions/adr-0004-inventory-valuation.md).

## Step 2 — Cost allocation

Seven methods, per PRD §6.3. The method is configurable per expense category.

| Method | Basis |
| --- | --- |
| By line | Directly attributed to a nominated line |
| By quantity | Proportional to units |
| By value | Proportional to extended line cost |
| By weight | Proportional to total line weight |
| By volume | Proportional to total line volume |
| Equal share | Divided equally across participating lines |
| Manual | Entered per line, with the residual displayed until it reaches zero |

Allocable categories: transport, handling, packaging, freight, clearing, insurance, bank
charges, import duty, port fees, and any administrator-configured category.

Rounding residue from proportional allocation is assigned to the largest line, so the sum of
allocated amounts equals the total exactly.

## Step 3 — Business margin

Two methods, held distinct per PRD §6.3.

| Method | Formula |
| --- | --- |
| Markup on cost | `price = cost_base × (1 + markup_rate)` |
| Target gross margin | `price = cost_base ÷ (1 − target_margin)` |

The method and rate may be set per business, product category or product. The more specific
setting prevails. A target margin of 1.0 or above is rejected.

The applied method is recorded on the line, so the figure is always explicable.

## Step 4 — Affiliate commission

| Attribute | Specification |
| --- | --- |
| Basis | The line's selling price excluding tax, before customer discount |
| Method | Fixed amount per unit, or a percentage of the basis |
| Scope | Selected products, selected categories, selected quotation lines, or all eligible lines |
| Rate variation | By product and by effective date |
| Accrual | At invoice |
| Payable | On receipt of the customer's payment |
| Partial fulfilment | Commission accrues proportionally to the fulfilled and paid portion |
| Visibility | Internal costing and profitability only; never on customer-facing output |

### Basis definition

"Excluding tax" means tax is never part of the basis. "Before customer discount" means a
discount granted at step 5 does not reduce the affiliate's entitlement, which is the purpose
of placing commission at step 4. The supplier's own discount is already deducted at step 1
and is therefore outside the basis.

### Gross-up for percentage commission

A percentage commission is expressed as a percentage of the selling price, not of the
margin-inclusive price beneath it. The price is therefore grossed up, so that the business
recovers the commission in full and its margin is preserved:

```
list_price = margin_inclusive_price ÷ (1 − commission_rate)
commission = list_price × commission_rate
```

A fixed commission is added directly and requires no gross-up. A commission rate of 1.0 or
above is rejected.

### States

```
accrued ──► approved ──► payable ──► paid
   │            │
   └──► cancelled (with reason and audit record)
```

A rate change or override is recorded with actor, timestamp, reason and prior value,
per PRD §6.4.

Contribution after commission is displayed at line level, so margin erosion is visible before
the quotation is issued, per PRD §14.

## Step 5 — Discount

| Level | Treatment |
| --- | --- |
| Line | Percentage or fixed amount against the line's list price |
| Document | Percentage or fixed amount, apportioned across lines by value for tax and reporting |

Approval limits are configured by role and by amount. A discount beyond the actor's limit
places the quotation in pending approval and cannot be issued until approved.

## Step 6 — Instalment uplift

| Attribute | Specification |
| --- | --- |
| Trigger | Selection of an instalment payment plan |
| Method | Fixed currency amount per unit, or a percentage of the net price |
| Variation | By repayment duration band, per PRD §6.5 |
| Applicability | By business, product or category, and optionally by customer |
| Classification | A finance charge. Held separately from tax, margin and commission throughout |

### Schedule disclosure

Every instalment quotation displays, per PRD §6.5: deposit, repayment duration, instalment
amount, instalment frequency, first due date, final due date, total finance charge and
total payable.

### Instalment states

```
scheduled ──► due ──► paid
     │         │
     │         ├──► overdue ──► paid
     │         └──► waived (authorised, reasoned)
     └──► rescheduled (original schedule retained as a version)
```

Early settlement and authorised restructuring are supported. The original schedule is never
overwritten; each revision is a new version, per PRD §25.5.

## Step 7 — Taxation

Tax rules are evaluated by the taxation module against the amount payable before tax. The
pricing engine does not compute tax itself; it supplies the taxable base and consumes the
result.

| Concern | Treatment |
| --- | --- |
| Components | Stored separately, never merged into a single figure |
| Inclusive pricing | Where a rule is inclusive, the base is derived by extraction rather than addition |
| Exemption and zero-rating | Produce a zero amount with the reason recorded, not an omitted rule |
| Non-recoverable tax | Included in the cost base at step 1 |
| Recoverable tax | Excluded from the cost base; recorded as a recoverable asset |
| Withholding | Does not alter the price; creates a withholding entry on payment |
| Evaluation result | Recorded per rule as Applied, Not applicable, Exempt, Needs review or Overridden |

## Arithmetic

| Rule | Specification |
| --- | --- |
| Type | Fixed-precision decimal throughout. Binary floating-point is prohibited. |
| Working precision | Six decimal places |
| Presentation precision | The currency's configured precision, four by default |
| Rounding | Half-up at presentation only, never during intermediate steps |
| Rounding residue | Assigned to the largest participating line so totals reconcile exactly |
| Line total | Computed from the stored unit price and quantity, never accumulated from displayed values |
| Document total | The sum of stored line totals, not of displayed line totals |

Every calculation is reproducible from its stored inputs. The engine is a pure function of
its inputs with no dependence on wall-clock time, user session or presentation state.

## Cost preservation

Per PRD §6.3 and §14.

1. On issue, the complete costing input set is written to an immutable snapshot.
2. A subsequent change to a supplier price, expense or rate does not alter the issued
   quotation.
3. After procurement, the actual cost is recorded alongside the estimate.
4. Variance is reported per line and per order, and is presented before procurement proceeds.
5. Realised profitability uses actual cost; quoted profitability uses the snapshot. The two
   are never conflated.

## Confidentiality

| Value | Customer-facing | Internal |
| --- | --- | --- |
| Supplier identity and cost | Never | Yes |
| Allocated costs | Never, except where configured as a disclosed charge | Yes |
| Business margin | Never | Yes |
| Affiliate commission | Never | Yes |
| Finance charge | Yes — disclosed as part of the instalment plan | Yes |
| Tax components | Yes | Yes |
| Discount | Yes | Yes |

Customer-facing templates cannot bind to a confidential field. An automated test asserts the
absence of supplier cost, margin and commission from rendered customer output, per PRD §14.

## Worked example

One line. Charcoal preset figures are illustrative; no tax rate is asserted as current law.

| Step | Computation | Amount |
| --- | --- | --- |
| Supplier cost | 10 units at 120.0000 | 1,200.0000 |
| Supplier discount | 5% | (60.0000) |
| Cost after discount | | 1,140.0000 |
| Transport allocated by value | share of 400.0000 across the document | 160.0000 |
| Clearing allocated by value | share of 100.0000 | 40.0000 |
| **Landed cost base** | | **1,340.0000** |
| Business margin | target gross margin 25% → ÷ 0.75 | 1,786.6667 |
| Affiliate commission | 5% of selling price, grossed up → ÷ 0.95 | 94.0351 |
| **List price** | | **1,880.7018** |
| Discount | 3% | (56.4211) |
| **Net price** | | **1,824.2807** |
| Instalment uplift | 8% over the six-month band | 145.9425 |
| **Payable before tax** | | **1,970.2232** |
| Tax | per configured rules | computed |
| **Total payable** | | computed |

Internal reporting shows, for this line: landed cost 1,340.0000, commission 94.0351,
finance charge 145.9425, contribution before commission 484.2807, and contribution after
commission 390.2456.

## Test obligations

Per PRD §9.8, the following carry mandatory unit tests at Phase 5 exit.

1. Each of the seven allocation methods, including the residue assignment.
2. Both margin methods, and their precedence hierarchy.
3. Commission within every scope, and outside every scope, asserting that the gross-up
   preserves margin exactly.
4. Commission on partial fulfilment and on partial payment.
5. Uplift by every configured duration band.
6. Discount within and beyond approval limits.
7. The full sequence end to end, asserting the canonical order.
8. Inclusive and exclusive tax derivation.
9. Rounding at four and at two decimal places, asserting exact total reconciliation.
10. Snapshot immutability under subsequent supplier price change.
11. Confidential values absent from customer-facing render output.
12. Reproducibility: identical inputs produce identical output across sessions and engines.
