# Architecture

> Sections marked **(planned)** describe the target design and are not implemented yet.
> What exists today is listed under [Current state](#current-state).

## Overview

**Shop Manager is a multi-business small-shop management SaaS.** Grocery / Kirana is one supported business
type among many (sweet shop, bakery, fruit, vegetable, dairy, general store, garments, footwear, cosmetics,
electronics, hardware, stationery, meat/food, other). The core is generic and business-agnostic:

```
Products -> Inventory -> Purchases -> Sales -> Returns -> Customers -> Expenses -> Reports -> Online Ordering
```

The internal project folder `kirana-store/` and a few technical names (`KIRANA_*` variables, `kirana.db`) are
the original working name; they are not part of the product identity and are renamed later.

The system is a modular monolith: one FastAPI backend, one React single-page app, one database.

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

## Business types: a generic core with optional defaults

Every shop has a `business_type` (a row in the `business_types` table). It exists to provide **defaults and
suggestions** and never to restrict anything:

- The **core** (products, inventory, purchases, sales, returns, customers, expenses, reports, exports) never
  reads the business type. There is one product model, one unit list, and one inventory ledger for every kind
  of business. Fractional quantities (25.5 kg of potatoes, 2.5 m of cloth) are a property of the *unit*,
  not of the business. A test scans the core source for business-specific wording and for any use of the type.
- The **only** module that knows what a type suggests is `business_type_service`: suggested categories and a
  suggested unit order. Every shop can still create any category and use any unit.
- A type is data. Adding one is an INSERT (a data migration); a type without a template simply suggests nothing.
- The template is the extension seam for later, still optional, defaults: dashboard labels, product templates,
  report layouts, online-store presentation.

### Future business-specific modules (not built)

Some kinds of business will eventually need specialised modules. They are **optional add-ons that reuse the
core**, never separate inventory systems or forks of it:

| Business | Possible future module |
|---|---|
| Halwai / Bakery | Production, recipes, ingredients, finished goods, wastage. Ingredients and finished goods are ordinary products; production is a pair of ledger movements (ingredients out, finished goods in) |
| Fruit / Vegetable | Wastage and spoilage (ledger adjustments with reasons), variable purchase cost. Weight-based stock already works |
| Garments / Footwear | Size and colour variants of a product |
| Electronics | Serial number / IMEI tracking and warranty per unit sold |

Each would add its own tables and screens next to the core, be enabled per shop (for example by business
type), and write stock only through `inventory_service`.

## Service layer and boundaries

| Service | Responsibility | Status |
|---|---|---|
| `inventory_service` | **The only reader and writer of the stock ledger, and the only place `avg_cost` is assigned.** Opening stock, adjustments, stock queries, inventory list, history (with the purchase behind each row), stock status; Phase 5 adds receiving purchase lines, reversing them, and rebuilding the average cost; Phase 7 adds shortage detection and taking sale lines out with their cost of goods | Phase 3 (purchases from Phase 5, sales from Phase 7; returns later) |
| `product_service` | Products: create, update, search, activate/deactivate; MRP setting; derives stock through `inventory_service` | Phase 3 |
| `catalog_service` | Units and categories | Phase 3 |
| `supplier_service` | Suppliers: create, update, search, activate/deactivate; duplicate warnings; the count of products per supplier. Generic for every business type | Phase 4 |
| `contact_validation` | Lenient validation and normalisation of phone, email and GSTIN. Used by suppliers and customers | Phase 4 |
| `customer_service` | The customer *record*: create, update, search, activate/deactivate, phone uniqueness within the shop. Knows nothing about money | Phase 6 |
| `business_type_service` | Business types, the shop's type, and the suggested categories/units. The only module that knows what a type suggests | Phase 3 |
| `export_service` | Format engine: CSV/XLSX rendering, formula-injection protection. Knows nothing about products | Phase 3 |
| `export_datasets` | What each export contains (columns and rows), built from the domain services | Phase 3 |
| `audit_service` | Writes the insert-only audit log | Phase 3 |
| `shop_service`, `context_service` | Shop settings and "today"; the current shop/user | Phase 3 |
| `khata_service` | **The only reader and writer of the customer ledger.** Opening balance, payment, adjustment, reversal, credit-sale and return-credit entries (the last two are for Phases 7 and 9), balances, the customer list with balances, and history with a running balance | Phase 6 |
| `purchase_service` | Purchases: draft, edit, post, void, correct, list and search, item rows for exports. Writes stock only through `inventory_service` | Phase 5 (returns: Phase 9) |
| `sale_service` | Detailed Sales: cart (draft), preview pricing, post, void, correct, list and search, item rows for exports. Writes stock only through `inventory_service` and credit only through `khata_service`. Sales returns join it in Phase 9 | Phase 7 |
| `sale_calculation` | The arithmetic of a bill (line, subtotal, total, cost of goods, profit, payment split): pure, no database, no floats, no tax | Phase 7 |
| `quick_sale_service` | Money-only sales. It has **no dependency on `inventory_service`**, by design | Phase 8 |
| `costing_service` | Moving weighted average: the pure arithmetic (no database, no floats) and the replay used to rebuild `avg_cost`. `cost_of_goods` (Phase 7) gives a sale line's cost, `None` when the cost is unknown | Phase 5 |
| `numbering_service` | Document numbers (`PUR/2026-27/0001`) from the `document_sequences` table, per shop, type and financial year | Phase 5 (sales reuse it from Phase 7) |

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

The table `customer_ledger` exists (Phase 2); `khata_service` (Phase 6) fills it.

Khata works the same way: an insert-only ledger with signed amounts, protected by the same trigger. A
customer's outstanding balance is the sum of their entries (**positive = the customer owes the shop, negative =
an advance**), computed on every read and never stored. Entry types: opening balance, credit sale,
payment, return credit, adjustment, reversal. `khata_service` is the only module allowed to reference the
table (same source-scanning test). Rules: [BUSINESS_RULES.md](BUSINESS_RULES.md), section KH.

