# Database

> **Status: implemented.** Migration `0001` (Phase 2) created the schema; migration `0002` (Phase 3 extension)
> added business types; migration `0003` (Phase 4) extended suppliers; migration `0004` (Phase 5) turned the purchase tables into a workflow; migration `0005` (Phase 6) added customer email; migration `0006` (Phase 7) turned the sale tables into a workflow. 24 tables. Rules are in [BUSINESS_RULES.md](BUSINESS_RULES.md);
> the workflows that fill these tables arrive in Phases 3 to 13.

## Principles

1. **Ledgers, not counters.** Stock and customer balances are derived from insert-only ledger tables.
   There is no editable `current_stock` or `balance` column anywhere (a test checks this).
2. **Nothing disappears.** Wrong entries are voided with reversal rows; documents are never hard-deleted.
3. **Snapshots on documents.** Prices, MRP and cost at the moment of a sale are copied onto the line, so
   later price changes never rewrite history.
4. **Tenant-safe by construction.** Every shop-owned table has `shop_id`, and references between shop-owned
   tables are composite foreign keys that include `shop_id` (see below).
5. **Portable SQL.** The same models and migration run on SQLite and PostgreSQL.

## Where things live

| What | Where |
|---|---|
| Engine, SQLite settings | `backend/app/db/engine.py` |
| Sessions, write transactions | `backend/app/db/session.py` |
| `Money`, `Quantity`, `UTCDateTime` types | `backend/app/db/types.py` |
| Models | `backend/app/models/` |
| Migrations | `backend/migrations/versions/` (`0001_initial_schema.py`) |
| Development seed | `backend/app/seed.py` |
| Development database file | `backend/data/kirana.db` (git-ignored) |

Configuration: `KIRANA_DATABASE_URL` (default `sqlite:///./data/kirana.db`; relative paths are resolved
against `backend/`) and `KIRANA_DB_BUSY_TIMEOUT_MS`.

## Conventions

| Topic | Decision |
|---|---|
| Money | Stored as **integer paise**. The `Money` type exposes `Decimal` rupees (`25.50` <-> `2550`). Floats are rejected with `TypeError`; more than 2 decimal places raises `ValueError` (no silent rounding) |
| Quantity | Stored as **integer thousandths** (1 kg = 1000). The `Quantity` type exposes `Decimal` with 3 places, so 250 g or 2.5 kg work. Whether a unit allows fractions is `units.allows_decimal`, enforced by services |
| Rounding | Computed amounts are rounded once, explicitly, with `round_money` / `round_quantity` (half up) and then stored. SQL only adds stored integers, so totals are exact |
| Ids | `BIGINT` on PostgreSQL, `INTEGER` on SQLite (its rowid alias, needed for auto-increment; already 64-bit) |
| Business dates | `DATE`, shop-local (default timezone `Asia/Kolkata`) |
| Timestamps | UTC via `UTCDateTime`. Naive datetimes are rejected; reads are always timezone-aware UTC. `created_at`/`updated_at` are set by the application |
| Unknown cost | `NULL`, never `0` |
| Enums | Text column + named `CHECK (col IN (...))`. No native database ENUM, so both databases behave the same |
| JSON | Generic `JSON` (audit log, idempotency), not PostgreSQL `JSONB` |
| Constraint names | Explicit and deterministic (`uq_`, `ck_`, `fk_`, `ix_`, `pk_` prefixes), all within PostgreSQL's 63-character limit (tested) |
| Numbering | `document_sequences` table, no database sequences |
| "Unique when present" | A plain `UNIQUE` constraint. `NULL`s are never equal in SQLite and PostgreSQL, so many rows may have no barcode/phone. Blank strings are rejected by a `CHECK` so `''` cannot collide |

## Tenant isolation

Every shop-owned table has `shop_id NOT NULL` referencing `shops.id`, and `UNIQUE (shop_id, id)`.
A reference to another shop-owned table is a composite foreign key:

```
products (shop_id, category_id)  ->  categories (shop_id, id)
```

so the database refuses a Shop A row that points at a Shop B row, whatever the application does. For
optional references (`customer_id`, `default_supplier_id`, ...) a `NULL` id skips the check and a set id
must match `shop_id`. Application queries must still filter by `shop_id`, but a missed filter can no longer
corrupt another shop's data. Tests verify this for products, suppliers, customers, sales, expenses, the
ledgers, and `created_by`.

