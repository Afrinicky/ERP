# ADR-0006 — Single base currency with a multi-currency-ready schema

Status: accepted · Date: 5 November 2026

## Context

The pilot operates in Ghana and transacts in Ghana cedi. PRD §29.7 requires support for
"multi-currency balances with original currency, exchange rate, base-currency value and
exchange differences where enabled". PRD §6.3 requires supplier cost to capture a currency.
Suppliers of medical and laboratory equipment frequently invoice in United States dollars or
euro.

## Decision

Every monetary column is accompanied by `currency_code`, `exchange_rate` and a base-currency
value, from the first migration. Only the base currency is configured at release.
Foreign-currency transacting, revaluation and exchange differences are activated by
configuration.

## Rationale

The columns cost little now. Retrofitting them means migrating every monetary column in the
system, on a live dataset carrying posted financial records, and then auditing every
calculation and report for the base-currency distinction. The §29.7 requirement makes that
retrofit a certainty rather than a risk.

Full multi-currency operation at release is a different proposition: rate tables and their
sourcing, revaluation at period end, realised and unrealised exchange differences, and
multi-currency presentation in every report and statement. For a pilot that currently
transacts in one currency, that is scope without present return.

## Implementation

1. Every monetary column carries `currency_code`, `exchange_rate` and a base-currency value.
   Where currency equals the base currency, the rate is 1 and the two amounts agree.
2. All accounting is conducted and reported in the base currency. Original currency and rate
   are retained for reference and reconciliation.
3. The chart of accounts includes `71200 Exchange differences` from Phase 3, unused until
   activation.
4. Exchange rates are stored effective-dated, entered manually. No external rate source is
   integrated.
5. Activation enables: supplier invoicing in a foreign currency, period-end revaluation of
   foreign-currency balances, realised differences on settlement, unrealised differences on
   revaluation, and multi-currency presentation.
6. Reports state their currency explicitly, per §29.8, from Phase 3 onward.

## Consequences

Three columns per monetary figure that the first release does not exercise, and the storage
and index cost that implies. Calculations carry a base-currency conversion that is the
identity at release. In exchange, the first foreign supplier invoice is a configuration
exercise rather than a migration.

## Amendment required

None. This implements §29.7's "where enabled" as written.
