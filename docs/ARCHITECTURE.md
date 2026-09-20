# Architecture

> Sections marked **(planned)** describe the target design and are not implemented yet.
> What exists today is listed under [Current state](#current-state).

## Overview

A modular monolith: one FastAPI backend, one React single-page app, one database.

```
React + Vite + TypeScript  ──HTTP/JSON──►  FastAPI  ──►  Service layer  ──►  Database
   (browser)                               routers        business rules       SQLite (MVP)
                                                          + transactions       PostgreSQL (later)
```

Why a monolith: one deployable, one database transaction per business action (essential for correct
stock), and far less to learn and operate than microservices. Module boundaries inside the backend keep
it possible to split later if ever needed.

## Frontend / backend separation

- The frontend is a static single-page app. It contains **no business rules**: it collects input,
  calls the API, and displays results. Anything that affects stock, money or profit is decided by the backend.
- All HTTP goes through `frontend/src/api/`. React components never call `fetch` directly.
- In development the Vite dev server proxies `/api` and `/health` to the backend, so there is no CORS
  friction. In production the API origin is set with `VITE_API_BASE_URL` and allowed in the backend's
  `KIRANA_CORS_ORIGINS`.
- The UI is mobile-first with large tap targets. Text lives in typed locale files (English, Hindi), so
  no user-facing string is hard-coded in components, and a missing translation fails the TypeScript build.
- Money, quantities and dates are formatted only at the edge (`en-IN`, DD/MM/YYYY, ₹). The API exchanges
  exact values, never pre-formatted strings.

## Backend layers

```
backend/
├── app/
│   ├── main.py            application factory and entry point
│   ├── seed.py            development seed (one shop + owner); refuses to run in production
│   ├── core/              settings and other cross-cutting setup
│   ├── db/                engine, sessions/transactions, Money/Quantity/UTC types
│   ├── models/            SQLAlchemy models, one module per area
│   ├── api/
│   │   ├── routes/        operational routes (health)
│   │   └── v1/            versioned business routers, mounted at /api/v1
│   └── services/          business rules and transactions
├── migrations/            Alembic environment and versions
└── tests/
```

| Layer | Responsibility | Must not |
|---|---|---|
| Routers (`api/`) | Parse and validate the request, call a service, shape the response | Contain business rules or touch the database |
| Services (`services/`) | Enforce rules, run one write transaction per action | Import from `api/`; call `commit()` themselves |
| Models (`models/`) | Table definitions, constraints, column types | Contain workflows; import services or `api/` |
| DB (`db/`) | Engine, SQLite settings, transactions, column types | Know about models or business rules |
| Reporting (planned) | Read-only queries | Write anything |

These import rules are enforced by `tests/test_architecture.py`.

## Service layer and boundaries (planned)

| Service | Responsibility |
|---|---|
| `inventory_service` | **The only writer of the stock ledger.** Opening stock, purchases, sales, returns, adjustments, reversals, and the "stock cannot go negative" check |
| `khata_service` | **The only writer of the customer ledger** |
| `purchase_service` | Purchases and purchase returns |
| `detailed_sale_service` | Product-wise bills and sales returns |
| `quick_sale_service` | Money-only sales. It has **no dependency on `inventory_service`**, by design |
| `costing_service` | Moving weighted average cost, cost snapshots for COGS |
| `numbering_service` | Document numbers from a sequence table (portable across databases) |
| `export_service` | CSV/XLSX generation with formula-injection protection |

Other services call `inventory_service` and `khata_service`; nothing else writes their tables. This gives
one place to test and reason about stock and balances.

## Inventory ledger

The table `inventory_transactions` exists (Phase 2); `inventory_service` (Phase 3) will fill it.

Stock is **never stored as an editable number**. Every stock movement is a row in the insert-only
`inventory_transactions` table with a signed quantity, and current stock is the sum for a product:

```
Current Stock = Opening + Purchases - Sales + Sales Returns - Purchase Returns ± Adjustments
              = SUM(inventory_transactions.qty_delta)
```

- Rows are only inserted. A database trigger aborts any `UPDATE` or `DELETE`. Corrections are new rows
  (returns, adjustments, reversals), so the history is complete and auditable.
- The database also enforces that each type has the right sign, that adjustments carry a reason code, and
  that a row is reversed at most once.
- Only Detailed Sales touch the ledger. Quick Sales never do: the `quick_sales` table has no product columns
  at all (see [BUSINESS_RULES.md](BUSINESS_RULES.md)).
- **One writer.** `inventory_service` is the only module allowed to reference the table; a test scans the
  source and fails if any other module does (the future read-only `reporting` package is allowed to read it).
- The stock check and the write happen in one write transaction. On SQLite that transaction is
  `BEGIN IMMEDIATE`; on PostgreSQL, product rows are locked with `SELECT ... FOR UPDATE`.

## Customer ledger

The table `customer_ledger` exists (Phase 2); `khata_service` (Phase 6) will fill it.

Khata works the same way: an insert-only ledger with signed amounts, protected by the same trigger. A
customer's outstanding balance is the sum of their entries. Entry types: opening balance, credit sale,
payment, return credit, adjustment, reversal. `khata_service` is the only module allowed to reference the
table (same source-scanning test).

## Reporting layer (planned)

`reporting/` is a read-only package of query functions (top products, low stock, sales by mode, profit
estimate, ...). The dashboard, the reports screens and exports all call the same functions, so a number
is computed in exactly one place. It never writes, and it is where the rules about not mixing Quick Sales
into product analytics or profit are enforced.

**Future AI assistant:** these same functions become the assistant's tools ("which products are low?",
"what was my estimated profit?"). The assistant calls vetted functions; it never generates SQL against shop data.

