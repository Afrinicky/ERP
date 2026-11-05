# Accounting Model

Satisfies PRD §6.12, §20.8, §25.7, §29.2, §29.9, §30, AC-18 to AC-22.

## Position

Every financial consequence in Nickland ERP is expressed as a balanced journal entry. No
module maintains its own private notion of a balance. Registers, dashboards and reports are
projections of the ledger, never substitutes for it.

Phase 3 delivers the ledger before any module that posts to it. See
[ADR-0005](decisions/adr-0005-phase-recut.md).

## Chart of accounts

Configurable, with a seeded default. Codes are five digits, grouped by the leading digit.

| Range | Group | Statement |
| --- | --- | --- |
| 10000–19999 | Assets | Balance sheet |
| 20000–29999 | Liabilities | Balance sheet |
| 30000–39999 | Equity | Balance sheet |
| 40000–49999 | Revenue | Profit and loss |
| 50000–59999 | Cost of sales | Profit and loss |
| 60000–69999 | Operating expenses | Profit and loss |
| 70000–79999 | Other income and expense | Profit and loss |
| 80000–89999 | Taxation on profit | Profit and loss |

### Seeded accounts

| Code | Account | Normal | Control |
| --- | --- | --- | --- |
| 10100 | Cash on hand | Debit | |
| 10200 | Bank accounts | Debit | |
| 10300 | Mobile Money wallets | Debit | |
| 10400 | Petty cash | Debit | |
| 11000 | Trade receivables | Debit | Customer subledger |
| 11100 | Other receivables | Debit | |
| 11200 | Loans and advances issued | Debit | Borrower subledger |
| 11300 | Withholding tax receivable | Debit | Authority subledger |
| 12000 | Inventory | Debit | |
| 12100 | Goods in quarantine | Debit | |
| 12200 | Goods in transit | Debit | |
| 13000 | Recoverable input tax | Debit | Authority subledger |
| 14000 | Prepaid expenses | Debit | |
| 15000 | Property, plant and equipment | Debit | |
| 20000 | Trade payables | Credit | Supplier subledger |
| 20100 | Other payables | Credit | |
| 20200 | Accrued expenses | Credit | |
| 20300 | Affiliate commission payable | Credit | Affiliate subledger |
| 21000 | Output tax payable | Credit | Authority subledger |
| 21100 | Withholding tax payable | Credit | Authority subledger |
| 21200 | Other statutory liabilities | Credit | Authority subledger |
| 22000 | Customer deposits and advances | Credit | Customer subledger |
| 22100 | Deferred revenue | Credit | |
| 23000 | Loans and borrowings | Credit | Lender subledger |
| 23100 | Accrued interest payable | Credit | Lender subledger |
| 30000 | Owner's capital | Credit | |
| 30100 | Retained earnings | Credit | |
| 30200 | Current year result | Credit | |
| 40000 | Sales revenue — quotation-led | Credit | |
| 40100 | Sales revenue — point of sale | Credit | |
| 40200 | Service revenue | Credit | |
| 40900 | Sales discounts | Debit | |
| 40950 | Sales returns | Debit | |
| 50000 | Cost of goods sold | Debit | |
| 50100 | Non-recoverable tax on purchases | Debit | |
| 50200 | Freight and clearing on sales | Debit | |
| 50300 | Affiliate commission expense | Debit | |
| 51000 | Inventory write-off and shrinkage | Debit | |
| 60000–69999 | Operating expense categories | Debit | |
| 70000 | Interest income | Credit | |
| 70100 | Other income | Credit | |
| 71000 | Interest expense | Debit | |
| 71100 | Bank charges | Debit | |
| 71200 | Exchange differences | Either | |
| 72000 | Bad debt expense | Debit | |
| 80000 | Income tax expense | Debit | |

Operating expense accounts within 60000–69999 are created by the administrator and mapped to
reporting categories.

## Posting rules

The authoritative mapping. Every entry below is one atomic transaction.

### Sale — quotation-led, on credit

| Account | Debit | Credit |
| --- | --- | --- |
| Trade receivables | Invoice total | |
| Sales revenue | | Net of discount |
| Sales discounts | Discount | |
| Output tax payable | | Tax |
| Cost of goods sold | Inventory cost | |
| Inventory | | Inventory cost |
| Affiliate commission expense | Commission | |
| Affiliate commission payable | | Commission |

### Sale — point of sale, cash

| Account | Debit | Credit |
| --- | --- | --- |
| Cash on hand | Tender received | |
| Sales revenue — point of sale | | Net of discount |
| Output tax payable | | Tax |
| Cost of goods sold | Inventory cost | |
| Inventory | | Inventory cost |

Electronic tender debits the relevant account only once the tender is confirmed. An
unconfirmed tender leaves the sale incomplete; nothing is posted. See
[ADR-0009](decisions/adr-0009-pos-payment-confirmation.md).

### Customer receipt

| Account | Debit | Credit |
| --- | --- | --- |
| Cash, bank or Mobile Money | Amount received | |
| Trade receivables | | Amount allocated |
| Withholding tax receivable | Amount withheld by the customer | |

Revenue is not touched. A receipt reduces a receivable; it never creates revenue a second
time, per PRD §30.

### Customer deposit

| Account | Debit | Credit |
| --- | --- | --- |
| Cash, bank or Mobile Money | Deposit received | |
| Customer deposits and advances | | Deposit received |

On satisfaction of the configured recognition condition, the deposit is released to revenue
or applied against the invoice. A deposit is never recognised as revenue on receipt,
per PRD §29.3.

### Goods receipt

| Account | Debit | Credit |
| --- | --- | --- |
| Inventory, or goods in quarantine | Landed cost | |
| Recoverable input tax | Recoverable tax | |
| Trade payables | | Supplier invoice total |
| Other payables | | Freight, duty, clearing and port charges |

