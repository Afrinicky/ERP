# Contributing

## Establishing a working copy

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev,desktop,postgres]'
(cd ui && npm install)
```

Apply the migrations and start the service:

```bash
.venv/bin/alembic upgrade head
```

```bash
.venv/bin/python -m nickland.interface --port 8099
```

```bash
cd ui && npm run dev
```

The interface then runs at `http://127.0.0.1:5174` and proxies the API.

To run the desktop application as a user would:

```bash
.venv/bin/nickland
```

## Before every commit

```bash
.venv/bin/ruff check src tests --fix && .venv/bin/ruff format src tests
```

```bash
.venv/bin/mypy
```

```bash
.venv/bin/python tools/generate_tokens.py --check
```

```bash
.venv/bin/python -m pytest
```

```bash
cd ui && npm run typecheck && npm run build
```

## Testing against PostgreSQL

PRD 9.8 makes PostgreSQL tests mandatory before the LAN release, and the suite runs
against both engines so behavioural differences surface.

```bash
docker run --rm -d -p 5433:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=nickland_test --name nickland-pg postgres:16
```

```bash
NICKLAND_TEST_POSTGRES_URL=postgresql+psycopg://postgres:postgres@localhost:5433/nickland_test .venv/bin/python -m pytest
```

## Standing obligations

These are enforced by `tests/architecture/`, not by review.

1. The domain layer imports no framework.
2. A module imports only from modules below it in the dependency order.
3. Monetary arithmetic uses fixed-precision decimal; `float()` does not appear in the
   domain, application or persistence layers.
4. No SQL is composed from a formatted string.
5. The presentation layer performs no monetary arithmetic.
6. No colour, font, spacing or radius literal appears outside the token layer.
7. Every endpoint the interface calls exists in the published schema, and every
   published endpoint has a caller.

## Design tokens

`tokens/*.json` is the single source. `ui/src/tokens/` is generated and never edited by
hand:

```bash
.venv/bin/python tools/generate_tokens.py
```

The generator runs the contrast audit and refuses to emit a palette that fails WCAG AA,
so an inaccessible theme cannot reach the interface.

## Adding a permission

1. Register it in `src/nickland/domain/permissions.py`, with its group and whether it is
   sensitive.
2. Grant it to the roles that should hold it.
3. Enforce it in the application service with `actor.require(...)`, never only by hiding
   an interface control.
4. Add a test asserting it is denied at the service layer.

## Commit discipline

- Business rules live in domain and application services, never in the presentation
  layer or in database-specific SQL.
- Posted financial and stock records are never deleted or overwritten; correction is by
  reversal.
- An initiated external request is never reported as a completed one.
- Estimated, forecast, budgeted and actual values are never conflated.
