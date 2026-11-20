# Point of Sale

Phase 10. Satisfies PRD §4.3, §5.2, §6.10, §13 Phase 7, AC-09, AC-10. Tender confirmation per
[ADR-0009](../decisions/adr-0009-pos-payment-confirmation.md). Stock moves through the
[inventory engine](inventory.md). Written at the commencement of the phase, as the
[module index](README.md) requires.

## Purpose

A counter sale is quick to ring up and impossible to get wrong in the books. The register
prices, taxes and totals in the service; the cashier scans, adjusts and takes payment; the sale
posts revenue, tax, the tender and the cost of the goods in one entry, and only when every unit
of payment is real.

## Records

| Record | Holds |
| --- | --- |
| `pos_shifts` | One cashier's session at one register: opening float, cash in and out, the closing count by denomination, expected and counted cash, the variance with its reason, and the supervisor's sign-off |
| `pos_cash_movements` | Cash paid into or taken out of the drawer during a shift, with the reason and its posting |
| `pos_sales` | A sale from cart to completion: customer (walk-in or registered), state, totals, the receipt issued, and the one journal entry and stock operation it posted |
| `pos_sale_lines` | Product, quantity, the resolved price and its source, any permitted override or discount with who allowed it, tax and line total |
| `pos_tenders` | Each part of the payment: method, amount, state, reference, idempotency key, who confirmed it, and for cash the amount received and change given |
| `pos_refunds`, `pos_refund_lines` | A refund, return, exchange or void: the original sale and lines, quantities, reason, who authorised it, how the money went back, and its posting |

## States

**Sale**: open (the cart) → parked ⇄ open → completed → (refunded in part or in full) ·
open → voided before completion (nothing posted) · completed → voided by full reversal in the
same shift.

**Tender** (per ADR-0009): pending → confirmed · pending → failed · pending → abandoned (only at
shift close, with a reason). Cash and on-account are confirmed on entry. Mobile Money, card and
bank transfer start pending and are confirmed against the provider's own receipt — a reference
the cashier records, or a callback from a provider adapter. A sale completes only when every
tender still on it is confirmed and the confirmed total equals the sale total; a pending tender
blocks completion, and nothing is posted, no receipt is issued and no stock moves.

**Shift**: open → submitted (counted) → closed (signed off). A variance, or a tender abandoned
at close, requires a reason and the sign-off of someone holding `pos.shift_approve`.

## Pricing, discount and tax

- The price is resolved in the service, in the fixed order of the price lists: an agreement
  with this customer, the customer's list, the default list, the product's list price.
- A price override needs `pos.price_override`; a discount needs `pos.discount`. Each records
  who and why. Neither is ever computed in the interface.
- Tax is evaluated by the tax engine on each line's net, for the customer's tax treatment, and
  recorded against the sale when it completes.
- Cash change is computed in the service; quick amounts come from `suggested_tenders`.

## Postings

| Event | Debit | Credit |
| --- | --- | --- |
| Sale completed | Each confirmed tender: 10100 Cash on hand (net of change), 10300 Mobile Money wallets, 10200 Bank accounts (card, transfer), 11000 Trade receivables (on account, on the customer's subledger) | 40100 Sales revenue — point of sale (net of discount); 21000 Output tax payable per rule, on the authority's subledger |
| | 50000 Cost of goods sold | 12000 Inventory (from the cost layers, per ADR-0004) |
| Refund | 40950 Sales returns (net); 21000 Output tax payable | The tender account the money leaves |
| | 12000 Inventory, or 12100 Goods in quarantine | 50000 Cost of goods sold, at the cost the goods left at |
| Cash in / cash out | 10100 / counter account | counter account / 10100 |
| Shift variance | 60900 Cash over and short (a shortage) / 10100 | 10100 / 60900 (an overage) |

Each is one journal entry, posted when the event completes, with an idempotency key, so a
repeated request or a duplicated provider callback posts once.

## Refunds, exchanges and voids

A refund names the original sale and the lines and quantities returned, never more than was
sold less what has already been returned. It needs `pos.refund`; a cashier without it has the
refund authorised by someone who has it, and both are recorded. Returned stock goes back to
usable stock or, if it is not fit to resell, to quarantine. An exchange is a refund and a new
sale linked to it. A void reverses a completed sale in full, in the same shift.

## Offline

Cash and on-account sales need nothing beyond the local database. Electronic tenders are
recorded as pending and confirmed against the provider's receipt whether or not a network is
present; no network failure is ever treated as a confirmation or as a failure (a timeout is
pending).

## Permissions

| Action | Permission |
| --- | --- |
| Open the register, build and complete a sale, park and resume | `pos.operate` |
| Open and close one's own shift, cash in and out | `pos.shift_manage` |
| Sign off a shift with a variance or an abandoned tender | `pos.shift_approve` |
| Discount | `pos.discount` |
| Override a price | `pos.price_override` |
| Refund, exchange, void | `pos.refund` |

A sale's journal entry is posted under the permission that authorised the sale.
`journal.post` guards only the entries a person writes by hand (manual journals, opening
balances, year end), so a sales person completes a sale without being able to write journals.

A supervisor authorises at the cashier's register by entering their own credentials. The
password is checked as at sign-in, lockout included, the session it opens is closed at once,
and both names are recorded on the refund.

## Test obligations

The six exit criteria of the build plan, each by an automated test and by
`tools/verify_phase_10.py`.
