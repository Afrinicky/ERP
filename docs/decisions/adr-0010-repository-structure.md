# ADR-0010 — Repository and package structure

Status: accepted · Date: 5 November 2026

## Context

PRD §9.3 requires a modular monolith with clear separation between interface, application,
domain and persistence, and requires that business rules not reside in interface event
handlers or database-specific SQL. PRD §9.3 further requires stable module boundaries so that
future clients can use the API without replacing the Python core.

A structure that merely documents these boundaries will not hold them. The structure must make
a violation fail a build.

## Decision

A single repository. A single Python distribution, `nickland`, layered by responsibility and
subdivided by business module. The presentation layer is a sibling directory.

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
├─ ui/
│  ├─ src/tokens/        generated — never hand-edited
│  ├─ src/components/    component library
│  ├─ src/modules/       one directory per business module
│  └─ src/api/           generated client from the OpenAPI schema
├─ tests/{unit,integration,e2e,concurrency}/
├─ docs/
├─ packaging/
└─ tools/
```

Within `domain/` and `application/`, packages follow the seventeen business modules of the
[architecture](../01-architecture.md).

## Enforced rules

Each is an automated check in continuous integration, not a convention.

| Rule | Check |
| --- | --- |
| `domain/` imports no framework | Import scan rejects `sqlalchemy`, `fastapi`, `pydantic` and any HTTP library |
| Module dependencies follow the stated order | Import graph scan rejects any edge against the order, and any cycle |
| ORM entities do not escape persistence | Type scan of application and interface signatures |
| Transactions open only in `application/` | Call-site scan for the session context manager |
| No monetary calculation in `ui/` | Scan for arithmetic on amount-typed values in the frontend |
| No colour, font, spacing or radius literal outside the token layer | Token lint, per §9.7 |
| No string-composed SQL | Scan for SQL string concatenation and interpolation |
| Generated artefacts are not hand-edited | Build regenerates and fails on a diff |
| No committed credential | Secret scan |

## Generated artefacts

Two artefacts are generated and never edited by hand:

1. `ui/src/api/` — the typed client, generated from the OpenAPI schema. A contract change
   therefore cannot silently diverge from its consumer.
2. `ui/src/tokens/` — CSS custom properties and TypeScript types, generated from
   `tokens/*.json`.

## Single repository

The business layer and the presentation layer change together on nearly every feature. Two
repositories would mean a paired pull request for most work, and a version skew between a
contract and its consumer. One repository makes a contract change and its consumer change
atomic.

## Consequences

A contributor must observe the layering. The automated checks make a violation visible at the
point it is introduced, rather than at review or after release.

The structure is larger than a small application needs. It is appropriate to seventeen
business modules across sixteen phases, and it is the structure §9.3 requires.

## Amendment required

None. This implements §9.3 as written.