Design points:
- **Two services.** `customer_service` handles the customer record and never touches the ledger.
  `khata_service` imports it (never the other way round) and provides everything that needs a balance, including
  the customer list with balances and balance filters, the same way `inventory_service` owns the inventory list.
- **One transaction.** The routers open `write_transaction()`; `khata_service` never commits. A customer created
  with an opening balance is one transaction. The customer row is locked first, so opening-balance duplicates
  and double reversals are refused even under simultaneous requests.
- **No generic insert.** Endpoints under `/api/v1/customers`: list, create, get, patch, activate, deactivate,
  balance, ledger, and the controlled writes `opening-balance`, `payments`, `adjustments`, `ledger/{id}/reverse`.
  There is no DELETE and no endpoint for credit sales or return credit: Phases 7 and 9 call the service.
- **Exports** `/api/v1/exports/customers` and `/customers/{id}/ledger`.
- **UI.** Customers list (search, balance and status filters, largest dues first), add/edit form (with an
  optional opening balance), and a detail page with the balance, contact details, action forms (payment,
  opening balance, adjustment), and the ledger with debit and credit columns, running balance and reversal.
  Migration `0005` adds `customers.email` and a name index.

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

## Future: online ordering (not built, and not grocery-specific)

A customer-facing online store is future work and must work for every business type:

```
Customer -> Shop online store -> Browse products -> Cart -> Address -> Payment -> Order
         -> Shop accepts -> Preparing -> Ready -> Out for delivery -> Delivered
```

The stages fit a grocery basket, a hot-sweets order (Preparing/Ready), a bakery cake, a bunch of vegetables or
a mobile charger alike. The design rule is **reuse, never duplicate**:

- It uses the same `products`, units, pricing, `customers` and inventory. There is **no second inventory
  system**. Stock stays the sum of `inventory_transactions`; an accepted order becomes a normal Detailed Sale
  through `sale_service`, which posts stock through `inventory_service`.
- Product presentation comes from the generic product fields; the business type may later supply storefront
  defaults (labels, categories order) through the same template seam. Business-specific pieces such as
  "Preparing" times or variants plug in as optional modules.
