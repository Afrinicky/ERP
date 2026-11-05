# Security and Audit

Satisfies PRD §9.6, §10, §29.9.

## Authentication

| Concern | Specification |
| --- | --- |
| Password hashing | Argon2id with per-installation tuned parameters |
| Password policy | Minimum length, composition and reuse rules, configurable by the administrator |
| Lockout | Configurable failed-attempt threshold and lockout duration |
| Sessions | Opaque server-side tokens with absolute and idle expiry; no credential is held by the presentation layer |
| Local service | Bound to loopback; accepts only a token issued by the host process at start-up |
| LAN transport | TLS, with certificate provisioning documented for the local network |
| First run | Administrator provisioning forced before any business data may be entered |

## Authorisation

Per PRD §10, permissions are enforced in the application service. Hiding an interface control
is a convenience and is never treated as a control.

### Roles

The eight roles of PRD §3 are seeded. Each is editable, and further roles may be created.

| Role | Principal capabilities |
| --- | --- |
| Owner and administrator | Business setup, branding, templates, pricing, tax configuration, permissions, approvals |
| Sales officer | RFQs, quotations, customer follow-up, sales orders |
| Purchasing officer | Supplier sourcing, comparisons, purchase orders, supplier invoices |
| Receiving and store officer | Goods receipt, inspection, assembly, batch and expiry, stock |
| Dispatch officer | Picking, packing, waybills, delivery, proof of receipt |
| Cashier | Point of sale, payments, receipts, returns, shift reconciliation |
| Accountant | Receivables, payables, expenses, tax, financial reports |
| Auditor | Read-only access to records, documents and the audit trail |

### Permission model

A permission is a verb against a resource: `quotation.issue`, `journal.post`,
`tax_rule.override`, `period.reopen`. The registry is explicit; no permission is implied by
another. Sensitive capabilities are separately held, among them:

- `costing.view` — supplier cost, margin and commission
- `discount.approve` — discount beyond the actor's own limit
- `journal.post` and `journal.approve` — held by different actors where the threshold requires
- `period.close` and `period.reopen`
- `tax_rule.override`
- `debt.write_off`
- `loan.restructure`
- `report.export_sensitive`
- `theme.manage` and `template.publish`

### Scoping

Every request carries a scoping context derived from the actor: business, and branch where
relevant. Services apply it to every query. A record outside the actor's scope is not
returned, and is not reported as existing, per PRD §10.

## Approval and separation of duties

Per PRD §29.9, maker-checker separation applies to journals, large payments, refunds,
write-offs, debt restructuring, loan changes, tax overrides and period reopening.

| Concern | Specification |
| --- | --- |
| Thresholds | Configurable by amount, account, branch, transaction type and risk category |
| Separation | Above its threshold, the checker must be an actor distinct from the maker |
| Evidence | An approval records the approver, timestamp, basis and any attachment |
| Standing state | A transaction awaiting approval cannot be issued, posted or acted upon |
| Override | Every override requires a reason; the reason is mandatory, not optional |

## Audit

Per PRD §10 and §29.9, the audit store is append-only and immutable.

Every recorded event holds: actor, timestamp, session and device, action, source record,
reason where one is required, and before and after values for a changed field.

### Mandatory events

| Domain | Events |
| --- | --- |
| Identity | Sign-in, sign-in failure, lockout, password change, role assignment, permission change |
| Configuration | Business settings, numbering, custom fields, theme, template publication |
| Pricing | Rate change, margin change, commission rate change, discount approval, override |
| Taxation | Rule creation, amendment, withdrawal, override, exemption, recalculation of drafts |
| Ledger | Posting, reversal, manual journal, approval, period close, period reopening |
| Inventory | Adjustment, write-off, count variance, expiry override, quarantine release |
| Finance | Write-off, provision, allocation amendment, reconciliation adjustment |
| Financing | Agreement creation, schedule amendment, restructuring, settlement, waiver |
| Documents | Template publication, document issue, reprint, acknowledgement capture |
| Point of sale | Shift open and close, variance reason, refund, void, price override |

### Integrity