Non-recoverable tax is capitalised into the landed cost, not expensed separately.

### Supplier payment

| Account | Debit | Credit |
| --- | --- | --- |
| Trade payables | Amount allocated | |
| Withholding tax payable | | Amount withheld from the supplier |
| Cash, bank or Mobile Money | | Amount paid |

No expense or purchase is recognised. The purchase was recognised at receipt, per PRD §30.

### Expense

| Account | Debit | Credit |
| --- | --- | --- |
| Operating expense category | Net amount | |
| Recoverable input tax | Recoverable tax | |
| Trade or other payables, or cash | | Total |

Where the expense is allocable to landed cost, it debits inventory instead of an expense
account, per PRD §20.3.

### Transfer between the business's own accounts

| Account | Debit | Credit |
| --- | --- | --- |
| Destination account | Amount | |
| Source account | | Amount |

Neither income nor expense is affected, per PRD §25.7 and §30.

### Borrowing — drawdown

| Account | Debit | Credit |
| --- | --- | --- |
| Bank or cash | Proceeds | |
| Loans and borrowings | | Principal |

Proceeds are a liability. They are neither revenue nor profit, per PRD §29.7.

### Borrowing — repayment

| Account | Debit | Credit |
| --- | --- | --- |
| Loans and borrowings | Principal portion | |
| Interest expense | Interest portion | |
| Operating expenses — bank charges | Fee portion | |
| Bank or cash | | Total paid |

Principal repayment is not an operating expense. Interest and fees are recognised separately,
per PRD §29.7.

### Lending — disbursement

| Account | Debit | Credit |
| --- | --- | --- |
| Loans and advances issued | Principal | |
| Bank or cash | | Principal |

Money lent creates a receivable asset. It is not expensed, per PRD §25.7.

### Lending — collection

| Account | Debit | Credit |
| --- | --- | --- |
| Bank or cash | Amount received | |
| Loans and advances issued | | Principal portion |
| Interest income | | Interest portion |

### Tax remittance

| Account | Debit | Credit |
| --- | --- | --- |
| Output tax payable, withholding tax payable or other statutory liability | Amount remitted | |
| Bank or cash | | Amount remitted |

### Affiliate commission payout

| Account | Debit | Credit |
| --- | --- | --- |
| Affiliate commission payable | Amount paid | |
| Bank, cash or Mobile Money | | Amount paid |

The expense was recognised at accrual. Payment settles the liability only.

### Instalment collection

| Account | Debit | Credit |
| --- | --- | --- |
| Cash, bank or Mobile Money | Amount received | |
| Trade receivables | | Principal portion |
| Deferred revenue, or interest income | | Finance charge portion |
| Output tax payable | | Tax portion where applicable |

Allocation across principal, finance charge and tax follows the configured accounting policy,
per PRD §21.

### Stock adjustment and write-off

| Account | Debit | Credit |
| --- | --- | --- |
| Inventory write-off and shrinkage | Loss | |
| Inventory | | Loss |

A gain reverses the direction. Every adjustment carries a reason and an approval reference.

### Reversal

A posted entry is never edited. A reversal creates a new entry with the original's debits and
credits exchanged, references the original, and carries a reason, per PRD §10 and §29.2.

## Subledgers

| Subledger | Control account |
| --- | --- |
| Customers | Trade receivables; customer deposits and advances |
| Suppliers | Trade payables |
| Employees | Other receivables; other payables |
| Affiliates | Affiliate commission payable |
| Tax authorities | Output tax payable; withholding tax payable and receivable; recoverable input tax |
| Lenders | Loans and borrowings; accrued interest payable |
| Borrowers | Loans and advances issued |

Every subledger balance equals its control account balance. A reconciliation report asserts
this and is a Phase 3 exit criterion.

## Period control

| State | Behaviour |
| --- | --- |
| Open | Ordinary posting permitted |
| Closed | Ordinary posting rejected; the period is locked |
| Reopened | Posting permitted; the reopening is recorded with actor, timestamp, reason and approval |

Close requires every reconciliation gate to pass: trial balance sums to zero, every subledger
agrees with its control account, every bank and cash account is reconciled or has a stated
exception, inventory valuation agrees with the inventory control account, and tax obligations
agree with the tax registers.

Year-end close transfers the current year result to retained earnings and establishes opening
balances.

## Accounting basis

Accrual is the primary basis. A cash-basis presentation is available where configured. Every
report states its basis explicitly, per PRD §29.2. The two are never mixed within one
statement.

## Prohibitions

Enforced in the application service, not merely discouraged.

1. An unbalanced entry cannot be posted.
2. A posted entry cannot be amended or deleted.
3. Posting into a closed period is rejected.
4. A repeated idempotency key creates nothing.
5. Cash received is never posted as revenue; cash paid is never posted as expense.
6. A transfer never touches an income or expense account.
7. Loan principal never touches revenue or operating expense.
8. A draft or preview never affects a posted balance.
9. A manual journal requires narration, and above its configured threshold requires a checker
   distinct from the maker.
10. Estimated, forecast, budgeted and actual values are never combined in a posted figure.

## Statements

| Statement | Source |
| --- | --- |
| Trial balance | Account balances for the period; sums to zero |
| General ledger | Journal lines by account |
| Journal register | Journal entries by date and source |
| Profit and loss | Revenue, cost of sales and expense groups |
| Balance sheet | Asset, liability and equity groups |
| Cash-flow statement | Operating, investing and financing classification by account mapping |
| Account activity | Movement and running balance for one account |

Every statement states its period, currency, accounting basis, generation time and applied
filters, per PRD §29.8.
