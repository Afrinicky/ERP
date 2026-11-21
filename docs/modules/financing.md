# Debt, Loans and Financing

Phase 12. PRD §25, §29.7, AC-23 to AC-30.

A loan is a formal agreement, never income or expense. Money borrowed is a liability and
money lent is an asset; drawing either down and repaying its principal moves the balance
and the cash together. Only interest, fees and penalties reach profit, each on its own
account, and only when the agreement says they are owed.

## Accounts

| Code | Account | Subledger | Use |
| --- | --- | --- | --- |
| 11200 | Loans and advances issued | borrower | Principal lent, including staff advances |
| 11210 | Interest receivable on loans issued | borrower | Interest, fees and penalties due to the business |
| 23000 | Loans and borrowings | lender | Principal borrowed, and interest capitalised in a grace period |
| 23100 | Accrued interest payable | lender | Interest, fees and penalties the business owes |
| 70000 | Interest income | | Interest on loans issued |
| 70200 | Loan fee income | | Arrangement fees, instalment fees and penalties charged by the business |
| 71000 | Interest expense | | Interest on borrowings |
| 71300 | Loan fees and finance charges | | Fees and penalties the business pays |
| 72000 | Bad debt expense | | A loan issued written off |
| 11100 | Other receivables | | Other debts owed to the business |
| 20500 | Refundable deposits and other amounts held | | Other debts the business owes |

Every posting on a control account names the agreement as its subledger party, so each
agreement's balance in the ledger is read directly and checked against its own record: the
agreement screen shows "Agrees with the ledger" or "Differs from the ledger".

## Postings

| Event | Borrowing | Lending |
| --- | --- | --- |
| Drawdown | Dr bank (net of fee), Dr 71300 fee / Cr 23000 | Dr 11200 / Cr bank (net of fee), Cr 70200 fee |
| Instalment falls due | Dr 71000 interest, Dr 71300 fees / Cr 23100 | Dr 11210 / Cr 70000 interest, Cr 70200 fees |
| Grace period | Dr 71000 / Cr 23000 (capitalised) | Dr 11200 / Cr 70000 (capitalised) |
| Repayment | Dr 23000 principal, Dr 23100 charges / Cr bank | Dr bank / Cr 11200 principal, Cr 11210 charges |
| Penalty | Dr 71300 / Cr 23100 | Dr 11210 / Cr 70200 |
| Early settlement | Interest to the day and the settlement fee are accrued, then the payment clears everything | the same, reversed |
| Refinancing | Dr 23000 and 23100 (old) / Cr 23000 (new); no cash | Dr 11200 (new) / Cr 11200 and 11210 (old) |
| Write-off | not permitted | Dr 72000 / Cr 11200, Cr 11210 |

Interest and fees are recognised when an instalment falls due (`POST /financing/accrue`, and
automatically for any instalment a repayment touches), not when cash moves.

## Interest models

`none`, `fixed` (a charge agreed in advance, spread evenly), `flat` (principal × rate ×
term, spread evenly), `reducing_balance` (level instalment on the balance outstanding),
`interest_only` (principal at maturity), `balloon` (reducing balance leaving a final
lump sum) and `custom` (instalments entered by hand, whose principal must sum to the amount
lent). Grace periods capitalise interest; interest-only periods pay interest without
reducing principal. Compounding on a cycle different from the repayments is converted to
the effective periodic rate. Every figure is rounded to the pesewa as it is produced; the
last instalment takes whatever principal remains, so the principal column always sums
exactly.

## Allocation

A repayment settles the oldest instalment first, and within it each component in the order
the agreement names: by default penalties, fees, interest, principal. Anything beyond what
has fallen due prepays principal where the agreement allows it, which produces a new
schedule version for the instalments still to come; otherwise it pays the next instalments
ahead of their dates. More than is owed is refused: an agreement is closed by settlement.

## Versions

The schedule built from the terms is version 1, the schedule as signed. It is never
recalculated in place. A reschedule, payment holiday, deferment, interest change or
restructure is proposed as a new version beside it, built on the principal outstanding at
its effective date, with the reason recorded. Nothing changes until it is approved; on
approval the current version is superseded, kept exactly as it stood, and any interest,
fees and penalties already recognised and unpaid are carried into the new version as an
arrears row, so nothing owed is lost and nothing is owed twice. A revision is refused if
the agreement has moved since it was proposed.

## Approval and safeguards

- `loan.manage` records and amends agreements; a draft's terms may change, a submitted
  agreement's only by revision.
- Agreements at or above the threshold (`loans.approval_threshold`) wait for approval by
  someone other than the submitter, resting on documented authorisation: an attachment or a
  note of who authorised it and how. Below it, an agreement is active once submitted.
- `loan.restructure` proposes and decides revisions and refinances; a material revision is
  decided by someone other than its proposer.
- `repayment.manage` records drawdowns, repayments and settlements.
- `debt.write_off` writes off a loan issued. A debt the business owes is never written off.
- A penalty is charged only where the agreement provides a penalty rate, and only on arrears.
- Settled, refinanced and written-off agreements, with every version and transaction, are
  kept.

## Registers

Two registers, read from the record that creates each debt:

| Owed to the business | Owed by the business |
| --- | --- |
| Customer invoices | Supplier invoices |
| Loans and advances issued | Unpaid expenses and staff claims |
| Other receivables | Tax obligations |
| | Commissions payable |
| | Customer receipts not yet applied (refunds due) |
| | Borrowings |
| | Other payables |

Each source appears once, and the register refuses to show totals if it ever does not. An
"other debt" entered by hand is refused if its reference is an invoice, expense or
agreement already in the register, or if the same debt is already recorded. The two sides
are never netted.

An agreement's overdue amount is its unpaid instalments, not its whole balance; it ages by
those instalments, and the rest stands as not yet due.

## Analysis

- **Overview:** receivables and payables, loans issued and borrowings with their charges,
  due soon and overdue, collected and paid in the month, debt service against cash, ageing,
  largest debtors and creditors, the maturity profile and six months of trend.
- **Calendar:** money expected in and out by date.
- **Cash flow by class (AC-29):** each entry's cash is attributed to the accounts opposite
  it, netted within each class: collections from customers, payments to suppliers,
  expenses, commissions, tax, loan principal borrowed and repaid, interest and finance
  charges paid, loan principal lent and collected, interest received, owner's capital, and
  transfers between the business's own accounts.

## Verification

`tools/verify_phase_12.py` checks the six exit criteria and AC-23, AC-28, AC-29 and AC-30
against a live database. `tests/integration/test_loans.py`, `test_debts.py` and
`tests/e2e/test_financing_api.py` run on SQLite and PostgreSQL.