## Multi-shop SaaS readiness

Not built in the MVP, but the foundations are laid now because they are expensive to retrofit:

- Every business table carries `shop_id`, and **references between shop-owned tables are composite foreign
  keys `(shop_id, x_id)`**. The database refuses a row in Shop A that points at a row in Shop B, so isolation
  does not depend on every query remembering a `WHERE shop_id = ...`. Queries must still filter by shop.
- The service layer will receive the current shop from a single dependency. Until authentication (Phase 14)
  it is a fixed development shop, added in Phase 3 together with the first routes that need it.
- Users belong to a shop and have a role (owner/staff).
- No shop data in global state, files or caches without a shop key.
- Later: PostgreSQL Row-Level Security as defence in depth, subscription plans and usage limits, an admin
  panel, WhatsApp notifications, scheduled reports and cloud backup.

## Future: online ordering (not built)

A customer storefront, cart, online orders (COD/UPI), delivery address and status, and order history are
future work. The rule for it is **reuse, never duplicate**: it uses the same `products`, `customers`,
pricing and inventory. There is no second inventory system; an accepted order becomes a normal Detailed
Sale created through `detailed_sale_service`, which posts stock through `inventory_service`. Details are in
[DATABASE.md](DATABASE.md#future-online-ordering).

## Database strategy

SQLite for the MVP (zero setup, one file), PostgreSQL when hosting many shops. The database is chosen only by
`KIRANA_DATABASE_URL`, and the code avoids dialect-specific SQL. Sessions and transactions are in
`app/db/session.py`: reads use a plain transaction, and every change runs in one `write_transaction()`.
The full design, including why SQLite is safe for the ledger logic and what is still unverified on
PostgreSQL, is in [DATABASE.md](DATABASE.md).

## Configuration and security

- Configuration comes from environment variables only (`KIRANA_*` for the backend, `VITE_*` for the
  frontend). `.env` files are git-ignored; `.env.example` files document every variable.
- Interactive API docs are disabled when `KIRANA_ENVIRONMENT=production`.
- CORS allows only the configured origins.
- Later phases add: argon2 password hashing, short-lived tokens, login rate limiting, audit logging,
  idempotency keys for create endpoints, and per-shop authorization checks.

## Current state

**Phase 1 (foundation)**
- Backend: application factory, settings, `GET /health`, an empty `/api/v1` router.
- Frontend: routing, responsive shell (sidebar drawer on mobile), dashboard placeholder, "coming soon"
  pages for planned modules, English/Hindi switching, API client, and a live server-status badge.

**Phase 2 (database foundation)**
- SQLite engine (foreign keys, WAL, busy timeout, explicit transactions), `read_session` /
  `write_transaction`, and the `Money`, `Quantity` and `UTCDateTime` types.
- 23 tables (see [DATABASE.md](DATABASE.md)), created by Alembic migration `0001`, with the shared units
  seeded and the insert-only triggers installed.
- A development seed script.
- **No services, endpoints or screens use the database yet.** `inventory_service` and `khata_service` are
  documented placeholders.

## Testing strategy

- Database tests run on SQLite files created by the **real Alembic migration** (not `create_all`), so they
  test the schema that ships. The migration runs once per session; each test gets a private copy.
- They cover: connection and SQLite settings, write-transaction commit/rollback and the write lock,
  `Money`/`Quantity` exactness, migration up/down and model-versus-migration drift (`alembic check`),
  PostgreSQL DDL rendering, uniqueness, foreign keys, cross-shop references, value validity, the ledger rules
  and insert-only triggers, seed data, and the architecture rules.
- From Phase 3, each service rule gets tests, plus invariant tests (ledger sum equals reported stock across
  random sequences) and a concurrency test for overselling.
- Frontend: TypeScript strict checks, oxlint, and a production build on every change.
- PostgreSQL: the same suite runs on PostgreSQL at the checkpoint after Phase 10 and again in Phase 15.

