# Nickland ERP — Planning Documentation

Requirements baseline: Product Requirements Document v1.4, 5 November 2026.
Planning status: approved. Implementation has not commenced.

## Reading order

New contributors should read in the following sequence.

1. [Build plan](00-build-plan.md) — what is delivered, in which order, and the exit condition of each phase.
2. [Architecture](01-architecture.md) — runtime topology, layer responsibilities and module boundaries.
3. [Data model](02-data-model.md) — entity groups, naming conventions, monetary precision and integrity rules.
4. [Design system](03-design-system.md) — design tokens, the two shipped themes, component inventory and interface standards.

## Domain specifications

| Document | Governs |
| --- | --- |
| [Pricing engine](04-pricing-engine.md) | Landed cost, allocation, margin, affiliate commission, instalment financing, discount and tax sequencing |
| [Accounting model](05-accounting-model.md) | Chart of accounts, double-entry posting rules, subledgers, period control and statement production |
| [Document designer](06-document-designer.md) | Template data model, visual editor, field palette, rendering, numbering and version preservation |
| [Security and audit](07-security-and-audit.md) | Authentication, role-based authorisation, approval thresholds, immutable audit and data protection |
| [Quality strategy](08-quality-strategy.md) | Test obligations per phase, environments, concurrency testing and release engineering |

## Governance

| Document | Purpose |
| --- | --- |
| [Requirements conflict register](09-conflict-register.md) | Every contradiction identified within the PRD, with the ratified resolution and the amendment required |
| [Traceability matrix](10-traceability.md) | Mapping from each PRD section and acceptance criterion to the phase that satisfies it |
| [Decision records](decisions/) | Ten ratified decisions, each with context, options considered and consequences |

## Module specifications

Per-module specifications are produced at the commencement of the phase that delivers the
module, and are filed in [`modules/`](modules/). The [build plan](00-build-plan.md) states
which module specifications each phase requires.

## Ratified decisions

| Reference | Decision |
| --- | --- |
| [ADR-0001](decisions/adr-0001-python-core-web-presentation.md) | Python core with an embedded web presentation layer |
| [ADR-0002](decisions/adr-0002-dual-theme-token-engine.md) | Token-driven theming with two shipped presets and no imposed default |
| [ADR-0003](decisions/adr-0003-tenancy-schema-ready.md) | Tenancy carried in the schema; a single business exposed in the interface |
| [ADR-0004](decisions/adr-0004-inventory-valuation.md) | Moving weighted average valuation over a layer-capable stock ledger |
| [ADR-0005](decisions/adr-0005-phase-recut.md) | Delivery phases re-cut to dependency order |
| [ADR-0006](decisions/adr-0006-currency.md) | Single base currency with a multi-currency-ready schema |
| [ADR-0007](decisions/adr-0007-pricing-sequence.md) | Canonical pricing calculation sequence |
| [ADR-0008](decisions/adr-0008-affiliate-commission.md) | Affiliate commission basis and payout trigger |
| [ADR-0009](decisions/adr-0009-pos-payment-confirmation.md) | Electronic tender confirmation discipline at the point of sale |
| [ADR-0010](decisions/adr-0010-repository-structure.md) | Repository and package structure |