Global tables: `shops` (the tenants) and `units` (shared reference data).

## Tables

### Tenancy and users
- **`business_types`** *(0002)*: code (primary key, e.g. `GROCERY`), English name, sort order, active flag. Shared
  reference data. Adding a kind of business is an `INSERT`, with no schema change. The 15 types are GROCERY, GENERAL_STORE,
  SWEET_SHOP, BAKERY, DAIRY, FRUIT, VEGETABLE, MEAT_FOOD, GARMENTS, FOOTWEAR, COSMETICS, ELECTRONICS, HARDWARE,
  STATIONERY and OTHER. What a type *suggests* (categories, units) lives in `business_type_service`, not here.
- **`shops`**: name (the business name), **`business_type`** *(0002, required, foreign key to `business_types`, no
  default so a new shop must choose)*, phone, address (all required), gstin?, upi_id?, timezone, language (`en`/`hi`),
  `allow_negative_stock` (default false), `mrp_validation_mode` (`WARN`/`BLOCK`).
  `mrp_validation_mode` has no database default so the default for new shops (Phase 3) can be chosen without a migration.
- **`users`**: shop, email (globally unique, stored lower-case, enforced by `CHECK`), phone?, password_hash,
  full_name, role (`OWNER`/`STAFF`), is_active. Since Phase 12 a row is the person's *membership* of the shop (see Migration 0015).

### Catalogue
- **`units`**: code (unique), name, allows_decimal. Shared by every business type. Seeded by the migrations: pcs, kg,
  g, L, ml, pkt, box, doz (`0001`) and m (metre), pair, btl (bottle), tray (`0002`). Units that allow fractions:
  kg, L, doz, m. Whether a quantity may have a fraction depends only on the unit, never on the business type.
  *Future:* per-shop custom units would be an additive change (a nullable `shop_id` on `units`).
- **`categories`**: shop, name (unique per shop), is_active.
- **`products`**: shop, sku (unique per shop), name, brand?, category (required), unit, default_supplier?,
  reorder_level (>= 0), **mrp?**, selling_price, **purchase_price?**, **avg_cost?**, barcode? (unique per shop
  when present), is_active. **No stock column.**
  `selling_price <= mrp` is deliberately **not** a `CHECK`: MRP validation is a per-shop warn/block setting
  enforced by services (BUSINESS_RULES P2).

### Parties
- **`suppliers`**: shop, name (required, not unique), phone?, alternate_phone?, email?, address?, gstin?, notes?,
  is_active. *(alternate_phone, email and notes were added by `0003`.)* Phone numbers are stored compactly
  (`9876543210`, `+919876543210`), email in lower case, GSTIN in upper case. Names, phones and GSTINs are
  deliberately **not** unique: a likely duplicate is a warning, not an error. Indexed on `(shop_id, name)`.
  A product refers to its optional default supplier through the composite foreign key
  `products (shop_id, default_supplier_id) -> suppliers (shop_id, id)`, so a product can never use another
  shop's supplier, and `(shop_id, default_supplier_id)` is indexed for "products of this supplier".
- **`customers`**: shop, name, phone? (unique **per shop** when present, never across shops; stored compactly),
  `email?` *(added by `0005`)*, address?, notes?, is_active. Indexed by `(shop_id, name)` for search. The khata
  balance is not stored here: it is the sum of the customer's `customer_ledger` rows.

### Ledgers (insert-only)
- **`inventory_transactions`**: the stock ledger.
  `txn_type`: `OPENING`, `PURCHASE`, `SALE`, `SALE_RETURN`, `PURCHASE_RETURN`, `ADJUSTMENT`, `REVERSAL`.
  Columns: product, signed `qty_delta`, `unit_cost?`, `txn_date` (business date), `reference_type` +
  `reference_id` (the source document line; a polymorphic pointer, so no foreign key), `reverses_txn_id?`,
  `reason_code?`, `note?`, `created_by`, `created_at`.
  - Sign matches type (`OPENING`/`PURCHASE`/`SALE_RETURN` > 0, `SALE`/`PURCHASE_RETURN` < 0, `ADJUSTMENT`/`REVERSAL` <> 0).
  - `ADJUSTMENT` requires a `reason_code` (`CUSTOMER_RETURN_NO_BILL`, `COUNT_CORRECTION`, `DAMAGED`,
    `EXPIRED`, `LOST`, `OTHER`); only adjustments carry one; `OTHER` needs a non-blank note.
  - A `REVERSAL` must point at the row it undoes, and a row can be reversed once.
  - `UNIQUE (shop_id, reference_type, reference_id, txn_type)`: a document line posts each type once.
  - `reference_type` values: `PRODUCT` (opening stock), `PURCHASE_ITEM`, `SALE_ITEM`,
    `PURCHASE_RETURN_ITEM`, `SALES_RETURN_ITEM`.