- New tables (storefront settings, carts, orders and order items, order payments, customer addresses, delivery)
  all carry `shop_id` and the composite tenant foreign keys. Details are in [DATABASE.md](DATABASE.md#future-online-ordering).

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

**Phase 3, extension (generic platform):** a `business_type` on every shop (15 types, extensible by data),
migration `0002`, four more units (metre, pair, bottle, tray), suggested categories and units per type, a
Settings screen, and generic wording in the UI (the app is "Shop Manager" and shows the business's own name).

**Phase 4 (suppliers):** generic supplier management (API and screens) with a default-supplier link from products,
migration `0003`, and the reusable contact-validation helpers. Product screens now show and pick the default
supplier, and the products export gains a "Default Supplier" column. Purchase history, returns and payments for a
supplier do not exist yet; the supplier page reserves room for them.

**Phase 5 (purchases):** supplier → purchase → purchase items → inventory ledger → moving weighted average.
`purchase_service` runs the DRAFT → POSTED → VOID lifecycle inside the router's single write transaction:
posting locks the purchase (a second post gets a conflict), locks the products in ascending id order (no
deadlocks), asks `inventory_service` to add each line and update the average, numbers the document and audits
it; any failure rolls all of it back. Voiding writes reversals through `inventory_service`, which then rebuilds
the average by replay. Endpoints under `/api/v1/purchases` (list, create, get, patch header, add/patch/replace
items, post, void, correct; no DELETE) and `/api/v1/exports/purchases`, `/purchase-items`, `/purchases/{id}`.
Field errors inside a list use dotted paths (`items.2.quantity`, sent as `["body","items",2,"quantity"]`). The
UI does line totals with exact integer (BigInt) arithmetic, so what is shown equals what the server computes.
Migration `0004`.

**Phase 6 (customers + khata):** customer management and the customer ledger described above, screens, exports
and migration `0005`. Credit sales and return credit exist only as service methods, ready for later phases.

**Phase 7 (detailed sales):** the product-wise billing workflow. `sale_service` runs the DRAFT to POSTED to VOID
lifecycle inside the router's single write transaction. Posting locks the sale (a second post gets a conflict),
locks the products in ascending id order, checks availability, asks `inventory_service` to take each line out and
report its cost of goods, numbers the document, charges any credit through `khata_service`, and audits; any
failure rolls all of it back. Voiding reverses both effects and rebuilds the average cost by replay.
Endpoints under `/api/v1/sales` (list, calculate, create, get, patch header, replace items, post, void, correct;
no DELETE) and `/api/v1/exports/sales`, `/sale-items`, `/sales/{id}`. **`POST /sales/calculate` prices a cart
without saving**, so the billing screen shows the server's numbers and duplicates no arithmetic; it also reports
stock on hand per line and the paid/credit split for an amount. Field errors inside a list use dotted paths
(`items.2.quantity`). The UI: sale list with filters, a billing screen (product search that also takes a
barcode scanner's typed code, cart, customer picker, bill discount, payment panel), and a detail page with the
payment, cost and profit ("Not available" when a cost is unknown) and the stock effect. Product history and the
customer's khata link to the invoice. Migration `0006`.

**Not built yet:** supplier payments and ledger, purchase returns, sales returns, quick sales, expenses,
dashboard analytics, reports, authentication, and screens for stock adjustments.

## Testing strategy

- Database tests run on SQLite files created by the **real Alembic migration** (not `create_all`), so they
  test the schema that ships. The migration runs once per session; each test gets a private copy.
- They cover: connection and SQLite settings, write-transaction commit/rollback and the write lock,
  `Money`/`Quantity` exactness, migration up/down and model-versus-migration drift (`alembic check`),
  PostgreSQL DDL rendering, uniqueness, foreign keys, cross-shop references, value validity, the ledger rules
  and insert-only triggers, seed data, and the architecture rules.
- Business-type tests check that products and stock work identically for every type and every unit, that types
  only ever suggest, and that the core source contains no business-specific code. Migration `0002` is tested
  against a database that already holds data. Simultaneous requests are tested for duplicate SKUs, barcodes
  and opening stock.
- Phase 3 added tests through the real HTTP API (a client acting as a chosen shop, so two shops can be
  compared side by side) and at service level: products, opening stock and adjustments, derived stock and
  status, history with running balance, shop isolation, insert-only ledger, and exports (CSV/XLSX contents,
  formula neutralisation, scoping). Still to come: invariant tests (ledger sum equals reported stock across
  random sequences) and the overselling concurrency test, when sales exist.
- Frontend: TypeScript strict checks, oxlint, and a production build on every change.
- PostgreSQL: the same suite runs on PostgreSQL at the checkpoint after Phase 10 and again in Phase 15.

## Phase 8 additions

**New services** (routers stay thin, services never commit or import HTTP, only `inventory_service` touches the stock
ledger and only `khata_service` the customer ledger):

| Service | Role |
|---|---|
| `quick_sale_service`, `payment_service` | money-only sales; payment resolution shared with `sale_service` |
| `product_lookup_service` | one lookup for scanning and typed codes |
| `promotion_service`, `promotion_calculation` | which offers may apply, and the pure paise arithmetic and stacking |
| `price_providers`, `price_comparison_service` | provider interface and outside-price matching, cache and history |
| `entitlement_service` | plan features, limits and monthly usage, enforced by the backend |
| `sales_report_service` | Detailed, Quick, Combined; Gross, Discount, Net; discount analytics |

**Offers and billing.** `sale_service` calls `promotion_service.evaluate` for the preview, whenever a draft is
re-totalled, and again at posting under a lock on any offer that has a usage limit. The frontend displays the
server's answer and does no discount arithmetic.

**External providers.** A provider is a small class behind one interface; adding one changes nothing else. The only
place that talks to the internet is `price_providers.http_get` (HTTPS only, fixed hosts, timeout, size cap). A price
check runs in three steps (read, fetch with no transaction open, write) so no database lock is held while waiting.
Providers run in parallel, each on its own, and every failure becomes a status, never an error. Keys are
`SecretStr` settings read from the backend environment (`UPCITEMDB_API_KEY`), never in the frontend, the database,
a URL, a log line or a response; the providers endpoint reports only configured true or false.

**Plans.** `GET /subscription` feeds the frontend `useEntitlements` hook, used only to show or hide. The server
refuses a feature the plan lacks with HTTP 403 and `type: plan_limit`.

**Design for Phase 9 (not built).** Error handling will reuse the exception-handler layer and the idempotency
mechanism; image intelligence will sit behind the same provider pattern and reuse `product_lookup_service` for
barcodes. See the Phase 9 entry in the roadmap.
