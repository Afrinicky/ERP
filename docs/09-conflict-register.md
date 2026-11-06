# Requirements Conflict Register

Contradictions, omissions and ambiguities identified in PRD v1.4, with the ratified
resolution for each. The PRD was authored across four revisions (v1.0 to v1.3) without a
consolidating pass, and earlier text was not always withdrawn when later text superseded it.

Items marked **Amendment required** need a PRD edit so the document and the implementation
agree. Until that edit is made, this register governs.

---

## C-01 — Presentation technology

**Severity: critical. Amendment required.**

| Source | Statement |
| --- | --- |
| §9.1 | "Python is the primary implementation language for the desktop application… Do not use React/TypeScript with Tauri as the assumed core application stack." |
| §9.2 | "PySide6 (Qt for Python) for the Windows desktop user interface, including POS, dashboards, data-entry screens, administration and document-design tools." |
| §9 summary table | "Desktop — Windows desktop; React + TypeScript; Tauri recommended." |
| §9.7 | "Build the primary desktop UI with PySide6 widgets and reusable design components." |

Three positions in one section. §9.1 forbids React with Tauri; the §9 table recommends
exactly that; §9.2 and §9.7 mandate PySide6.

**Resolution.** Python holds the whole business layer, satisfying the substance of §9.1. The
presentation layer is React with TypeScript, rendered in an embedded WebView2 surface hosted
by pywebview. Tauri is not used, so §9.1's named prohibition is honoured. PySide6 is not used.

**Amendment.** §9.2 and §9.7 are revised to specify the embedded web presentation layer.
The §9 summary table row is corrected to remove Tauri. §9.1's prohibition is clarified to
apply to the business layer, which it does.

See [ADR-0001](decisions/adr-0001-python-core-web-presentation.md).

---

## C-02 — Colour system

**Severity: high. Amendment required.**

| Source | Statement |
| --- | --- |
| §7.1 | "Professional light interface by default, with bluish-black/navy as the initial primary brand colour." |
| Document control | "Professional light interface; bluish-black/navy default." |
| §28.1 | Charcoal `#252525`, orange `#F7931E`, beige `#C9A078`, off-white `#F7F5F3`. |
| §31 | "The default application theme visibly uses charcoal/black, orange, beige and white/off-white." |

v1.3 §28 supersedes v1.0 §7.1, but §7.1 was not withdrawn.

**Resolution.** Both palettes ship as complete, equal presets over one token layer. No
default is imposed; the administrator selects a preset during first-run setup. §31's
acceptance criterion is satisfied whenever the Charcoal preset is active, and is restated to
say so.

**Amendment.** §7.1 and the document-control row are revised to describe Navy as a shipped
preset rather than the default. §31 is restated to test the Charcoal preset specifically.

See [ADR-0002](decisions/adr-0002-dual-theme-token-engine.md).

---

## C-03 — Delivery phases omit three modules

**Severity: critical. Amendment required.**

§13 lists ten delivery phases. It was authored for v1.0 and never revised. The modules added
in v1.1 to v1.3 appear in no phase:

- Tax & Statutory Obligations (§19), a core module per its own text
- Financial Management (§20), elevated to flagship status by §29
- Debt, Loans and Financing (§25)
- The accounting foundation of §29.2, which §13 does not mention at all

§13 also places finance at Phase 8, after POS at Phase 7. POS posts revenue, tax, tender and
cost of goods sold. Without a ledger, those postings have nowhere to go, and would require
reposting once Phase 8 arrived.

**Resolution.** The phase plan is re-cut to sixteen phases in dependency order, grouped into
six stages. The ledger precedes POS and finance; taxation precedes pricing. Every §13 phase
is preserved in substance and mapped in the [traceability matrix](10-traceability.md).

**Amendment.** §13 is replaced by the [build plan](00-build-plan.md).

See [ADR-0005](decisions/adr-0005-phase-recut.md).

---