- **`customer_ledger`**: the khata ledger. `entry_type`: `OPENING_BALANCE`, `CREDIT_SALE`, `PAYMENT`,
  `RETURN_CREDIT`, `ADJUSTMENT`, `REVERSAL`. Signed `amount_delta` (positive = the customer owes more),
  `payment_method?` (payments only), `reference_type?` (`SALE`, `QUICK_SALE`, `SALES_RETURN`) + `reference_id`,
  `reverses_entry_id?` (unique: an entry is reversed at most once), `note?`, `entry_date`, `created_by`.
  Same sign, reversal and reference rules as the stock ledger. `UNIQUE (shop, reference_type, reference_id,
  entry_type)` means one document line is credited once. `payment_reference?` holds a payment's transaction
  number. Opening-balance uniqueness (one live per customer) is enforced by `khata_service` under the customer
  row lock, because a reversed opening balance may legitimately be entered again.

Both ledgers, and `audit_log`, are **protected by database triggers** that abort any `UPDATE` or `DELETE`
(SQLite `RAISE(ABORT)`, PostgreSQL trigger function). They have no `updated_at` column. Because rows can
never be deleted, tests build a fresh database per test rather than cleaning up.

### Purchasing
- **`purchases`** *(reshaped by `0004`)*: supplier, supplier_invoice_no?, purchase_date, total_amount (sum of
  the line totals), amount_paid (<= total; unused until supplier payments), payment_method?,
  payment_reference?, notes?, `status` (`DRAFT`/`POSTED`/`VOID`, no default: created as `DRAFT`),
  `purchase_no?` (unique per shop; set at posting), `posted_at?`, `posted_by?`, `void_reason?` (required when
  `VOID`), `voided_at?`, `replaces_id?` (unique), `created_by`.
  - `purchase_no` and `posted_at` exist together; a `POSTED` purchase must have a number; a `DRAFT` has none.
  - The supplier-invoice rule is a **partial unique index** `(shop, supplier, supplier_invoice_no) WHERE
    status <> 'VOID'`: unique among live purchases, released by a void. (Works on SQLite and PostgreSQL.)
  - This table no longer uses the shared document-lifecycle columns; `0004` replaced the status check
    (`'POSTED','VOID'` → `'DRAFT','POSTED','VOID'`) and the void-reason check.
