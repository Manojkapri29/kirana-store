# Architecture

> Sections marked **(planned)** describe the target design and are not implemented yet.
> What exists in Phase 1 is listed under [Current state](#current-state).

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
backend/app/
├── main.py            application factory and entry point
├── core/              settings and other cross-cutting setup
├── api/
│   ├── routes/        operational routes (health)
│   └── v1/            versioned business routers, mounted at /api/v1
└── services/          business rules and transactions
```

| Layer | Responsibility | Must not |
|---|---|---|
| Routers (`api/`) | Parse and validate the request, call a service, shape the response | Contain business rules or touch the database |
| Services (`services/`) | Enforce rules, run one database transaction per action | Import from `api/` |
| Models (planned, Phase 2) | Table definitions and column types | Contain workflows |
| Reporting (planned) | Read-only queries | Write anything |

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

## Inventory ledger (planned)

Stock is **never stored as an editable number**. Every stock movement is a row in the insert-only
`inventory_transactions` table with a signed quantity, and current stock is the sum for a product:

```
Current Stock = Opening + Purchases - Sales + Sales Returns - Purchase Returns ± Adjustments
              = SUM(inventory_transactions.qty_delta)
```

- Rows are only inserted. Corrections are new rows (returns, adjustments, reversals), so the history
  is complete and auditable.
- Only Detailed Sales touch the ledger. Quick Sales never do (see [BUSINESS_RULES.md](BUSINESS_RULES.md)).
- The stock check and the write happen in one database transaction. On SQLite, write transactions are
  serialized; on PostgreSQL, product rows are locked (`SELECT ... FOR UPDATE`).

## Customer ledger (planned)

Khata works the same way: an insert-only `customer_ledger` with signed amounts. A customer's outstanding
balance is the sum of their entries. Entry types: opening balance, credit sale, payment, return credit,
adjustment, reversal.

## Reporting layer (planned)

`reporting/` is a read-only package of query functions (top products, low stock, sales by mode, profit
estimate, ...). The dashboard, the reports screens and exports all call the same functions, so a number
is computed in exactly one place. It never writes, and it is where the rules about not mixing Quick Sales
into product analytics or profit are enforced.

**Future AI assistant:** these same functions become the assistant's tools ("which products are low?",
"what was my estimated profit?"). The assistant calls vetted functions; it never generates SQL against shop data.

## Multi-shop SaaS readiness

Not built in the MVP, but the foundations are laid now because they are expensive to retrofit:

- Every business table carries `shop_id`, and every query is scoped by it.
- The service layer receives the current shop from a single dependency. It is a fixed development shop
  until authentication (Phase 14) replaces it with the logged-in user's shop.
- Users belong to a shop and have a role (owner/staff).
- No shop data in global state, files or caches without a shop key.
- Later: PostgreSQL Row-Level Security as defence in depth, subscription plans and usage limits, an admin
  panel, WhatsApp notifications, scheduled reports and cloud backup.

## Database strategy

SQLite for the MVP (zero setup, one file), PostgreSQL when hosting many shops. The code is written so the
switch is a configuration change plus a verification run. Details, and why SQLite is safe for the ledger
logic, are in [DATABASE.md](DATABASE.md).

## Configuration and security

- Configuration comes from environment variables only (`KIRANA_*` for the backend, `VITE_*` for the
  frontend). `.env` files are git-ignored; `.env.example` files document every variable.
- Interactive API docs are disabled when `KIRANA_ENVIRONMENT=production`.
- CORS allows only the configured origins.
- Later phases add: argon2 password hashing, short-lived tokens, login rate limiting, audit logging,
  idempotency keys for create endpoints, and per-shop authorization checks.

## Current state

Phase 1 delivers the skeleton only:

- Backend: application factory, settings, `GET /health`, an empty `/api/v1` router, tests.
- Frontend: routing, responsive shell (sidebar drawer on mobile), dashboard placeholder, "coming soon"
  pages for planned modules, English/Hindi switching, API client, and a live server-status badge.
- No database, models, authentication, or business features.

## Testing strategy

- Backend: pytest against the FastAPI app. From Phase 2, tests run on a real SQLite database, with
  service-level tests for every business rule, invariant tests (ledger sum equals reported stock across
  random sequences), a concurrency test for overselling, and tenant-isolation tests.
- Frontend: TypeScript strict checks, oxlint, and a production build on every change.
- PostgreSQL: the same suite runs on PostgreSQL at the checkpoint after Phase 10 and again in Phase 15.
