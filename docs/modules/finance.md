# Financial Management Core

Phase 11. Satisfies PRD §20.1–§20.5, §29.1, §29.3–§29.5, AC-18 to AC-20. Postings follow the
[accounting model](../05-accounting-model.md). Written at the commencement of the phase, as
the [module index](README.md) requires.

## The rule the phase rests on

Revenue is earned when goods or services are invoiced; expense is incurred when a cost is
recognised. Money moving is neither. A receipt settles a receivable or creates a liability;
a payment settles a payable or an expense already recognised; a transfer moves money between
two of the business's own accounts. No receipt posts to revenue and no payment posts to
expense, and the service refuses a draft that would.

## Treasury

| Record | Holds |
| --- | --- |
| `financial_accounts` | A cash drawer, petty cash box, bank account or Mobile Money wallet: its kind, name, institution and number, custodian, and the ledger account it posts to |
| `treasury_movements` | A transfer between two accounts, a bank charge, interest received, or an adjustment, with its reason and posting |

Each base cash account (10100, 10200, 10300, 10400) is a financial account from the start.
Another bank or wallet is a new ledger account in the same group (10201, 10301, …), so every
account has its own balance, cashbook and reconciliation. The cashbook is the ledger account,
with a running balance, read for a period.

| Event | Debit | Credit |
| --- | --- | --- |
| Transfer | Receiving account | Sending account |
| Transfer charge | 71100 Bank charges | Sending account |
| Bank charge | 71100 Bank charges | The account |
| Interest received | The account | 70000 Interest income |
| Adjustment | The account or 71200 | 71200 Exchange differences or the account |

## Receivables

| Record | Holds |
| --- | --- |
| `sales_invoices`, `sales_invoice_lines` | A customer invoice from a sales order's delivered or ordered quantities, or a register sale on account: lines, tax by rule, totals, due date from the customer's terms, amounts paid, credited and written off |
| `customer_credit_notes` | A credit against an invoice, with its reason |
| `customer_receipts`, `receipt_allocations` | Money received from a customer into a financial account, allocated to invoices; what is not allocated stays on account as the customer's credit |
| `customer_deposits` | An advance held as a liability, against an order or on account, with the condition for applying it: the invoice, or the delivery of the goods |
| `collection_notes` | A promise to pay, a dispute, or a note of contact, against a customer or invoice |
| `debt_provisions` | A provision against a doubtful invoice, released or used on write-off |

| Event | Debit | Credit |
| --- | --- | --- |
| Invoice issued | 11000 Trade receivables (customer) | 40000 Sales revenue — quotation-led (net); 21000 Output tax payable (per rule, authority) |
| Receipt | The financial account | 11000 (customer) |
| Deposit received | The financial account | 22000 Customer deposits and advances (customer) |
| Deposit applied, once its condition is met | 22000 (customer) | 11000 (customer) |
| Credit note | 40950 Sales returns; 21000 | 11000 |
| Provision | 72000 Bad debt expense | 11010 Allowance for doubtful debts |
| Write-off | 11010 (as far as provided); 72000 for the rest | 11000 |
| Refund of a customer's credit | 11000 | The financial account |

A credit limit is checked when an invoice is issued: an invoice taking the customer over the
limit, or any invoice to a customer on hold, needs `payment.approve` and a reason. Ageing runs
by due date: not yet due, 1–30, 31–60, 61–90, over 90 days. The collection worklist lists
overdue invoices, most overdue first, with the latest promise or dispute; a disputed invoice
is held out of reminders.

## Expenses

| Record | Holds |
| --- | --- |
| `expenses` | Payee, category (the expense or asset account), amount, recoverable input tax, date, the payee's reference, operating or capital, how it is settled (from a financial account, on credit to the payee, or as a staff claim), approval |
| `recurring_expenses` | A template raised weekly, monthly or quarterly |

An expense at or above the approval threshold waits for `expense.approve` and posts on
approval; below it, it posts on entry. A second expense with the same payee and reference, or
the same payee, amount and date, is refused unless the duplicate is confirmed with a reason. A
capital expense posts to an asset account (15000), never to an expense account.

| Event | Debit | Credit |
| --- | --- | --- |
| Expense paid | The expense or asset account; 13000 for recoverable tax | The financial account |
| Expense on credit | As above | 20100 Other payables |
| Staff claim | As above | 20400 Staff claims payable (employee) |
| Claim or credit settled | 20100 or 20400 | The financial account |

## Reconciliation

A statement is imported as CSV (date, description, reference, and amount or debit and credit)
against a financial account. Each line is matched to one or more unreconciled ledger lines on
that account. Suggestions come from the same amount within three days, reference first. A
line already imported, by date, amount and reference, is flagged as a duplicate and not
matched. What is left is the unmatched queue; a line with no counterpart (a charge, interest)
is posted from the queue as a treasury movement and matched. A statement is reconciled when
every line is matched and the statement's closing balance equals the ledger balance at its
date.

Source reconciliation compares each source of money with the ledger: POS shifts (counted
against expected, variances posted), each bank and Mobile Money account (statements), customer
receipts, supplier payments and tax payments (records against postings).

## Command centre

Headline measures are computed from the ledger for a period, each with the accounts it is
made of, so drilling down lists the journal lines that make it and the total always equals
the ledger:

| Measure | From |
| --- | --- |
| Gross sales | Credits to 40000, 40100, 40200 |
| Net sales | Gross sales less 40900 and 40950 |
| Recognised revenue | All revenue accounts, net |
| Cost of goods sold | Cost of sales accounts |
| Gross profit | Recognised revenue less cost of goods sold |
| Operating expenses | Operating expense accounts |
| Net profit | The profit and loss result |
| Cash and bank | Every financial account, at the period end |
| Receivables, payables, loan balances, tax liabilities | 11000 less 11010; 20000; 23000; 21000–21200 |
| Net working capital | Current assets less current liabilities |

Revenue is also broken down by channel, product, category, customer, branch and sales person
from the invoice and register lines.

Four layouts are supplied (owner, finance manager, accountant, branch manager). A person
arranges, resizes and saves their own; `dashboard.configure` saves a layout for a role.

## Payables

Added to Phase 7's: payment batches (several suppliers, one approval under `payment.approve`),
invoice priority, supplier statements, and the payment voucher issued for each payment.

## Permissions

| Action | Permission |
| --- | --- |
| View receivables, ageing, statements | `receivable.view` |
| Issue an invoice | `sales_invoice.issue` |
| Credit note | `sales_return.manage` |
| Record a receipt or deposit, collection notes | `receipt.manage` |
| Provision, write off | `debt.write_off` |
| Financial accounts | `financial_account.manage` |
| Transfers and treasury movements | `transfer.manage` |
| Record expenses; approve them | `expense.manage`; `expense.approve` |
| Statements and matching | `reconciliation.manage` |
| Save a layout for a role | `dashboard.configure` |

## Test obligations

The six exit criteria of the build plan, each by an automated test and by
`tools/verify_phase_11.py`.
