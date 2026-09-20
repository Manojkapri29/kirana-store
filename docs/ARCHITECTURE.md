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
  The form validation in the UI (required fields, decimal places, whole numbers for pieces) is only quick
  feedback; the server checks everything again and its messages are shown when they differ.
- All HTTP goes through `frontend/src/api/`. React components never call `fetch` directly. Server state
  (lists, details, caching, refetching after a change) is handled by TanStack Query.
- **Money and quantities are JSON strings** (`"25.50"`, `"2.500"`) in both directions, and the frontend never
  turns them into JavaScript numbers except to display them. The API refuses a JSON number with a fraction.
- In development the Vite dev server proxies `/api` and `/health` to the backend, so there is no CORS
  friction. In production the API origin is set with `VITE_API_BASE_URL` and allowed in the backend's
  `KIRANA_CORS_ORIGINS`.
- The UI is mobile-first with large tap targets: tables on desktop, cards on phones. Text lives in typed
  locale files (English, Hindi), so no user-facing string is hard-coded in components, and a missing or
  mistyped translation key fails the TypeScript build. Messages produced by the server are shown as sent
  (English for now).
- Money, quantities and dates are formatted only at the edge (`en-IN`, DD/MM/YYYY, ₹).

```
frontend/src/
├── api/          typed API calls (client, products, inventory, catalog, exports)
├── features/     products/ (list, form, detail, opening stock, history), inventory/
├── components/   shared UI: buttons, fields, alerts, pagination, search, export buttons
├── lib/          decimal.ts (exact decimal checks), format.ts (display formatting)
├── layouts/  pages/  hooks/  i18n/  app/
```

## Backend layers

```
backend/
├── app/
│   ├── main.py            application factory and entry point
│   ├── seed.py            development seed (one shop + owner); refuses to run in production
│   ├── core/              settings, request context, development identity
│   ├── db/                engine, sessions/transactions, Money/Quantity/UTC types
│   ├── models/            SQLAlchemy models, one module per area
│   ├── schemas/           Pydantic request/response models (the JSON contract)
│   ├── api/
│   │   ├── deps.py        current shop/user, owner check
│   │   ├── errors.py      service errors -> HTTP responses
│   │   ├── routes/        operational routes (health)
│   │   └── v1/            versioned routers: reference, products, inventory, exports
│   └── services/          business rules and transactions
├── migrations/            Alembic environment and versions
└── tests/
```

| Layer | Responsibility | Must not |
|---|---|---|
| Routers (`api/`) | Parse the request, open the transaction for writes, call a service, shape the response | Contain business rules; import models |
| Schemas (`schemas/`) | Describe request/response JSON and parse money/quantity strictly | Import `api/` |
| Services (`services/`) | Enforce rules, raise `DomainError`s, never commit | Import `api/`, `schemas/` or `fastapi` |
| Models (`models/`) | Table definitions, constraints, column types | Contain workflows; import services or `api/` |
| DB (`db/`) | Engine, SQLite settings, transactions, column types | Know about models or business rules |
| Reporting (planned) | Read-only queries | Write anything |

These import rules are enforced by `tests/test_architecture.py`.

**Request flow (a write):** router -> `write_transaction()` -> service (rules, ledger, audit log) ->
commit -> response built from the same transaction. A `DomainError` rolls everything back and becomes a
404 (not found, also for another shop's data), 409 (conflict, e.g. duplicate SKU) or 422 (rule broken),
with the offending field named so a form can show the message next to it.

**Current shop and user:** `api/deps.py` resolves them through `context_service`. Until Phase 14 that is
the seeded development owner; in production it answers 503 so a deployment cannot run without login.
Phase 14 replaces that one function.

## Service layer and boundaries

| Service | Responsibility | Status |
|---|---|---|
| `inventory_service` | **The only reader and writer of the stock ledger.** Opening stock, adjustments, stock queries, inventory list, history, stock status | Phase 3 (purchases, sales, returns join it later) |
| `product_service` | Products: create, update, search, activate/deactivate; MRP setting; derives stock through `inventory_service` | Phase 3 |
| `catalog_service` | Units and categories | Phase 3 |
| `export_service` | Format engine: CSV/XLSX rendering, formula-injection protection. Knows nothing about products | Phase 3 |
| `export_datasets` | What each export contains (columns and rows), built from the domain services | Phase 3 |
| `audit_service` | Writes the insert-only audit log | Phase 3 |
| `shop_service`, `context_service` | Shop settings and "today"; the current shop/user | Phase 3 |
| `khata_service` | **The only writer of the customer ledger** | Phase 6 |
| `purchase_service` | Purchases and purchase returns | Phases 5, 9 |
| `detailed_sale_service` | Product-wise bills and sales returns | Phases 7, 9 |
| `quick_sale_service` | Money-only sales. It has **no dependency on `inventory_service`**, by design | Phase 8 |
| `costing_service` | Moving weighted average cost, cost snapshots for COGS | Phase 5 |
| `numbering_service` | Document numbers from a sequence table | Phase 7 |

Other services call `inventory_service` and `khata_service`; nothing else touches their tables. For
example the product list gets stock from `inventory_service.get_stock_map`, and exports get their rows from
`inventory_service.list_inventory` / `list_transactions`.

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

**Phase 1 (foundation):** application factory, settings, `GET /health`, responsive React shell,
English/Hindi switching, API client layer, live server-status badge.

**Phase 2 (database foundation):** SQLite engine and transactions, `Money`/`Quantity`/`UTCDateTime` types,
23 tables created by Alembic migration `0001` (units seeded, insert-only triggers), development seed.

**Phase 3 (first business functionality):**
- Products (create, edit, search, filter, activate/deactivate) and categories, through a JSON API with
  strict money/quantity parsing and shop isolation.
- The inventory ledger in use: opening stock, adjustments (service level), current stock derived from the
  ledger, inventory list with In/Low/Out of stock, transaction history with running balance.
- CSV/XLSX exports of products, inventory and stock history through a reusable export engine.
- Audit log entries for product, category and opening-stock changes.
- Screens: Products (list, add, edit, detail with history and opening stock) and Inventory.

**Not built yet:** suppliers, purchases, sales, returns, khata, expenses, dashboard analytics, reports,
authentication, and screens for adjustments. `khata_service` is still a placeholder.

## Testing strategy

- Database tests run on SQLite files created by the **real Alembic migration** (not `create_all`), so they
  test the schema that ships. The migration runs once per session; each test gets a private copy.
- They cover: connection and SQLite settings, write-transaction commit/rollback and the write lock,
  `Money`/`Quantity` exactness, migration up/down and model-versus-migration drift (`alembic check`),
  PostgreSQL DDL rendering, uniqueness, foreign keys, cross-shop references, value validity, the ledger rules
  and insert-only triggers, seed data, and the architecture rules.
- Phase 3 added tests through the real HTTP API (a client acting as a chosen shop, so two shops can be
  compared side by side) and at service level: products, opening stock and adjustments, derived stock and
  status, history with running balance, shop isolation, insert-only ledger, and exports (CSV/XLSX contents,
  formula neutralisation, scoping). Still to come: invariant tests (ledger sum equals reported stock across
  random sequences) and the overselling concurrency test, when sales exist.
- Frontend: TypeScript strict checks, oxlint, and a production build on every change.
- PostgreSQL: the same suite runs on PostgreSQL at the checkpoint after Phase 10 and again in Phase 15.

