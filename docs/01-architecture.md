# Architecture

Satisfies PRD §9.1 to §9.7. Governed by [ADR-0001](decisions/adr-0001-python-core-web-presentation.md)
and [ADR-0010](decisions/adr-0010-repository-structure.md).

## Position

Nickland ERP is a modular monolith. Python holds the whole of the business: domain rules,
application services, persistence, integrations, document rendering and background work.
The presentation layer is a typed component application rendered in an embedded system web
view. It renders, validates input and nothing more.

Microservices are excluded for the initial release, per PRD §9.3.

## Runtime topology

### Standalone deployment

```
┌──────────────────────────────────────────────────────────┐
│ Windows desktop process                                  │
│                                                          │
│  ┌────────────────────┐      ┌────────────────────────┐  │
│  │ WebView2 surface   │ HTTP │ Local application      │  │
│  │ React + TypeScript │◄────►│ service (FastAPI)      │  │
│  │ presentation only  │ loop-│ bound to 127.0.0.1     │  │
│  └────────────────────┘ back └───────────┬────────────┘  │
│                                          │               │
│                              ┌───────────▼────────────┐  │
│                              │ Domain and application │  │
│                              │ services (Python)      │  │
│                              └───────────┬────────────┘  │
│                                          │               │
│         ┌────────────────┬───────────────┼─────────────┐ │
│         ▼                ▼               ▼             ▼ │
│  ┌───────────┐  ┌──────────────┐ ┌────────────┐ ┌──────┐ │
│  │ SQLite    │  │ Attachment   │ │ Background │ │ Prin-│ │
│  │ database  │  │ store        │ │ workers    │ │ ters │ │
│  └───────────┘  └──────────────┘ └────────────┘ └──────┘ │
└──────────────────────────────────────────────────────────┘
```

The service binds to the loopback interface on an ephemeral port and accepts only a
session token issued by the host process at start-up. No port is exposed to the network.

### LAN deployment

```
   Client workstation                    Host computer
┌────────────────────────┐         ┌──────────────────────────────┐
│ WebView2 + React       │         │ Application service (FastAPI)│
│                        │  HTTPS  │ Domain and application       │
│ Thin client shell      │◄───────►│ services — identical code    │
│ (no database access)   │  + auth │                              │
└────────────────────────┘         │  ┌────────────┐ ┌──────────┐ │
                                   │  │ PostgreSQL │ │ Attach-  │ │
   Further workstations ──────────►│  │ 16         │ │ ment     │ │
   POS terminals ─────────────────►│  └────────────┘ │ store    │ │
                                   │  ┌────────────┐ └──────────┘ │
                                   │  │ Background │              │
                                   │  │ workers    │              │
                                   │  └────────────┘              │
                                   └──────────────────────────────┘
```

Clients never hold a database connection, per PRD §9.3. The sole difference between the two
deployments is the database engine, the transport and the authentication mechanism. Domain
code is identical. A SQLite file on a shared network drive is never used as a multi-user
database, per PRD §9.2.

## Layers

| Layer | Responsibility | Permitted dependencies |
| --- | --- | --- |
| Presentation | Rendering, input capture, client-side validation, navigation, local view state | The HTTP contract only |
| Interface | HTTP routing, request and response schemas, authentication, session handling, error translation | Application |
| Application | Use-case orchestration, transaction boundaries, permission enforcement, audit emission, event dispatch | Domain, Persistence |
| Domain | Business rules, invariants, calculations, state machines, value objects | Nothing outside the domain |
| Persistence | Repositories, ORM mapping, migrations, query construction | Domain |
| Integration | Printers, barcode devices, payment adapters, mail, file formats | Domain |
| Platform | Configuration, logging, security primitives, clock, identifiers, background scheduling | Nothing |

### Enforced constraints

1. The domain layer imports no framework. It has no knowledge of SQLAlchemy, FastAPI or HTTP.
2. The presentation layer holds no business rule. Pricing, tax, commission, allocation,
   scheduling and posting are computed by the service and transmitted as results.
3. Permission checks live in the application layer. The interface layer additionally rejects
   unauthenticated requests; the presentation layer merely hides controls, which is a courtesy
   and never a control.
4. Transaction boundaries are opened in the application layer and nowhere else.
5. Audit events are emitted by the application layer within the same transaction as the change.
6. A repository returns domain objects. ORM entities do not escape the persistence layer.

