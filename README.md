# Nickland ERP

Offline-first business management platform for procurement, supplier sourcing, sales,
point-of-sale transactions, inventory, fulfilment, documents, taxation and financial records.

Prepared for Nicholas Nickland Enterprise. Requirements baseline: Product Requirements
Document v1.4, 5 November 2026.

## Status

Phases 1 to 15 of the [build plan](docs/00-build-plan.md) are delivered: the standalone
desktop edition and the LAN edition, from master data, quotations, procurement, inventory,
dispatch and the point of sale through the general ledger, financial management, financing,
planning, reporting and period close. Each phase has a verifier in `tools/` that exercises
its exit criteria against a live database. Phase 16 (cloud readiness) is next.

## Installing

Windows installers are built by GitHub Actions. Open **Actions → Build Windows Installer**,
run it (or push to `main`), and download the `Nickland_ERP_Windows_Installer` artifact:
`NicklandERP-Setup-<version>.exe`. A tag such as `v0.2.0` also publishes the installer as
a GitHub release. See [Packaging](docs/packaging.md) for what the installer contains and how
a build is checked, and the [LAN edition](docs/modules/lan.md) for setting up a host.

## Running from source

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev,desktop,postgres]'
(cd ui && npm ci && npm run build)
.venv/bin/nickland
```

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
| Language | Python 3.12 or later (3.13 in the installer) |
| Application service | FastAPI |
| Persistence | SQLAlchemy 2.x, Alembic migrations |
| Contracts | Pydantic v2 |
| Standalone database | SQLite |
| Shared database | PostgreSQL 16 |
| Desktop container | pywebview over Microsoft Edge WebView2 |
| Presentation layer | React 18 with TypeScript, built by Vite |
| Packaging | PyInstaller and Inno Setup, built on GitHub Actions |
