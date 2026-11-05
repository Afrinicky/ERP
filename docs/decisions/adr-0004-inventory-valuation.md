# ADR-0004 — Moving weighted average over a layer-capable stock ledger

Status: accepted · Date: 5 November 2026

## Context

PRD §6.11 requires selecting "FIFO or weighted-average valuation after accounting policy
confirmation". The decision cannot wait for Phase 8, because the Phase 5 pricing engine needs
the stock cost base for lines sourced from inventory, and the Phase 3 ledger needs the basis
on which cost of sales is posted. See [conflict register](../09-conflict-register.md) C-05.

The business is procurement-led: much stock is bought against a known customer order. It also
holds stock, and tracks batch and expiry on products where policy requires it (§6.8, §6.11).

## Decision

Moving weighted average is the configured default. The stock ledger maintains cost layers, so
FIFO is selectable per product policy without schema change.

## Rationale

Weighted average reconciles more simply. One unit cost per product per location means the
inventory control account agrees with the valuation report by construction, which matters
because that agreement is a period-close gate. It also suits procurement-led buying, where
a line is frequently received and issued against the same order at a known cost.

FIFO is nonetheless the better fit for batch and expiry-tracked stock, where physical flow is
genuinely oldest-first, and some accounting policies require it. Building the ledger with
cost layers from the outset costs one table and the discipline of recording a layer on every
receipt. Retrofitting layers onto a weighted-average-only ledger, on a live dataset, would
mean reconstructing cost history from movement records — which is at best approximate.

Choosing FIFO as the default instead would impose its reconciliation complexity on every
product, including the non-stock and service items for which it is meaningless.

## Implementation

1. `cost_layers` records every receipt as a layer: product, location, batch where applicable,
   quantity received, quantity remaining, unit cost, receipt reference.
2. Weighted average computes the unit cost across layers with quantity remaining, at the
   product and location level.
3. FIFO consumes layers oldest-first by receipt date, then by layer identifier.
4. The valuation policy is set per business and may be overridden per product.
5. Landed cost — freight, duty, clearing, port, handling and insurance — is allocated into the
   layer's unit cost at receipt, not expensed, per §20.3 and §29.4.
6. Cost of sales posts at the cost determined by the applicable policy at the moment of issue.
7. A policy change applies prospectively. Existing layers and posted cost of sales are never
   recalculated.
8. Non-stock, service and procurement-on-demand items carry no layer and no balance, per
   §6.11.
9. The inventory control account is reconciled to the valuation report as a period-close gate.

## Consequences

The stock ledger carries layer detail the default policy does not consume, and a receipt
writes one additional row. In exchange, FIFO is a configuration change rather than a
migration, and both policies are testable from the outset.

Phase 8 exit requires valuation to reconcile to the inventory control account under both
policies.

## Amendment required

PRD §6.11 is closed with this decision, subject to the pilot's accounting policy
confirmation. Should that confirmation require FIFO, the change is configuration.
