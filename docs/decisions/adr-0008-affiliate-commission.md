# ADR-0008 — Affiliate commission basis and payout trigger

Status: accepted · Date: 5 November 2026

## Context

PRD §15 asks for confirmation of "affiliate commission calculation basis, discount/tax
treatment and payout trigger". PRD §6.4 requires the accrued, approved, payable and paid
states, "support partial fulfilment and configurable payout trigger", and that rates vary by
product and effective date — but does not say what the commission is calculated on, or what
moves it between states. See [conflict register](../09-conflict-register.md) C-07.

## Decision

| Attribute | Specification |
| --- | --- |
| Basis | The line's selling price excluding tax, before customer discount |
| Method | Fixed amount per unit, or a percentage of the basis, grossed up |
| Scope | Selected products, selected categories, selected quotation lines, or all eligible lines |
| Rate variation | By product and by effective date |
| Accrual | At invoice |
| Payable | On receipt of the customer's payment |
| Partial fulfilment | Proportional to the fulfilled and paid portion |
| Visibility | Internal costing and profitability only |

Both the basis and the trigger remain configurable, per §6.4.

## Rationale

**Excluding tax.** Tax is money held for an authority. Paying commission on it would mean the
business pays a referrer out of a statutory liability. No reading of §6.4 supports that.

**Before customer discount.** A discount is a decision of the sales officer. The affiliate is
not party to it and should not fund it. This follows from the sequence of
[ADR-0007](adr-0007-pricing-sequence.md), where commission precedes discount.

**On selling price rather than cost.** §6.4 requires a "configurable calculation base", and
§14 identifies as a risk that commission reduces margin. A referrer introduced a sale, so
their entitlement tracks the sale's value. Calculating on cost would mean a high-margin sale
and a low-margin sale of the same item pay identical commission, which does not reflect what
was introduced.

**Payable on customer payment.** §6.4 requires a "configurable payout trigger" and
distinguishes payable from paid. Paying commission before the customer has paid converts a
referral cost into working capital exposure, and on a bad debt the business pays commission on
revenue it never collected. Accruing at invoice records the obligation correctly; making it
payable at collection protects cash.

**Gross-up.** A commission expressed as a percentage of the selling price must be grossed up,
or the business's realised margin falls by the commission. See ADR-0007.

## States

```
accrued ──► approved ──► payable ──► paid
   │            │
   └──► cancelled (reason and audit record required)
```

| Transition | Trigger |
| --- | --- |
| → accrued | Invoice issued |
| → approved | Authorised review, per §6.4 |
| → payable | Customer payment received, proportionally on partial payment |
| → paid | Commission payment recorded against the affiliate |
| → cancelled | Sale reversed, credit note issued, or authorised cancellation with reason |

## Accounting

Accrual debits `50300 Affiliate commission expense` and credits
`20300 Affiliate commission payable`. Payment debits the payable and credits the paying
account. The expense is recognised at accrual, not at payment, per the
[accounting model](../05-accounting-model.md).

## Controls

1. Rate changes and overrides record actor, timestamp, reason and prior value, per §6.4.
2. Line-level contribution after commission is displayed before the quotation is issued, so
   margin erosion is visible in advance, per §14.
3. Commission is never exposed on customer-facing output. Four independent controls enforce
   this; see [security and audit](../07-security-and-audit.md).
4. Approval limits apply where the commission exceeds a configured threshold.

## Consequences

An affiliate waits for the customer to pay. This is stated in the referrer agreement rather
than discovered. Partial payment produces a partial entitlement, so a commission may be
payable in instalments, which the register tracks.

## Amendment required

PRD §6.4 is extended with the basis, the trigger and the gross-up. §15 bullet 2 is closed.
