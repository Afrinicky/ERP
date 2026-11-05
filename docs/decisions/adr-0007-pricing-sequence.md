# ADR-0007 — Canonical pricing calculation sequence

Status: accepted · Date: 5 November 2026

## Context

PRD §15 asks for confirmation of "installment uplift basis and whether it applies
before/after discount". PRD §6.3 requires the system to "clearly define the selected cost
base, tax treatment and discount sequence" but does not define it. PRD §6.3 lists nine
components — supplier cost, transportation, other costs, taxes, business margin, affiliate
commission, instalment uplift, discount and rounding — without stating their order.

Without a fixed order, two quotations carrying identical inputs can produce different totals,
and §6.3's requirement that the build-up be transparent cannot be met. See
[conflict register](../09-conflict-register.md) C-06.

## Decision

A canonical sequence. Every step's parameters are configurable; the order is fixed.

```
1  Supplier cost
2  Cost allocation              → landed cost base
3  Business margin
4  Affiliate commission         → list price
5  Discount                     → net price
6  Instalment uplift            → payable before tax
7  Taxation                     → total payable
```

## Rationale for each placement

**Allocation before margin.** §6.3 requires transport and other costs to be allocated. Margin
must be earned on the true landed cost, not on the supplier invoice alone, or the business
systematically under-recovers its own freight and clearing.

**Commission before discount.** §6.4 requires commission to be attributable to a referrer and
auditable. A discount is a decision of the sales officer; the affiliate is not party to it.
Placing commission earlier means a discount does not silently reduce someone else's
entitlement. §14 separately requires line-level contribution after commission to be visible,
which this ordering supports: the erosion is attributable to the discount, not hidden inside
the commission.

**Discount before uplift.** §6.5 classifies the uplift as a financing charge. Charging
financing on an amount the customer was discounted would mean the customer pays interest on
money they never owed. This is the specific question §15 asks, and this is the answer.

**Tax last.** Tax is computed on the amount actually payable. Computing it earlier would tax
an amount subsequently discounted away, and would conflict with §6.3's requirement that tax
components be stored separately from the cost build-up.

## Gross-up for percentage commission

A percentage commission is expressed as a percentage of the selling price, not of the
margin-inclusive price beneath it. The price is grossed up so the business recovers the
commission in full and its margin is preserved:

```
list_price = margin_inclusive_price ÷ (1 − commission_rate)
```

Without the gross-up, a 5% commission would reduce the business's realised margin by
approximately 5% of the selling price, which is not what "the business pays 5% commission"
is understood to mean.

## Configurability

| Configurable | Fixed |
| --- | --- |
| Allocation method per expense category | The order of the seven steps |
| Margin method and rate, by business, category or product | That margin precedes commission |
| Commission scope, basis, method and rate | That commission precedes discount |
| Discount approval limits by role | That discount precedes uplift |
| Uplift method and duration bands | That uplift precedes tax |
| Tax rules, treatments and applicability | That tax is computed last |

## Arithmetic

Fixed-precision decimal throughout, six working decimal places, half-up rounding at
presentation only. Rounding residue is assigned to the largest participating line so document
totals reconcile exactly. Line totals are computed from stored unit prices, never accumulated
from displayed values.

## Consequences

A business wanting a different order cannot have one. This is deliberate: §6.3 requires
transparency, and a configurable order means no two quotations are comparable and the test
matrix multiplies by the number of permutations.

Every step is nonetheless parameterised, so the practical flexibility §6.3 asks for is
retained.

## Amendment required

PRD §6.3 is extended with the canonical sequence. §15 bullet 1 is closed.
