# Nickland ERP

Offline-first business management platform for procurement, supplier sourcing, sales,
point-of-sale transactions, inventory, fulfilment, documents, taxation and financial records.

Prepared for Nicholas Nickland Enterprise. Requirements baseline: Product Requirements
Document v1.4, 5 November 2026.

## Status

Planning. No application code has been written. The present repository contains the
approved architecture, data model, design system, phase plan and decision record.

## Documentation

| Document | Purpose |
| --- | --- |
| [Documentation index](docs/README.md) | Entry point to the full planning set |
| [Build plan](docs/00-build-plan.md) | Stages, phases, dependencies and exit criteria |
| [Architecture](docs/01-architecture.md) | Runtime topology, layering and module boundaries |
| [Data model](docs/02-data-model.md) | Entity groups, conventions and integrity rules |
| [Design system](docs/03-design-system.md) | Tokens, themes, components and interface standards |
| [Pricing engine](docs/04-pricing-engine.md) | Cost build-up, margin, commission, financing and tax |
| [Accounting model](docs/05-accounting-model.md) | Chart of accounts, posting rules and period control |
| [Document designer](docs/06-document-designer.md) | Template model, editor, rendering and versioning |
| [Security and audit](docs/07-security-and-audit.md) | Authentication, authorisation, approvals and audit |
| [Quality strategy](docs/08-quality-strategy.md) | Test obligations, environments and release engineering |
| [Requirements conflict register](docs/09-conflict-register.md) | Contradictions in the PRD and their resolutions |
| [Traceability matrix](docs/10-traceability.md) | PRD section to phase and acceptance criteria mapping |
| [Decision records](docs/decisions/) | Ten ratified architectural decisions |

## Confirmed technology direction

Python is the primary implementation language for business logic, domain services,
persistence, integrations, background work and the local service layer. The presentation
layer is a typed component application rendered in an embedded system web view. No
business rule is implemented in the presentation layer.

| Concern | Selection |
| --- | --- |
| Language | Python 3.12 |
| Application service | FastAPI |
| Persistence | SQLAlchemy 2.x, Alembic migrations |
| Contracts | Pydantic v2 |
| Standalone database | SQLite |
| Shared database | PostgreSQL 16 |
| Desktop container | pywebview over Microsoft Edge WebView2 |
| Presentation layer | React 18 with TypeScript, built by Vite |
| Packaging | PyInstaller, signed Windows installer |
