# Quality Strategy

Satisfies PRD §9.8, §11.

## Obligations

Per PRD §9.8, automated unit tests are mandatory for pricing, tax, commission, instalment
schedules, loan amortisation, accounting postings and inventory calculations. These are not
discretionary and no phase closes without them.

## Test layers

| Layer | Subject | Database | Speed |
| --- | --- | --- | --- |
| Unit | Domain calculations and invariants, with no input or output | None | Under 10 seconds for the whole suite |
| Integration | Application services, repositories, transactions, permissions | SQLite and PostgreSQL | Under 5 minutes |
| End to end | Complete workflows through the service layer | Both | Under 15 minutes |
| Concurrency | Simultaneous users and contended resources | PostgreSQL | Under 10 minutes |
| Presentation | Component behaviour, keyboard operation, accessibility | None | Under 3 minutes |
| Visual | Component rendering in both presets at three breakpoints | None | Under 5 minutes |

## Calculation tests

Per PRD §9.8. Each item below is a test module, not a single test.

| Subject | Coverage |
| --- | --- |
| Pricing | Seven allocation methods including residue; both margin methods and their precedence; commission in and out of every scope; gross-up preserving margin; uplift per duration band; discount within and beyond limits; the canonical sequence end to end; rounding reconciliation at two and four decimal places; snapshot immutability |
| Taxation | Six calculation methods; inclusive extraction against exclusive addition; exemption and zero-rating producing a recorded zero; withholding; recoverable against non-recoverable; configured cascading; effective-dated selection at a boundary date; the five evaluation results; override requiring reason; prospective-only rate change |
| Commission | Scope resolution; effective-dated rates; partial fulfilment; payable only on customer payment; state transitions; cancellation |
| Instalments | Schedule generation per duration band; deposit handling; partial payment; overpayment; early settlement; waiver; reschedule producing a new version with the original intact |
| Loan amortisation | No interest, fixed, flat rate, reducing balance, interest-only and balloon; daily to custom frequency; grace period; multiple drawdowns; configurable allocation order; early settlement quotation; restructuring preserving every prior version |
| Accounting | Every posting rule in the accounting model; balance assertion; reversal; idempotency; period rejection; subledger-to-control agreement; receipt not creating revenue; payment not creating expense; transfer touching neither; loan principal touching neither |
| Inventory | Weighted average on receipt and issue; FIFO layer consumption; landed cost allocation; quantity-state reconciliation; batch and serial constraint; expiry block and override; quarantine exclusion; count variance; valuation agreeing with the control account |

Every calculation test asserts exact decimal equality. No test tolerates a floating-point
epsilon, because no floating-point arithmetic exists in the path.

## Workflow tests

Per PRD §9.8.

1. Quotation to order to procurement to receipt to assembly to dispatch to acknowledgement to
   invoice to payment, with lines sourced from stock and from two suppliers.
2. POS cash sale, then return, then refund, with authorisation.
3. POS split tender with one electronic component left unconfirmed, asserting the sale does
   not complete.
4. Partial payments across several invoices with an unapplied residue.
5. Debt collection: ageing progression, reminder, promised date, partial recovery, write-off.
6. Loan drawdown, scheduled repayments, a missed instalment, a restructure, early settlement.
7. Period close with every reconciliation gate, then an authorised reopening.
8. Backup and restore, asserting the dataset and attachments are reproduced.
9. Template amendment after issue, asserting the issued document renders unchanged.
10. Tax rule withdrawal, asserting posted documents are numerically unchanged.

## Resilience tests

Per PRD §9.8.

| Scenario | Assertion |
| --- | --- |
| Simultaneous LAN users | No duplicate document number; no lost stock reservation; no double posting |
| Contended last unit | Exactly one reservation succeeds |
| Duplicate payment callback | Exactly one posting |
| Interrupted transaction | No partial stock or accounting posting |
| Offline restart mid-workflow | Draft recovered; nothing posted that was not committed |
| Failed migration | Application refuses to start; backup is intact; rollback succeeds |
| Power loss during sale completion | Either the whole sale is posted or none of it |
| Host restart with clients connected | Clients recover; no partial posting |
| Storage exhaustion | Clear failure; no corrupt write; no silent data loss |

