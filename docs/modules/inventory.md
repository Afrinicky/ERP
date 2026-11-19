# Inventory, Receiving and Assembly

Phase 8. Satisfies PRD §6.11, §6.8 in part, §13 Phase 6 in part. Valuation per
[ADR-0004](../decisions/adr-0004-inventory-valuation.md). Written at the commencement of the
phase, as the [module index](README.md) requires.

## Purpose

Every unit the business owns is somewhere, in some state, at some cost, and the three are
recorded together in one append-only ledger. The inventory value on the balance sheet is the
sum of the cost layers, and the cost layers move only when a movement posts the same amount
to the general ledger — so valuation agrees with the inventory control accounts by
construction rather than by a reconciliation somebody remembers to run.

## The four records

| Record | Mutable | Holds |
| --- | --- | --- |
| `stock_movements` | Never | One row per change: what, where, which batch or serial, which state, how many, at what value, why, and by whom. Rows of one action share an operation identifier and one journal entry |
| `stock_balances` | By the engine only | Quantity by product, warehouse, location, bin, batch and state: the ledger summed, kept so a stock list is not a ledger scan |
| `stock_positions` | By the engine only | Quantity by product and warehouse in each state, and a sequence that every movement in the pool increments. The row the engine locks before it changes the pool |
| `cost_layers` | By the engine only | A receipt's quantity and value, and what remains of each |

The balances, positions and layers are derived. The ledger is the record. A reconciliation
report recomputes the first three from the fourth and lists any difference; the exit
criteria require there to be none at any instant.

## Quantity states

| State | Meaning | Available | Valued to |
| --- | --- | --- | --- |
| Usable | On hand, passed, sellable | Yes, less what is reserved | 12000 Inventory |
| Quarantine | Received pending inspection | No | 12100 Goods in quarantine |
| Rejected | Failed inspection, held for return | No | 12100 Goods in quarantine |
| In transit | Dispatched between warehouses, not yet received | No | 12200 Goods in transit |
| Reserved | An overlay on usable stock, held for an order line | — | Not valued: the unit is still usable stock |

**Available** is usable stock in batches that have not passed their expiry date, less what is
reserved. **Incoming** is not a ledger state: it is what issued purchase orders have yet to
deliver, read from procurement.

## Valuation

Moving weighted average is the default; FIFO is selectable per product (ADR-0004). Both run
over the same layers, and both keep this invariant:

> The value of a pool — product, warehouse, state — is the sum of its layers' remaining value,
> and every change to that sum is posted to the account the state is valued to.

- A layer stores its remaining value, not a unit cost. Taking part of a layer takes
  `remaining value × quantity taken ÷ remaining quantity`; taking all of it takes all of its
  value, so no residue is ever left behind at six places.
- **FIFO** consumes the oldest layer first, by the date the stock first arrived and then by
  layer identifier. A layer moved between warehouses or states keeps its original date.
- **Moving weighted average** re-averages the pool whenever value enters it: the pool's total
  value is redistributed across its layers by quantity, the last layer taking the residue so
  the total is exact. Every layer in an average pool therefore carries the same unit cost,
  and consuming oldest-first in quantity costs exactly the average.
- A policy change is prospective. Layers keep the value they hold; nothing posted is restated.
- Non-stock, service and bundle items carry no layer and no balance (PRD §6.11).

Value enters a layer from a receipt, a landed charge, an approved invoice price variance or
capitalised non-recoverable tax, and leaves it on a credit note. Where part of the receipt
has already been issued, that share goes to cost of sales instead of the layer, because the
goods it relates to are no longer on the shelf.

## Movements and postings

| Movement | Quantity | Debit | Credit |
| --- | --- | --- | --- |
| Opening stock | + usable | 12000 | 30000 Owner's capital |
| Receipt (procurement) | + usable or quarantine | 12000 or 12100 | 20150 Goods received not invoiced |
| Inspection pass | quarantine → usable | 12000 | 12100 |
| Inspection fail | quarantine → rejected | — | — |
| Return to supplier | − rejected | 20150 | 12100 |
| Relocation | location or bin within a warehouse | — | — |
| Transfer dispatch | − usable at source, + in transit at destination | 12200 | 12000 |
| Transfer receipt | in transit → usable at destination | 12000 | 12200 |
| Reservation, release | ± reserved | — | — |
| Issue to a customer | − usable | 50000 Cost of goods sold | 12000 |
| Customer return | + quarantine, at the cost it left at | 12100 | 50000 |
| Damage, expiry, loss on count or adjustment | − usable | 51000 Inventory write-off and shrinkage | 12000 |
| Gain on count or adjustment | + usable, at the pool's current unit cost | 12000 | 51000 |
| Landed charge | value into the receipt's layers | 12000 / 12100, or 50000 for the issued share | 20100 Other payables |

Inventory accounts 12000, 12100 and 12200 refuse manual journals: a figure posted by hand
there would have no layer behind it, and the valuation could no longer agree with them.

## Traceability

- A **batch**-tracked product carries a batch on every physical movement. A receipt names it,
  with an expiry date where the product requires one; an issue names it.
- A **serial**-tracked product records one movement row per unit, and each serial has a
  current place and state.
- Issuing a batch whose expiry date has passed is refused unless an authorised override is
  recorded on the movement: who, and why (`expiry.override`). Reservation never takes expired
  stock, override or not.
- Manufacturer, inspection criteria, inspector and evidence are kept with the batch and the
  inspection.

## Concurrency

Every change to a pool begins by incrementing the pool's sequence in one statement, which on
PostgreSQL takes the row lock and on SQLite the write lock. The movement then reads the pool
as it stands. Two tills reserving the last unit therefore serialise: one succeeds, and the
other either waits and finds nothing left or is refused — never both succeed.

## Corrections

The ledger is append-only, at the session guard. An operation is corrected by reversing it:
the reversal writes the negated rows, restores or consumes exactly the layers the original
touched, and posts the opposite journal. A reversal is permitted while the operation is the
latest in every pool it touched; once later stock has moved through the same pool, the
correction is a further adjustment, because restating an average after other movements have
used it would change figures already posted.

## Assembly

Per sales order line: required (ordered less cancelled), available (in the warehouse, not
reserved), reserved for this line, incoming (on purchase orders for this line, not yet
received), assembled (staged at the warehouse's assembly location for this line), and
dispatched (issued). Goods received against a purchase order raised for an order line are
reserved for that line automatically; quarantined goods are reserved when they pass inspection.

## Permissions

| Action | Permission |
| --- | --- |
| Read stock, movements and reports | `stock.view` |
| Locations and bins | `location.manage` (added) |
| Opening stock, adjustments, write-offs | `stock.adjust` |
| Transfers and relocation | `stock.transfer` (added) |
| Inspection | `inspection.manage` |
| Count, and approve a count | `stock_count.manage`, `stock_count.approve` |
| Reserve, assemble, issue | `assembly.manage` |
| Issue an expired batch | `expiry.override` |
| Reverse an operation | `stock.adjust` |

## Test obligations

The six exit criteria of the build plan, each by an automated test and by
`tools/verify_phase_8.py`, under both valuation policies where valuation is involved.