- **`purchase_items`** *(extended by `0004`)*: purchase, product, `unit_id` (the product's unit), quantity > 0,
  unit_cost (price before discount), `discount` (>= 0, an amount), line_total (= round(quantity × unit_cost) −
  discount; what costing uses), and a posting snapshot, `NULL` on drafts: `stock_before`, `avg_cost_before?`,
  `avg_cost_after?` (`NULL` cost means unknown).
- **`purchase_returns`**: purchase, return_date, `credit_mode` (`CASH`/`UPI`/`SUPPLIER_CREDIT`), total_amount, reason?.
- **`purchase_return_items`**: return, the original `purchase_item`, product, quantity > 0, unit_cost, line_total.

### Sales
- **`sales`** *(reshaped by `0006`)* (Detailed Sale): `invoice_no?` (unique per shop; set when posted), `status`
  (`DRAFT`/`POSTED`/`VOID`, no default: created as `DRAFT`), sale_date, customer?, `subtotal`, `discount` (an
  amount off the whole bill), total_amount, `payment_type?` (`PAID`/`CREDIT`), `amount_paid?`, payment_method?,
  payment_reference?, notes?, `posted_at?`, `posted_by?`, `void_reason?`, `voided_at?`, `replaces_id?`,
  `created_by`.
  - `total_amount = subtotal - discount` (a CHECK), so a bill discount above the items cannot exist.
  - `invoice_no` and `posted_at` exist together; a `POSTED` sale must have a number; a `DRAFT` has none; a numbered
    sale has its payment details (only a draft, or a discarded draft, lacks them); `VOID` needs a reason.
  - The Phase 2 payment rules are unchanged and apply whenever the payment fields are set: `PAID` means paid =
    total, `CREDIT` needs a customer and paid < total, money received needs a method. (Payment fields are `NULL`
    on a draft, which passes them.) There is no room for overpayment.
  - This table no longer uses the shared document-lifecycle columns.
- **`sale_items`** *(extended by `0006`)*: sale, product, `unit_id` (the product's unit), quantity > 0, unit_price,
  mrp? (snapshot), discount, line_total (= round(quantity x price) - discount; >= 0, which also prevents a
  discount above the gross), `unit_cost?` and `cogs_amount?` (both set or both `NULL`; the cost snapshot taken at
  posting, `NULL` on a draft and whenever the cost was unknown).
- **`sales_returns`**: sale, return_date, `refund_mode` (`CASH`/`UPI`/`KHATA`), total_refund, reason?.
- **`sales_return_items`**: return, the original `sale_item`, product, quantity > 0, refund_amount, and
  `unit_cost?`/`cogs_amount?` copied from the original line.
- **`quick_sales`** (Quick/Daily Sale): sale_date, total_amount > 0, customer?, note?, payment fields.
  **Money only: no product, quantity or cost columns and no foreign key to products or sale lines.**
  It can be on credit (the same PAID/CREDIT rules as `sales`). Tests assert this structure.

### Expenses
- **`expense_categories`**: shop, name (unique per shop), is_active.
- **`expenses`**: expense_date, category, description?, amount > 0, payment_method.

### Cross-cutting
- **`document_sequences`**: unique per (shop, doc_type, fiscal_year); `last_number >= 0`.
- **`idempotency_keys`**: unique per (shop, key); operation, request_hash, response status/body.
- **`audit_log`**: insert-only. user? (`NULL` for system actions), entity_type, entity_id?, action,
  `before_json?`, `after_json?`.

### Document lifecycle (purchases, purchase_returns, sales, sales_returns, quick_sales, expenses)
`status` (`POSTED`/`VOID`), `void_reason` (required when void), `voided_at`, `replaces_id?` (unique; points
at the document this one corrects), `created_by` (required, same shop), and timestamps.

## How Phase 3 uses the tables

| Table | Use |
|---|---|
| `products`, `categories`, `units` | Product management. `avg_cost` is set from the opening cost |
| `inventory_transactions` | `OPENING` rows (reference `PRODUCT` + product id) and `ADJUSTMENT` rows (service level). Stock is `SUM(qty_delta)` |
| `audit_log` | Product, category and opening-stock changes, with before/after values (money as strings) |
| `shops` | `allow_negative_stock`, `mrp_validation_mode` (default `WARN`), timezone for "today" |

Because there is **one `OPENING` row per product** (`UNIQUE (shop_id, reference_type, reference_id, txn_type)`),
a second opening entry is refused by the database as well as by the service.

## Migration 0002: business types

- Creates `business_types` and seeds the 15 types; inserts the four new units.
- Adds `shops.business_type`. **Existing shops become `GROCERY`**, because every shop that existed before was a
  kirana/grocery shop. The temporary default is dropped in a second step so future shops must choose.
- On SQLite adding a foreign-keyed column means rebuilding `shops`, a table that many tables reference. SQLite's
  documented procedure needs foreign key enforcement **off** while the table is rebuilt, so the migration
  environment connects with enforcement off and then runs `PRAGMA foreign_key_check`; any violation fails the
  migration. The application itself always runs with foreign keys on. On PostgreSQL it is plain `ALTER TABLE`.
- Tested against a database that already holds a shop, user, category, product and ledger row: all rows and
  constraints survive, foreign keys stay valid, the ledger trigger survives, downgrade and re-upgrade work.
- The downgrade removes the four new units and fails loudly if a product already uses one.

## Migration 0003: supplier details

Adds `suppliers.alternate_phone`, `email` and `notes` (all nullable) and two indexes. Nothing else changes and
no table is rebuilt: existing suppliers keep their data (the new columns are `NULL`) and product links stay
valid. Tested against a database that already holds a supplier and a linked product, including downgrade and
re-upgrade. Format checks (phone, email, GSTIN) are done by the service, not by `CHECK` constraints, because
adding a `CHECK` to an existing SQLite table means rebuilding it.

## Migration 0004: purchases

Turns `purchases` and `purchase_items` into the Phase 5 workflow (columns above). Steps: add the new columns;
**backfill** any purchase that already exists as `POSTED` with the number `PUR/LEGACY/<id>` and `posted_at =
created_at`, and each line's `unit_id` from its product; then tighten the constraints (status now allows
`DRAFT`, the new number/posted/draft checks, the unique number, the `posted_by` foreign key), replace the
old unique `(shop, supplier, invoice)` constraint with the partial index, and add the line checks. The table
rebuilds SQLite needs for this run with foreign keys off, then `PRAGMA foreign_key_check` (as in `0002`); on
PostgreSQL it is plain `ALTER TABLE`. Because the migration environment re-prefixes check-constraint names,
every `CHECK` name in the migration is wrapped in `op.f(...)`. The backfill is a single SQL statement, so
`alembic upgrade --sql` also renders. Tested against a database holding posted and void purchases: rows,
numbers, units and foreign keys survive, the new rules are enforced, downgrade and re-upgrade work.
**Downgrade is refused while any `DRAFT` purchase exists** (revision `0003` has no draft status): post or
discard them first.

## Migration 0005: customers

Adds `customers.email` (nullable) and the index `ix_customers_shop_id_name`. No table is rebuilt on upgrade, so
the insert-only ledger triggers are not touched. The downgrade drops the column, which makes SQLite rebuild
`customers` (foreign keys off, then `PRAGMA foreign_key_check`, as in `0002`); the ledger triggers survive
because `customer_ledger` itself is not rebuilt. Tested against a database holding a customer with ledger
entries: rows, balance, indexes and triggers survive upgrade, downgrade and re-upgrade.

## Migration 0006: sales

Turns `sales` and `sale_items` into the Phase 7 workflow (columns above). Steps: add `subtotal`, `discount`,
`posted_at`, `posted_by`; **backfill** existing sales with `subtotal = total_amount`, `posted_at = created_at`,
`posted_by = created_by` (any sale that already exists was posted, so it keeps its number and payment); then
relax `invoice_no`, `payment_type` and `amount_paid` to nullable, replace the status check
(`'POSTED','VOID'` becomes `'DRAFT','POSTED','VOID'`) and the void-reason check, add the new checks and the
`posted_by` foreign key. `sale_items.unit_id` is filled from each line's product, then made `NOT NULL` with a foreign
key to `units`. The table rebuilds SQLite needs run with foreign keys off, then `PRAGMA foreign_key_check` (as in
`0002` and `0004`); on PostgreSQL it is plain `ALTER TABLE`. Every `CHECK` name in the migration is wrapped in
`op.f(...)` and matches the name the model produces (a test compares them, because `alembic check` does not).
The backfill is plain SQL, so `alembic upgrade --sql` also renders. Tested against a database holding a posted
and a voided sale with a cost snapshot: rows, numbers, units and foreign keys survive, the new rules are enforced,
downgrade and re-upgrade work. **Downgrade is refused while any `DRAFT` sale exists** (revision `0005` has no
draft status): post or discard them first.

## Always derived, never stored

- Current stock and the Opening / Purchased / Sold / Returned / Adjusted columns of the inventory screen
- Customer outstanding balance
- Returned quantity per sale or purchase line
- Detailed versus Quick totals
- A supplier's "products supplied"

The one deliberate cache is `products.avg_cost`, updated in the same transaction as each stock movement
and rebuildable from the ledger.

## Editing and deleting

Ledger rows and posted documents are never updated in place. A wrong entry is **voided**: `status = 'VOID'`
with a reason and `REVERSAL` rows in the ledgers. An **edit** is a void plus a new document linked by
`replaces_id`, in one transaction. Customer returns are separate documents, not voids.

## Transactions and locking

- Reading uses a plain transaction (`read_session`, `get_session`); it never commits.
- Writing uses `write_transaction()` / `get_write_session`: one transaction, committed on success and
  rolled back on any exception. **One business action is one write transaction**, with the stock check and
  the ledger insert inside it. Services never call `commit()` themselves.
- On SQLite a write transaction starts with `BEGIN IMMEDIATE`, so writers are serialized and two sales
  cannot both see the last unit as available. A test proves a second writer is refused at the start.
  Readers are never blocked (WAL mode).
- On PostgreSQL services will additionally lock the product rows (`.with_for_update()`, ignored by SQLite).
  That call is added with `inventory_service` in Phase 3.
- SQLite settings applied to every connection: `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout`. The
  default `synchronous=FULL` is kept: durability matters more than speed for money.

## Migrations

- `0001_initial_schema` creates all tables, seeds the units, and installs the insert-only triggers. It uses
  plain SQLAlchemy types and never imports application code, so it stays valid as models evolve.
- Each migration runs in one transaction (`transactional_ddl`).
- `alembic check` must report no differences between the models and the migrated database; a test enforces it.
- The database file is only ever created or changed through Alembic (and the seed script for dev data).
- Future migrations that alter an existing SQLite table use batch mode (already enabled), which rebuilds the
  table. Run them with foreign keys off for that step; this is handled when the first such migration is written.

Common commands (from `backend/`):

```bash
alembic upgrade head        # create/upgrade the database
alembic current             # show the applied revision
alembic check               # verify models and migrations agree
alembic downgrade base      # remove everything
python -m app.seed          # development shop + owner (never in production)
```

## Seed data

- **Units** (in the migration, present in every environment): pcs, kg, g, L, ml, pkt, box, doz.
- **Development data** (`python -m app.seed`): one shop "Development Shop" and one owner user
  `owner@dev.kirana.local` whose password hash is `!`, which no password can match. It exists so ledger and
  document rows can record a `created_by`. No products, sales, or money are created.

## SQLite now, PostgreSQL later

No MVP feature requires PostgreSQL.

| SQLite behaviour | Handling (works on both databases) |
|---|---|
| No true `NUMERIC`; decimals become floats | Integer paise and thousandths |
| No `SELECT ... FOR UPDATE` | `BEGIN IMMEDIATE` on SQLite; `.with_for_update()` for PostgreSQL |
| Limited `ALTER TABLE` | Alembic batch mode |
| Foreign keys off by default | `PRAGMA foreign_keys=ON` on every connection |
| Timestamps lose timezone | `UTCDateTime` |
| Trigger syntax differs | The migration branches per dialect |
| Boolean defaults `0`/`1` | `sa.true()` / `sa.false()` |

**What is verified today:** the migration is rendered as PostgreSQL DDL offline (no server) and a test checks
it: `BIGSERIAL` ids, `DEFAULT false`, `TIMESTAMP WITH TIME ZONE`, the trigger function, no SQLite-only
syntax. **What is not yet verified:** running it on a real PostgreSQL server, the PostgreSQL trigger
function, and the concurrency test. That happens at the PostgreSQL checkpoint after Phase 10.

PostgreSQL becomes necessary when many shops write concurrently in a hosted deployment, when Row-Level
Security is wanted, or when managed backups and replicas are needed.

## Future: online ordering

*(Generic: it must work for every business type; see [ARCHITECTURE.md](ARCHITECTURE.md).)*

Not built, and the schema does not prevent it. The design rule is **reuse, never duplicate**: an online
order must use the same `products`, `customers`, pricing and inventory as in-store sales.

- New tables would all carry `shop_id` and use the same composite tenant foreign keys: storefront
  settings, `carts`/`cart_items`, `orders`/`order_items`, order payment status (COD, UPI), delivery address
  (`customer_addresses`), delivery status and assignment.
- Customers already have the fields online orders need (name, phone); login credentials, addresses and
  consent would be added in new tables, not by changing the khata.
- **No second inventory system.** Stock stays the sum of `inventory_transactions`. An accepted order is
  fulfilled by creating a normal Detailed Sale through `detailed_sale_service`, which posts `SALE` rows via
  `inventory_service`. Any "reserved" stock would be a separate, non-ledger concept subtracted when showing
  availability, never a second stock number.
- `sales` could later gain a nullable `order_id` and a `channel` column by an additive migration.
- Online payments would reuse `payment_method`/`payment_reference`, which are already on documents.

## Migrations 0007 to 0010 (Phase 8)

* **0007 subscriptions.** `plans` and `plan_features` (global reference data, seeded with three editable example
  plans), `shop_subscriptions` (one current TRIAL or ACTIVE per shop, by a partial unique index) and
  `subscription_usage` (per shop, month and metric). No payment tables.
* **0008 quick sales.** `quick_sales` gains `quick_no`, `gross_amount`, `discount`, `posted_at/by`; `status` gains DRAFT;
  payment columns become nullable for a draft; existing rows become POSTED with number `QS/LEGACY/<id>`. The table
  still has no product, quantity or cost column (a test guards it).
* **0009 promotions.** `promotions` (kind, scope, status, priority, stackable, dates, coupon code unique per shop,
  benefit columns of exactly one kind enforced by a CHECK, conditions, limits, JSON `targets`), `sale_promotions`
  (the frozen snapshot per posted sale), `sales.promotion_discount`, `sales.coupon_code`,
  `sale_items.promotion_discount`. The sale rule becomes `total = subtotal - discount - promotion_discount` and a
  line's share cannot exceed the line. Downgrade is refused while a sale used an offer.
* **0010 price observations.** `price_observations`: append-only, per shop; it is both the price-check history and
  the cache. Prices are positive, currency is three letters, and nothing references a product.

Money is integer paise, a percentage is basis points, quantities are thousandths. Every new table has `shop_id`,
timestamps and composite (shop, id) keys; SQLite and PostgreSQL both support everything used.

## Migrations 0011 and 0012 (Phase 9)

* **0011 return numbers.** `sales_returns.return_no` and `purchase_returns.return_no` (`SRT/2026-27/0001`,
  `PRT/2026-27/0001`, gapless per shop and financial year through the existing document sequence). Added as nullable,
  backfilled for any existing row (`SRT/LEGACY/<id>`, `PRT/LEGACY/<id>`), then made NOT NULL with a unique
  `(shop_id, return_no)` and a not-blank CHECK. The downgrade drops them again. The returns tables, their item tables and
  the ledger transaction types (`SALE_RETURN`, `PURCHASE_RETURN`) already existed from Phase 2; nothing about the
  original sale or purchase is changed by a return.
* **0012 product images.** `product_images`: the one photo a shop chose to keep for a product, **metadata only**
  (`sha256`, `content_type`, `size_bytes`, `width`, `height`, `storage_key`, `created_by`). Unique per
  `(shop_id, product_id)` (at most one photo per product), composite tenant keys to the product and the user, positive
  size and dimensions, a 64-character hash. The file itself is never in the database: it lives in a private image store
  (`<shop_id>/<sha256>.<ext>`, a folder that is git-ignored and never served directly; object storage later behind the
  same interface). Nothing creates a row from an analysis. It also adds the plan feature `image_intelligence` (on for
  `pro`, off for the others).

The idempotency table (`idempotency_keys`: shop, key, operation, request hash, stored response) predates Phase 9; the
mechanism that uses it is now shared by every create and post (sales, quick sales, purchases, returns, products,
customers, offers, confirmed photo products). Diagnostics are **not** stored in the database: they go to the server log
(and optionally a private file), so there is no table a screen could read.

## Migration 0013 (Phase 10)

* **`ai_usage`.** One row per request that reached the AI layer: shop, user, `feature` (ask, tool, invoice_photo,
  stock_list_photo), `provider`, `model`, `status` (OK, FAILED, UNSUPPORTED), `input_tokens`, `output_tokens`, and
  `estimated_cost_micros` with `cost_currency` only when the provider reports a cost. No question, prompt, answer or
  document text. Index on (shop, created_at). It feeds the usage screen and, with `subscription_usage`, the monthly limit.
* **`ai_actions`.** A change the AI prepared and what a person did with it: `kind` (PURCHASE_DRAFT, STOCK_ADJUSTMENT,
  PROMOTION_DRAFT), `status` (PROPOSED, EXECUTED, CANCELLED, FAILED), `feature`, `proposal` (as first proposed, never
  edited), `current` (what a confirmation would do now), `attempts`, `result_type` and `result_ids` (only when EXECUTED,
  enforced by a CHECK), `failure_message`, `reference_id`, `decided_by`, `decided_at`. Tenant keys to users. The
  business data itself is created by the existing services and lives in their tables.
* **Plan data.** Features `ai_assistant`, `ai_insights`, `ai_documents` and the limit `max_ai_requests_per_month`, seeded
  for the three example plans (free: basic questions, 30 a month; basic: plus insights, 300; pro: everything, unlimited).
  Editable like all plan entries; the downgrade removes them. `subscription_usage` gains the metric `ai_requests`.

Audit entries for AI actions use the existing `audit_log` (entity `ai_action`).

## Migration 0014 (Phase 11)

One additive migration (tested up, down and up again; a Phase 10 database keeps all its data):

| Change | Detail |
|---|---|
| `shops` | `account_status` (default `ACTIVE`), `status_reason`, `status_changed_at` |
| `audit_log` | nullable `request_id` |
| `system_admins`, `admin_audit_logs`, `support_access_grants` | platform tables (no `shop_id` except where a shop is the target); `admin_audit_logs` is insert-only (triggers on SQLite and PostgreSQL) |
| `system_events` | operational events, optional shop |
| `backup_records`, `restore_records` | backup and restore history; no credentials or absolute paths |
| `notification_events`, `notification_deliveries`, `notification_preferences` | shop-owned; composite foreign keys `(shop_id, id)` keep an event, a delivery and a user in one shop; `(event, user, channel)` unique |
| `plan_features` | `exports` (on) and `max_exports_per_month`, `max_image_analyses_per_month` (unlimited): no plan changes behaviour |

Indexes were **not** added: the hot paths were checked with `EXPLAIN QUERY PLAN` (see `PRODUCTION.md`) and already use existing
indexes. `docs/POSTGRES_MIGRATION_CHECKLIST.md` lists what changes when PostgreSQL replaces SQLite.

## Migration 0015 (Phase 12)

Additive, offline-renderable SQL for the data steps, tested up, down and up again and on a Phase 11 database with data:

| Change | Detail |
|---|---|
| `accounts` | one sign-in identity per email: Argon2id hash, status, failed-login count, pause time, last login |
| `users` (now the membership) | + `account_id`, `role_id`, `status` (`INVITED/ACTIVE/SUSPENDED/REMOVED`), `invited_by`, `joined_at`, `removed_at`, `last_active_at`; the global unique email became `UNIQUE (shop_id, email)` and `UNIQUE (account_id, shop_id)`, so one person can belong to several shops |
| `roles`, `role_permissions` | system roles are global rows (`shop_id` NULL, unique code); custom roles belong to a shop; six system roles are seeded with their default permissions |
| `invitations` | token **hash** only; status; expiry; composite FK to the inviter |
| `auth_sessions` | token hash, CSRF hash, account, chosen membership, idle and absolute times, revocation |
| `background_jobs` | the job queue: type, idempotency key (unique per type), status, attempts, next run, safe error |

Every existing user becomes an account and a membership (OWNER stays OWNER; STAFF becomes CASHIER; an inactive user becomes SUSPENDED). Nothing is deleted.
Downgrade removes the new structures and is **refused while one email belongs to more than one shop**. No new indexes beyond those the queries use.


## Migration 0017: CRM, loyalty, campaigns, automation, referrals (Phase 14)

| Change | Detail |
|---|---|
| `customers` | + `customer_type`, `source`, `tags`, `preferred_contact_channel`, four `marketing_opt_in_*` flags (default false), `referred_by_customer_id` (composite self-FK) |
| `shops` | + `crm_campaign_audience_threshold`, `crm_loyalty_adjustment_threshold` (nullable, `>= 0`; NULL = no extra approval) |
| `customer_notes` | insert-only |
| `customer_groups`, `customer_group_members` | manual or rule-based groups |
| `loyalty_programs`, `loyalty_ledger` | one program per shop; the ledger is insert-only, unique on (shop, customer, entry type, reference type, reference id) |
| `campaigns`, `campaign_audience_snapshots`, `campaign_sends` | snapshots and sends are insert-only; `campaigns.requires_approval` |
| `automation_rules`, `automation_runs` | runs are insert-only |
| `referral_programs`, `referral_codes`, `referral_events` | one referred customer per referral (unique) |
| `ai_actions.kind` | CHECK widened to include `CAMPAIGN_DRAFT` |
| `role_permissions` | the 12 CRM permissions inserted for the system roles |

All money is integer paise; every table carries `shop_id` with composite tenant foreign keys. Five tables get the existing insert-only
triggers. Downgrade is tested (up → down → up). Nothing existing is renamed or dropped.
