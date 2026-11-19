# Dispatch and Proof of Delivery

Phase 9. Satisfies PRD §6.9, §13 Phase 6 in part, AC-08. Built on the
[inventory module](inventory.md): every quantity that leaves the warehouse, comes back, or
is lost on the way is a stock movement through the same engine, valued from the same
layers. Written at the commencement of the phase, as the [module index](README.md) requires.

## Purpose

An order is delivered in one or more shipments. Each shipment is picked, packed and
dispatched with its own waybill, and is closed by what the customer actually signed for.
The difference between what left and what was received is never lost: it is returned to
the store, held for inspection, or written off, and each outcome is posted.

## Records

| Record | Holds |
| --- | --- |
| `shipments` | One consignment for one sales order: mode, carrier, driver, vehicle, destination, package count, tracking reference, dates, state, and the documents issued for it |
| `shipment_lines` | Per order line: quantity shipped, delivered, returned to the store, returned damaged, lost, and returned by the customer after delivery |
| `delivery_exceptions` | Every outcome other than a clean delivery: what, how many, why, who, and the stock operation that posted it |

A shipment is numbered after its order — `SO-2026-0001/1`, `/2` — so several waybills for
one order read as what they are. The waybill, delivery note, packing list and pick list
are issued documents with their own numbers, linked to the shipment and, through it, to the
order.

## States

| State | Meaning | Next |
| --- | --- | --- |
| Prepared | Lines and quantities chosen from what is reserved for the order | Packed, Cancelled |
| Packed | Stock staged at the assembly bay, packages counted | Dispatched, Cancelled |
| Dispatched | Goods have left: in transit at cost | Delivered, Partially delivered, Failed |
| Delivered | Every unit shipped was received | — |
| Partially delivered | Some were received; the rest is accounted for as an exception | — |
| Failed | Nothing was received; everything came back | — |
| Cancelled | Withdrawn before dispatch; no stock moved | — |

## Modes

Carrier, own vehicle, courier, and customer collection. A collection needs no driver or
vehicle, and its waybill names the person who collected.

## Movements and postings

| Event | Stock | Debit | Credit |
| --- | --- | --- | --- |
| Dispatch | Usable at the assembly bay → in transit at the dispatch location; the reservation is consumed | 12200 Goods in transit | 12000 Inventory |
| Delivery | − in transit | 50000 Cost of goods sold | 12200 |
| Returned to the store (failed or short delivery) | In transit → usable at the assembly bay, reserved again for the line | 12000 | 12200 |
| Returned damaged | In transit → quarantine, for inspection | 12100 Goods in quarantine | 12200 |
| Lost in transit | − in transit | 51000 Inventory write-off and shrinkage | 12200 |
| Customer return after delivery | + quarantine, at the cost it was delivered at | 12100 | 50000 |

Delivery and every exception draw only on the layers the shipment's own dispatch created,
so the cost of what a customer received is the cost of what was put on that truck, and
never of another shipment or a transfer in transit to the same warehouse.

Revenue and the receivable are the sales invoice's concern and are posted when invoicing
arrives; a customer return records whether a replacement is owed or the quantity is
cancelled, so the invoice and any credit note follow the order.

## Quantities on the order line

- **Dispatched** is what has left and not come back.
- **Delivered** is what the customer signed for, less what they later returned.
- **Outstanding** is what was ordered, less cancelled, less delivered. A partial delivery
  leaves exactly the undelivered remainder outstanding; goods returned to the store are
  staged again for the line, so the next shipment can carry them.
- A damaged or lost unit leaves the bay for good, so it is neither staged nor dispatched;
  the line needs another unit reserved and staged.

## Proof of delivery

Confirming a delivery records the recipient — name, role, place, time — and the evidence,
a drawn signature or a scanned copy, as an acknowledgement of the shipment's waybill. The
acknowledgement is append-only, as every document acknowledgement is, and the waybill is
reprinted from its stored bytes, so the copy the driver carried and the record of who signed
for it can both be produced unchanged.

## Documents

| Document | Issued | Lists |
| --- | --- | --- |
| Pick list | Prepared onwards | Each line by batch and location, earliest expiry first |
| Packing list | Packed onwards | Lines and package count |
| Waybill | Dispatched onwards | Lines, batches, dispatch detail; signed by the recipient |
| Delivery note | Dispatched onwards | Lines and batches, for the customer's goods-inwards |

Each is issued once per shipment; asking again serves the stored copy. Prices are off on all
four by default and are the administrator's decision in the document designer.

## Permissions

| Action | Permission |
| --- | --- |
| Read shipments | `dispatch.view` |
| Prepare, pack, dispatch, cancel, record an exception | `dispatch.manage` |
| Issue a waybill, delivery note, packing list or pick list | `waybill.issue` |
| Confirm delivery with proof | `proof_of_delivery.capture` |
| Accept a return after delivery | `sales_return.manage` |

## Test obligations

The three exit criteria of the build plan, each by an automated test and by
`tools/verify_phase_9.py`.
