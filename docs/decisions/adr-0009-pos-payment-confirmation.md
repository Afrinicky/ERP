# ADR-0009 — Electronic tender confirmation discipline at the point of sale

Status: accepted · Date: 5 November 2026

## Context

PRD §6.10 requires "pending/successful/failed electronic payment states" and directs: "do not
treat prompt initiation as confirmation". PRD §9.4 repeats it: "never imply success merely
because a request was sent." PRD §14 lists "payment prompt mistaken for payment" as a risk
whose control is to "require provider confirmation or reconciliation".

PRD §15 asks for confirmation of which "electronic payment integrations" to build. The pilot
has not yet nominated a provider, a merchant account or a terminal.

PRD §9.4 also requires that core POS cash transactions operate with no internet access.

## Decision

Phase 10 builds the full tender state machine with manual confirmation. A payment-provider
adapter interface is defined. No vendor implementation is built.

| Tender method | Confirmation |
| --- | --- |
| Cash | Immediate on receipt |
| Mobile Money | Explicit confirmation against the provider's own receipt or statement |
| Card | Explicit confirmation against the terminal's own receipt |
| Bank transfer | Explicit confirmation against the bank record |
| On account | Immediate; creates a receivable rather than a cash movement |

## Rationale

The requirement is not integration; the requirement is that an unconfirmed payment is never
recorded as a payment. Three separate PRD sections insist on this. A manual confirmation
discipline satisfies it completely and works with no network.

Building an integration before a provider is nominated would mean guessing at an API,
a callback format, an idempotency scheme and a reconciliation model — and then rebuilding
when the pilot chooses. The adapter interface is the part that can be designed without the
provider.

## Tender states

```
pending ──► confirmed ──► (sale completes)
   │
   ├──► failed      (cashier records the failure; tender is removed from the sale)
   └──► abandoned   (shift close; recorded as an unresolved tender with a reason)
```

| Rule | Specification |
| --- | --- |
| Completion | A sale completes only when every tender is confirmed and the confirmed total equals the sale total |
| Pending tender | Blocks completion. Nothing is posted. No receipt is issued. No stock moves. |
| Posting | Occurs on completion, as one transaction, per the [accounting model](../05-accounting-model.md) |
| Split tender | Each component carries its own independent state |
| Presentation | State is stated in words, never implied by colour alone, per §28.2 |
| Shift close | An unresolved pending tender is reported and requires a reason and supervisor sign-off |
| Idempotency | Every tender carries an idempotency key, so a repeated confirmation posts once |

## Adapter interface

Defined in Phase 10, implemented by no vendor.

```
initiate(amount, reference, instrument) → provider_reference, status
query(provider_reference)               → status
handle_callback(payload)                → status, idempotency_key
reconcile(period)                       → settled transactions
```

| Constraint | Specification |
| --- | --- |
| Isolation | Vendor protocol detail never reaches domain code, per §9.6 |
| Credentials | Held in the secrets mechanism, never in source or logs, per §9.6 |
| Logging | Request and response references and idempotency keys recorded; payloads not |
| Callbacks | Idempotent; sender verified where the provider supports it |
| Status mapping | A provider status maps only to pending, confirmed or failed. No fourth state leaks into the domain. |
| Timeout | A timeout maps to pending, never to failed, because the payment may yet settle |

## Consequences

A cashier confirms electronic tender against the provider's own receipt before completing a
sale. This is slower than an integrated confirmation and is the correct behaviour in its
absence: the alternative is recording payments that may not have occurred.

When a provider is nominated, the integration implements the adapter interface. The state
machine, the posting rules, the reconciliation model and the shift procedure do not change.

## Amendment required

PRD §15 bullet 5 is partially closed: the register and its tender discipline are specified,
and the provider nomination remains with the pilot. Device confirmations — printer and
scanner models — also remain with the pilot and configure rather than constrain Phase 10.