The audit store carries no update or delete path. Entries are written within the same
transaction as the change they describe, so an audited change cannot commit without its audit
record, nor an audit record without its change.

## Data protection

| Concern | Specification |
| --- | --- |
| Validation | Every field validated at the service boundary by a Pydantic contract. Presentation-layer validation is advisory only. |
| Query construction | Parameterised exclusively. No string-composed SQL anywhere in the codebase. |
| File upload | Media type verified by content rather than extension; size bounded; content hashed; stored outside any served path; filename never trusted |
| Attachment access | Served only through an authorised service endpoint that re-checks scope and permission |
| Generated paths | Document paths are derived from identifiers, never from user input |
| Import | Validated, dry-run previewed, bounded in size, and rolled back entirely on rejection |
| Secrets | Held in an operating-system credential store or an encrypted configuration file; never in source, never in logs, per PRD §9.6 |
| Logging | Credentials, tokens, card data, bank identifiers and personal identifiers are redacted. Integration request and response references and idempotency keys are retained; payloads are not. |
| Database credentials | Least privilege; the application's account holds no schema-alteration right at runtime |

## Confidentiality of commercial data

Per PRD §10 and §14, supplier cost, business margin, affiliate commission, payroll, bank
identifiers and loan agreements are never exposed to unauthorised roles or to customer-facing
output.

Four independent controls:

1. The `costing.view` permission gates every service response carrying a cost field.
2. Internal fields are absent from the palette of a customer-facing template.
3. A customer-facing template cannot be published with an internal binding or expression.
4. An automated render test asserts the absence of cost, margin and commission from the
   extracted text of every default customer-facing document.

## Integrity of posted records

Per PRD §10:

1. Posted financial and stock records are never deleted or overwritten. Correction is by
   reversal or audited adjustment.
2. Issued quotations and documents retain their snapshots.
3. Multi-record financial events are atomic.
4. A closed period rejects ordinary posting.
5. Every externally triggered financial operation carries an idempotency key, so retries,
   duplicated payment callbacks and any future synchronisation queue cannot post twice.

## Backup and recovery

Per PRD §9.5 and §29.9.

| Concern | Specification |
| --- | --- |
| Scope | Database and the attachment store together, as one consistent set |
| Schedule | Configurable; daily by default |
| Encryption | Available, and recommended where backups leave the host |
| Retention | Configurable, with a stated minimum |
| Verification | Each backup is verified for integrity on completion |
| Restore drill | A documented, periodic procedure; a successful drill is a release gate for the LAN edition |
| Pre-migration | A verified backup is taken before any schema upgrade, and the application does not start until migration success is confirmed |
| Disaster recovery | Documented procedure, including the recovery point and recovery time the configuration actually achieves |

## Integration security

Per PRD §9.6:

1. Vendor-specific protocol handling is isolated in adapters; no vendor detail reaches domain
   code.
2. Credentials are held in the secrets mechanism and are never logged.
3. Request and response references and idempotency keys are recorded; payloads carrying
   sensitive data are not.
4. An initiated request is never recorded as a completed one. A payment prompt is not proof of
   payment, per PRD §6.10 and §9.4.
5. Callback handling is idempotent and verifies the sender where the provider supports it.

## Release security

| Concern | Specification |
| --- | --- |
| Dependencies | Pinned by lock file; reviewed for licence and maintenance status before adoption |
| Vulnerability scanning | Dependency audit in continuous integration; a release is blocked on a known high-severity advisory |
| Secret scanning | Continuous integration rejects any committed credential |
| Build reproducibility | Builds are reproducible from a tagged commit |
| Installer signing | Windows installers are signed; an unsigned build is never distributed |
| Update distribution | Served over TLS with signature verification before application |

## Compliance statement

Per PRD §19.6 and §24, Nickland ERP supports recordkeeping, calculation, reconciliation and
reporting. It does not replace a qualified tax professional or an official filing platform.
Tax configuration carries no jurisdiction assumption; the initial Ghana configuration must be
validated against current official guidance and professional advice before it is used for
any statutory filing or tax decision. This statement is surfaced in the application, in the
taxation workspace, and is not dismissible by configuration.