## Confidentiality tests

Per PRD §14.

1. Every default customer-facing template rendered against a transaction with known cost,
   margin and commission; the extracted PDF text is asserted to contain none of them.
2. Every service response is asserted to omit cost fields for an actor lacking `costing.view`.
3. A customer-facing template is asserted unable to publish with an internal binding.
4. An expression in a customer-facing template is asserted unable to reference an internal
   field.

## Expression safety tests

Per PRD §6.6.

1. Arbitrary code in an expression is rejected at parse time.
2. No expression can resolve an identifier outside the palette.
3. Attribute access, indexing, assignment and imports are rejected.
4. Depth, term count and evaluation time bounds are enforced.
5. A failed expression renders a placeholder and does not abort the document.

## Presentation tests

| Subject | Assertion |
| --- | --- |
| Keyboard operation | Every workflow completable without a pointer, including the document designer |
| Focus | Always visible; trapped only within a modal dialog |
| Accessible naming | Every control, icon and chart carries an accessible name |
| Announcement | Validation and status changes announced to assistive technology |
| Contrast | Every shipped token pairing and every saved custom theme meets WCAG AA |
| Theme validation | A failing custom colour pairing is rejected by the theme editor |
| Token discipline | No colour, font, spacing or radius literal outside the token layer |
| Scaling | Usable at 200% zoom and at 125% and 150% Windows display scaling |
| Content | Prohibited vocabulary and punctuation absent |
| Charts | Period, currency, unit, legend, empty state, loading state and table alternative present |

## Performance targets

Per PRD §11, measured on the pilot's specified office hardware.

| Operation | Target |
| --- | --- |
| Application start to usable | Under 4 seconds |
| Catalogue search, 50,000 rows | Under 200 ms |
| Document-number or party search | Under 200 ms |
| Quotation open, 50 lines | Under 1 second |
| Pricing recalculation, 50 lines | Under 300 ms |
| POS product search | Under 100 ms |
| POS scan to cart | Under 150 ms |
| POS sale completion to receipt | Under 5 seconds |
| Pro forma render to PDF | Under 2 seconds |
| Dashboard with twelve measures and six charts | Under 2 seconds |
| Monthly management pack | Under 30 seconds |

## Environments

| Environment | Purpose |
| --- | --- |
| Local | Developer machine; SQLite; seeded sample business |
| Continuous integration | Every push; both engines; full suite less the visual layer |
| Nightly | Full suite including visual regression, concurrency and performance |
| Staging | Clean Windows 11 virtual machine; installer tested from the signed artefact |
| LAN staging | One host and three clients; concurrency and restore drill |

## Release engineering

Per PRD §9.8.

| Control | Specification |
| --- | --- |
| Dependency pinning | Lock file committed; no unpinned dependency reaches a release |
| Reproducible build | A tagged commit produces an identical artefact |
| Release notes | Every release states changes, migrations, and any required operator action |
| Installer testing | Every release installed on a clean machine before distribution |
| Upgrade testing | Upgrade from the previous release tested with a populated database |
| Rollback | Documented, with the database restore point stated |
| Signing | Installers signed; unsigned builds are never distributed |
| Update procedure | Documented, including the backup taken before migration |

## Gates

| Gate | Condition |
| --- | --- |
| Pull request | Unit, integration and presentation suites pass; linting, formatting and type checking pass; no new import cycle; coverage of changed calculation code is complete |
| Phase exit | The phase's stated exit criteria are demonstrated; the full suite passes against both engines; no known accounting or confidentiality defect remains |
| LAN release | PostgreSQL suite, concurrency suite and a successful restore drill |
| Any release | Clean-machine installation, upgrade test, signed installer, release notes |

## Coverage expectations

Coverage is not a target in itself. The following are requirements.

1. Every calculation path in pricing, tax, commission, instalments, amortisation, postings and
   inventory is covered.
2. Every posting rule in the accounting model has a test.
3. Every permission has a test asserting denial at the service layer.
4. Every state machine has a test for every permitted and every forbidden transition.
5. Every acceptance criterion in PRD §12, §22, §26, §30 and §31 maps to at least one
   automated test. See the [traceability matrix](10-traceability.md).