## Module boundaries

| Module | Owns | Publishes |
| --- | --- | --- |
| `identity` | Users, roles, permissions, sessions | Permission registry, current-actor resolution |
| `organisation` | Business, branches, warehouses, settings, numbering, custom-field definitions | Scoping context, sequence allocation |
| `catalogue` | Products, services, categories, attributes, units, barcodes, price lists | Item resolution, pricing lookup |
| `parties` | Customers, suppliers, affiliates, contacts, terms, credit limits | Party resolution, credit status |
| `ledger` | Chart of accounts, journals, periods, subledgers | Posting service, balance queries, period state |
| `taxation` | Tax rules, applicability, calculation, obligations, filings | Tax evaluation service, obligation accrual |
| `pricing` | Cost build-up, allocation, margin, commission, financing uplift | Quotation pricing service |
| `sales` | RFQ, quotations, sales orders, invoices, returns | Order state, revenue events |
| `procurement` | Supplier RFQ, quotations, purchase orders, supplier invoices | Procurement state, payable events |
| `inventory` | Stock ledger, batches, serials, valuation, counts, assembly | Availability, reservation, cost-of-sales events |
| `dispatch` | Picking, packing, waybills, delivery, proof of receipt | Fulfilment state |
| `pos` | Register, cart, tender, shifts, reversals | Sale completion events |
| `finance` | Cash and bank, expenses, receivables, payables, reconciliation | Cash position, ageing, allocation |
| `financing` | Debt registers, loan agreements, schedules, repayments | Debt exposure, schedule state |
| `documents` | Templates, designer model, rendering, numbering, snapshots, attachments | Render service, document store |
| `analytics` | Budgets, forecasts, profitability, reports, management pack | Report service |
| `platform` | Configuration, logging, clock, identifiers, backup, health, scheduling | Infrastructure primitives |

### Dependency rule

A module depends only on modules below it in the following order. Cycles are rejected by an
automated import check in continuous integration.

```
platform → identity → organisation → catalogue, parties → ledger → taxation → pricing
  → sales, procurement → inventory → dispatch, pos → finance → financing → documents → analytics
```

Where a lower module requires a reaction from a higher one, it emits a domain event. It never
imports upward.

## Cross-module communication

Financial and stock consequence is propagated by domain events handled within the same
transaction as the originating change.

| Event | Emitted by | Consumed by |
| --- | --- | --- |
| `SaleCompleted` | `sales`, `pos` | `ledger`, `inventory`, `taxation`, `finance` |
| `GoodsReceived` | `procurement` | `inventory`, `ledger`, `taxation` |
| `StockIssued` | `inventory` | `ledger` |
| `PaymentReceived` | `finance`, `pos` | `ledger`, `financing`, `pricing` |
| `PaymentMade` | `finance` | `ledger`, `taxation`, `financing` |
| `TaxAccrued` | `taxation` | `ledger`, `finance` |
| `CommissionAccrued` | `pricing` | `ledger`, `finance` |
| `InstalmentDue` | `financing` | `finance` |
| `DocumentIssued` | `documents` | `sales`, `procurement`, `dispatch` |

Handlers are synchronous and transactional. Any handler failure aborts the originating
operation entirely, per PRD §11 reliability.

## Transaction and idempotency policy

| Operation | Guarantee |
| --- | --- |
| Sale completion | Single transaction spanning sale, tender, stock movement, tax accrual and journal entries |
| Goods receipt | Single transaction spanning GRN, inspection outcome, stock movement, landed cost and journal entries |
| Payment allocation | Single transaction spanning receipt, allocation, subledger and journal entries |
| Loan repayment | Single transaction spanning repayment, allocation, schedule state and journal entries |
| Journal posting | Single transaction; balance asserted before commit |
| Period close | Single transaction; all reconciliation gates asserted before commit |

Every externally triggered financial operation carries an idempotency key. A repeated key
returns the original result and creates nothing. This covers retries, duplicated payment
callbacks and any future synchronisation queue, per PRD §29.9.

## Background work

Workers run in-process in standalone mode and as a supervised service in LAN mode. The same
job definitions serve both.