## C-04 — Multi-business tenancy undecided

**Severity: high. Resolved; amendment required.**

§15 lists as open: "Confirm whether multi-business tenancy is enabled at first release or
initially schema-ready." §10 requires every business-owned record to be scoped to the correct
business and branch, which presupposes the scoping columns exist.

**Resolution.** Schema-ready. Every business-owned table carries `business_id`, and
branch-relevant tables carry `branch_id`, from the first migration. Services scope every
query. The interface exposes a single business. Activation in Phase 16 requires no migration.

**Amendment.** §15 bullet 7 is closed with this decision.

See [ADR-0003](decisions/adr-0003-tenancy-schema-ready.md).

---

## C-05 — Inventory valuation undecided

**Severity: high. Resolved; amendment required.**

§6.11: "Select FIFO or weighted-average valuation after accounting policy confirmation." The
Phase 5 pricing engine needs the stock cost base, and the Phase 3 ledger needs the cost-of-
sales posting basis, so the decision cannot wait for Phase 8.

**Resolution.** Moving weighted average as the configured default, over a stock ledger that
maintains cost layers. FIFO is selectable per product policy without schema change.

**Amendment.** §6.11 is closed with this decision.

See [ADR-0004](decisions/adr-0004-inventory-valuation.md).

---

## C-06 — Pricing sequence undefined

**Severity: critical. Resolved; amendment required.**

§15: "Confirm installment uplift basis and whether it applies before/after discount; make the
basis configurable." §6.3 requires the system to "clearly define the selected cost base, tax
treatment and discount sequence" but does not define it. Without a fixed order, two
quotations carrying identical inputs can produce different totals.

**Resolution.** A canonical sequence: supplier cost, cost allocation, margin, affiliate
commission, discount, instalment uplift, taxation. Every step's parameters are configurable;
the order is fixed.

**Amendment.** §6.3 is extended with the canonical sequence. §15 bullet 1 is closed.

See [ADR-0007](decisions/adr-0007-pricing-sequence.md) and the
[pricing engine](04-pricing-engine.md).

---

## C-07 — Commission basis and payout trigger undefined

**Severity: high. Resolved; amendment required.**

§15: "Confirm affiliate commission calculation basis, discount/tax treatment and payout
trigger." §6.4 requires the accrued, approved, payable and paid states but does not say what
moves a commission between them.

**Resolution.** Basis: the line's selling price excluding tax, before customer discount. A
percentage commission is grossed up so the business's margin is preserved. Accrual at
invoice; payable on receipt of the customer's payment; proportional on partial fulfilment.
Basis and trigger remain configurable.

**Amendment.** §6.4 is extended. §15 bullet 2 is closed.

See [ADR-0008](decisions/adr-0008-affiliate-commission.md).

---

## C-08 — Document rendering technology

**Severity: low. Amendment required.**

The §9 summary table specifies "Versioned HTML/CSS templates rendered to PDF". §9.2 instead
directs the selection of "tested Python libraries for PDF/document generation", and §9.7
mandates a PySide6 interface, which would imply a Qt rendering path.

**Resolution.** Versioned HTML and CSS rendered to PDF by WeasyPrint, as the §9 table states.
This is consistent with the web presentation layer of C-01 and with the version-preservation
requirement of §6.6.

**Amendment.** §9.2 is clarified to name the rendering approach.

---

## C-09 — Tax treatment of a configured charge

**Severity: medium. No conflict; clarification recorded.**

§19.6 warns that "Tax and fee configuration must be flexible and must not assume every
configured charge is legally a tax", while §19 is titled "Tax & Statutory Obligations" and
§23 names the navigation module "Tax & Statutory".

**Resolution.** No contradiction. The module accommodates taxes, duties, levies, statutory
deductions and non-tax operational fees, and each rule carries a category and an accounting
classification that determines its treatment. The interface uses "Taxation" as the
navigation label, and no rule is presumed to be a tax.

---

