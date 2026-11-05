# ADR-0003 — Tenancy carried in the schema; a single business exposed

Status: accepted · Date: 5 November 2026

## Context

PRD §15 leaves open "whether multi-business tenancy is enabled at first release or initially
schema-ready". PRD §10 requires every business-owned record to be scoped to the correct
business and branch, which presupposes the scoping columns exist. PRD §9.3 requires a
tenant-aware data model for cloud readiness. The initial deployment is a single enterprise,
on one site.

## Decision

Every business-owned table carries `business_id` from the first migration. Branch-relevant
tables additionally carry `branch_id`. Application services derive both from the actor's
scoping context and apply them to every query without exception. The interface exposes a
single business.

Multi-business operation is activated by configuration in Phase 16 and requires no migration.

## Rationale

The cost of the columns now is one column and one index per table, plus a scoping context
that services apply uniformly. The cost of adding them later is a migration across every
business-owned table, a backfill, and an audit of every query in the system — on a live
dataset carrying posted financial records.

Full multi-business operation at release is a different matter: business switching,
per-business settings, templates, numbering, permissions and consolidated reporting is
substantial scope and test surface, for a pilot that has one business.

Scoping also serves §10 directly, independent of tenancy. A record outside the actor's scope
is not returned and is not reported as existing.

## Implementation

1. `business_id` is non-nullable on every business-owned table. `branch_id` is present and
   nullable where a record may be business-wide.
2. The scoping context is resolved once per request from the authenticated actor and passed
   to every service call. It is not derived from a request parameter.
3. A base repository applies the scope. A query that bypasses it fails an architectural test.
4. Unique constraints are scoped: document numbers per business, branch and type; SKU and
   barcode per business; account code per business.
5. Attachments, templates, settings, numbering sequences and theme profiles are all scoped.
6. First-run setup creates exactly one business. The interface offers no business switcher.
7. Phase 16 enables multi-business operation by configuration, adding the switcher, the
   per-business administration surface and consolidated reporting.

## Consequences

Every table carries a column and an index the first release does not strictly need. Every
query carries a predicate. In exchange, the §9.3 cloud-readiness requirement is satisfied by
construction, and §10's scoping requirement is enforced structurally rather than by
convention.

## Amendment required

PRD §15 bullet 7 is closed with this decision.