| Job | Cadence |
| --- | --- |
| Database and attachment backup | Configurable schedule |
| Tax obligation accrual and due-date evaluation | Daily |
| Instalment and loan schedule evaluation | Daily |
| Ageing recalculation | Daily |
| Scheduled report generation and delivery | Configurable schedule |
| Import and export processing | On demand |
| Health probes | Continuous |

Long-running work never blocks the interface. Progress, cancellation and failure are reported
to the presentation layer, per PRD §9.7.

## Offline posture

| Capability | Standalone, no network |
| --- | --- |
| Sales, quotation, procurement, inventory, dispatch | Fully available |
| POS cash and manual tender | Fully available |
| Document design, rendering, printing | Fully available |
| Ledger posting, reconciliation, reporting, charts | Fully available |
| Backup to local storage | Fully available |
| Electronic payment confirmation | Unavailable; tender remains pending |
| Cloud backup, online update, external integration | Unavailable; state reported as unavailable, never as successful |

An initiated external request is never reported as a completed one, per PRD §9.4.

## Health reporting

The application reports the state of the local service, the database connection, the network
interface, storage capacity, the backup position and the worker queue depth, per PRD §9.4.

## Technology selections

| Concern | Selection | Justification |
| --- | --- | --- |
| Language | Python 3.12 | PRD §9.2 |
| Service framework | FastAPI | PRD §9.2; serves both the local loopback service and the LAN host |
| Persistence | SQLAlchemy 2.x | PRD §9.2; engine-independent, supports both target databases |
| Migrations | Alembic | PRD §9.2; versioned, reversible |
| Contracts | Pydantic v2 | PRD §9.2; validation at every service boundary |
| Standalone database | SQLite | PRD §9.2 |
| Shared database | PostgreSQL 16 | PRD §9.2; concurrency controls required by §9.5 |
| Desktop container | pywebview over WebView2 | Pure Python host, uses the Windows system web view, no bundled browser, no Tauri |
| Presentation | React 18, TypeScript, Vite | ADR-0001 |
| Data grid | TanStack Table | Headless; styling governed by our own tokens |
| Charts | ECharts | Offline, accessible, exportable, dense financial chart types |
| Document rendering | WeasyPrint | HTML and CSS to PDF; permissive licence; matches the versioned template model of PRD §9.9 |
| Password hashing | Argon2id | PRD §9.6 |
| Spreadsheet exchange | openpyxl | PRD §9.2 |
| Barcode | python-barcode, treepoem where required | PRD §9.2 |
| Packaging | PyInstaller with a signed installer | PRD §9.2 |

Versions are pinned at project establishment and recorded in the lock file. No dependency is
adopted without a licence review, per PRD §9.2.

## Repository structure

```
nickland-erp/
├─ src/nickland/
│  ├─ platform/          configuration, logging, clock, identifiers, backup, health
│  ├─ domain/            pure business rules, one package per module
│  ├─ application/       use-case services, transaction boundaries, permissions, audit
│  ├─ persistence/       ORM mapping, repositories, Alembic migrations
│  ├─ integration/       printers, scanners, payment adapters, mail, file formats
│  ├─ interface/         FastAPI routers, schemas, authentication
│  ├─ documents/         template model, designer services, rendering
│  └─ desktop/           pywebview host, process lifecycle, single-instance control
├─ ui/                   React and TypeScript presentation layer
│  ├─ src/tokens/        generated design tokens — never hand-edited
│  ├─ src/components/    component library
│  ├─ src/modules/       one directory per business module
│  └─ src/api/           generated client from the OpenAPI schema
├─ tests/
│  ├─ unit/              domain calculations
│  ├─ integration/       services against SQLite and PostgreSQL
│  ├─ e2e/               end-to-end workflows
│  └─ concurrency/       LAN multi-user scenarios
├─ docs/                 this planning set
├─ packaging/            installer definition, signing, update manifest
└─ tools/                token generation, chart seeding, import templates
```

The API client and the design tokens are generated artefacts. Neither is edited by hand.

## Open technical confirmations

| Item | Required before | Owner |
| --- | --- | --- |
| Receipt printer models and command sets | Phase 10 | Pilot |
| Barcode scanner models and interface mode | Phase 10 | Pilot |
| Bank statement file formats available from the pilot's banks | Phase 11 | Pilot |
| Code-signing certificate provisioning | Phase 1 installer | Pilot |
| Ghana tax configuration validation against current official guidance | Before any statutory filing | Pilot's tax adviser |
