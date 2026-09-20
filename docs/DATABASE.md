# Database

> **Status: implemented.** Migration `0001` (Phase 2) created the schema; migration `0002` (Phase 3 extension)
> added business types. 24 tables. Rules are in [BUSINESS_RULES.md](BUSINESS_RULES.md);
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
  full_name, role (`OWNER`/`STAFF`), is_active. Login is built in Phase 14.

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
- **`suppliers`**: shop, name, phone?, address?, gstin?, is_active.
- **`customers`**: shop, name, phone? (unique per shop when present), address?, notes?, is_active.
  The khata balance is not stored here.

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
  `reverses_entry_id?`, `note?`. Same sign, reversal and reference rules as the stock ledger.

Both ledgers, and `audit_log`, are **protected by database triggers** that abort any `UPDATE` or `DELETE`
(SQLite `RAISE(ABORT)`, PostgreSQL trigger function). They have no `updated_at` column. Because rows can
never be deleted, tests build a fresh database per test rather than cleaning up.

### Purchasing
- **`purchases`**: supplier, supplier_invoice_no? (unique per supplier when present), purchase_date,
  total_amount, amount_paid (<= total), payment_method?, payment_reference?, notes?, plus the document
  lifecycle columns below.
- **`purchase_items`**: purchase, product, quantity > 0, unit_cost, line_total.
- **`purchase_returns`**: purchase, return_date, `credit_mode` (`CASH`/`UPI`/`SUPPLIER_CREDIT`), total_amount, reason?.
- **`purchase_return_items`**: return, the original `purchase_item`, product, quantity > 0, unit_cost, line_total.

### Sales
- **`sales`** (Detailed Sale): invoice_no (unique per shop), sale_date, customer?, total_amount,
  `payment_type` (`PAID`/`CREDIT`), amount_paid, payment_method?, payment_reference?, notes?.
  `PAID` means paid = total. `CREDIT` needs a customer and paid < total. Any money received needs a method.
- **`sale_items`**: sale, product, quantity > 0, unit_price, mrp? (snapshot), discount, line_total
  (>= 0, which also prevents a discount above the gross), `unit_cost?` and `cogs_amount?`
  (both set or both `NULL`).
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
