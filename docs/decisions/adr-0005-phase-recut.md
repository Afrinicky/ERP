# ADR-0005 — Delivery phases re-cut to dependency order

Status: accepted · Date: 5 November 2026 · Supersedes PRD §13

## Context

PRD §13 lists ten delivery phases. It was authored for v1.0 and was not revised when v1.1 to
v1.3 added the Tax & Statutory module (§19), the Financial Management module (§20, §29) and
Debt & Financing (§25). Those modules appear in no phase. The accounting foundation of §29.2
is not mentioned in §13 at all. See [conflict register](../09-conflict-register.md) C-03.

§13 also orders POS at Phase 7 and finance at Phase 8. A completed POS sale must post revenue,
output tax, tender movement and cost of sales (§30). Under §13's ordering there is no ledger
to post to when POS is built.

## Decision

The phase plan is re-cut to sixteen phases in dependency order, grouped into six stages, in
the [build plan](../00-build-plan.md). Two orderings change materially:

1. **The accounting ledger moves to Phase 3**, ahead of every module that posts financial
   consequence.
2. **The tax engine moves to Phase 4**, ahead of the pricing engine, because tax is an input
   to the cost build-up rather than a presentation applied afterwards.

Every §13 phase is preserved in substance and mapped in the
[traceability matrix](../10-traceability.md).

## Rationale

Building POS before the ledger would require every POS transaction to be reposted once the
ledger arrived, on data the pilot had already transacted against. The same applies to
procurement, receiving and inventory: each generates postings.

Building pricing before taxation would require the cost build-up to be revisited, since
§6.3 holds tax components separately within the build-up and distinguishes recoverable from
non-recoverable treatment — the latter being capitalised into the cost base. That is not a
later addition; it is part of the calculation.

§13's Phase 8 conflated five distinct concerns: the accounting foundation, the tax engine,
the financial registers, the analytics layer and the reporting layer. Separating them gives
each a defensible exit criterion.

## Phase count

Sixteen phases rather than ten, because four modules absent from §13 are added and §13's
Phase 6 and Phase 8 are each divided. The plan is not larger in scope than the PRD; it is
more completely enumerated.

| Stage | Phases | Outcome |
| --- | --- | --- |
| A — Platform Foundation | 1–2 | Secure, themed, installable shell with master data |
| B — Fiscal Core | 3–4 | Balanced ledger and a configurable tax engine |
| C — Commercial Core | 5–7 | Quotation, document production and procurement |
| D — Operations | 8–10 | Inventory, dispatch and the point of sale |
| E — Financial Command | 11–14 | Treasury, debt, forecasting and management reporting |
| F — Deployment Editions | 15–16 | Multi-user LAN edition and cloud readiness |

## Consequences

The pilot waits longer for its first transacting capability, because two infrastructural
phases precede the commercial phases. The compensation is that no phase closes with a known
accounting defect, and no transaction ever needs reposting.

Phases 1 to 4 produce an application a business cannot yet sell with. This is stated plainly
rather than disguised: Stage A and Stage B are foundation, and the first phase a user would
recognise as the product is Phase 5.

## Amendment required

PRD §13 is replaced by the build plan.