## C-10 — Open decisions requiring external confirmation

**Severity: varies. Open by nature.**

§15 and §24 require confirmations that cannot be settled by this project.

| Item | Blocks | Owner |
| --- | --- | --- |
| Ghana tax configuration validated against current official guidance | Any statutory filing or tax decision. Does not block Phase 4, which builds the configurable engine. | Pilot's tax adviser |
| Instalment deposits, reminders, missed-payment handling, early settlement rules | Phase 5 default configuration, not the engine | Pilot |
| POS printer models, barcode scanners, payment integrations | Phase 10 device configuration, not the register | Pilot |
| Mandatory fields for the pilot's document templates | Phase 6 default template seeding, not the designer | Pilot |
| Bank statement formats available from the pilot's banks | Phase 11 reconciliation import | Pilot |
| Code-signing certificate | Phase 1 installer distribution | Pilot |

In every case the engine is built configurable, so the confirmation sets data rather than
code. This is the explicit intent of §24.

---

## C-11 — Deployment mode of the §9 summary table

**Severity: low. Amendment required.**

The §9 summary table states "Single computer — Local application service and SQLite" and
"LAN — Host runs application service and PostgreSQL; clients use authenticated APIs, not
direct DB access", which is consistent with §9.3 and §9.4. The same table's Desktop row
contradicts §9.1, as recorded in C-01.

**Resolution.** The table is correct other than its Desktop row. That row is corrected under
C-01; the remainder stands.

---

## C-12 — Payroll scope

**Severity: low. Clarification recorded.**

Payroll is referred to conditionally throughout: "salaries/payroll if enabled" (§20.3),
"payroll/recurring expenses if enabled" (§29.1), "payroll" as a confidentiality concern
(§29.9). No payroll requirement is stated.

**Resolution.** Payroll is out of scope for Phases 1 to 16. The expense model, subledger
model and chart of accounts accommodate payroll postings without schema change, and the
confidentiality control of §29.9 is implemented for whenever payroll data exists.

---

## C-13 — Fixed assets and depreciation

**Severity: low. Clarification recorded.**

§29.2 lists journals generated from "depreciation if enabled". §20.3 and §29.4 require
capital expenditure to be distinguished from operating expenditure. No fixed-asset register
is specified.

**Resolution.** Capital expenditure is classified and posted to the asset accounts from
Phase 11. No fixed-asset register, depreciation schedule or disposal workflow is built. The
chart of accounts and journal model accommodate them.

---

## Summary

| Reference | Severity | Status |
| --- | --- | --- |
| C-01 Presentation technology | Critical | Resolved; amendment required |
| C-02 Colour system | High | Resolved; amendment required |
| C-03 Delivery phases | Critical | Resolved; amendment required |
| C-04 Tenancy | High | Resolved; amendment required |
| C-05 Inventory valuation | High | Resolved; amendment required |
| C-06 Pricing sequence | Critical | Resolved; amendment required |
| C-07 Commission basis | High | Resolved; amendment required |
| C-08 Document rendering | Low | Resolved; amendment required |
| C-09 Tax terminology | Medium | Clarified |
| C-10 External confirmations | Varies | Open by nature; assigned |
| C-11 Deployment table | Low | Resolved; amendment required |
| C-12 Payroll | Low | Clarified; out of scope |
| C-13 Fixed assets | Low | Clarified; out of scope |

Eight amendments would produce a consolidated PRD v1.5 in which no two sections disagree.

## Postscript — C-02 resolved by the business

C-02 recorded that PRD §7.1 specifies navy and §28.1 charcoal and orange, and that §7.1
was never withdrawn. The resolution was to ship both as equal presets and impose no
default, because nothing outside the document could settle a disagreement inside it.

The business has since supplied its brand mark, which settles it. A fourth preset is
built from the mark's own colours and is the one offered first; the other three remain
available and unchanged. See [ADR-0002](decisions/adr-0002-dual-theme-token-engine.md).
