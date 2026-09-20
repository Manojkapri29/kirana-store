# Database

> **Planned design. Nothing here is implemented yet.** The database, models and migrations are built in
> Phase 2; entities are added to the migrations as their phases arrive.

## Principles

1. **Ledgers, not counters.** Stock and customer balances are derived from insert-only ledger tables.
   There is no editable `current_stock` or `balance` column.
2. **Nothing disappears.** Wrong entries are voided with reversal rows; documents are never hard-deleted.
3. **Snapshots on documents.** Prices, MRP and cost at the moment of a sale are copied onto the line, so
   later price changes never rewrite history.
4. **Every table has `shop_id`.** Single shop in the MVP, but multi-shop needs no schema rewrite.
5. **Portable SQL.** The same code and migrations run on SQLite and PostgreSQL.

## Conventions

| Topic | Decision |
|---|---|
| Money | Stored as **integer paise**. A `Money` column type exposes Python `Decimal` rupees. Sums are exact on every database |
| Quantity | Stored as **integer thousandths** (1 kg = 1000). A `Quantity` type exposes `Decimal` with 3 decimals, so loose items like 250 g work |
| Rounding | Line totals and refunds are computed once in Python with `ROUND_HALF_UP` and stored; SQL only adds stored values |
| Business dates | Plain `DATE` in the shop's timezone (default `Asia/Kolkata`) |
| Timestamps | UTC, via a `UTCDateTime` type (SQLite drops timezone info otherwise) |
| Cost unknown | `NULL`, never `0` |
| Audit payloads | Generic `JSON`, not `JSONB` |
| Invoice numbers | A `document_sequences` table incremented in the same transaction (no database sequences) |

## Entities

`*` marks the phase in which the table is first created.

**Tenancy and users (Phase 2)**
- `shops`: name, phone, address, GSTIN (optional), UPI ID (optional), timezone, language, settings such as
  `allow_negative_stock` (default off) and the MRP validation mode.
- `users`: shop, email (unique), optional phone, password hash, role (`owner`/`staff`), active flag.

**Catalogue (Phase 3)**
- `categories`: shop, name (unique per shop).
- `units`: code, name, whether decimals are allowed (global, seeded: pcs, kg, g, L, ml, dozen...).
- `products`: shop, SKU (unique per shop), name, brand, category, unit, default supplier, reorder level,
  **MRP** (optional), selling price, purchase price, **average cost** (cached, rebuildable from the
  ledger), optional unique barcode, active flag. There are **no stock columns**.

**Stock (Phase 3, extended in 5 to 10)**
- `inventory_transactions`: the insert-only ledger.
  `txn_type`: `OPENING`, `PURCHASE`, `SALE`, `SALE_RETURN`, `PURCHASE_RETURN`, `ADJUSTMENT`, `REVERSAL`.
  Also: product, date, signed `qty_delta`, `unit_cost` (nullable), source document type and id,
  `reverses_txn_id`, `reason_code`, note, created by/at.
  - `CHECK` on sign by type: `OPENING`/`PURCHASE`/`SALE_RETURN` positive; `SALE`/`PURCHASE_RETURN`
    negative; `ADJUSTMENT`/`REVERSAL` non-zero.
  - `UNIQUE(source_type, source_id, txn_type)`: a document line cannot post twice.
  - `UNIQUE(reverses_txn_id)` where present: an entry is reversed at most once.
  - Index on `(shop_id, product_id, txn_date)`.
  - Adjustment `reason_code`: `CUSTOMER_RETURN_NO_BILL`, `COUNT_CORRECTION`, `DAMAGED`, `EXPIRED`,
    `LOST`, `OTHER` (`OTHER` requires a note).

**Suppliers and purchases (Phases 4 and 5, returns in 9)**
- `suppliers`: shop, name, phone, address, GSTIN (optional), active flag.
- `purchases`: supplier, supplier invoice number (unique per supplier), date, total, amount paid,
  payment method/reference, status (`posted`/`void`), `replaces_id`, void reason.
- `purchase_items`: product, quantity > 0, unit cost >= 0, line total.
- `purchase_returns` and `purchase_return_items`: reference the original purchase line; credit mode
  (cash, UPI, supplier credit).

**Customers and khata (Phase 6)**
- `customers`: shop, name, optional phone (unique per shop when present), address, notes, active flag.
- `customer_ledger`: insert-only. `entry_type`: `OPENING_BALANCE`, `CREDIT_SALE`, `PAYMENT`,
  `RETURN_CREDIT`, `ADJUSTMENT`, `REVERSAL`. Signed `amount_delta` (positive = customer owes more),
  payment method/reference, source document, `reverses_entry_id`, note.

**Sales (Phases 7 to 9)**
- `sales` (Detailed): invoice number (unique per shop), date, optional customer, total,
  payment type (`PAID`/`CREDIT`), amount paid, payment method/reference, status, `replaces_id`, void reason.
  `CHECK`: `PAID` means paid = total; `CREDIT` requires a customer and paid < total.
- `sale_items`: product, quantity > 0, unit price, MRP snapshot, discount, line total, **unit cost and
  COGS snapshot** (nullable when cost is unknown).
- `quick_sales` (Phase 8): date, total > 0, optional customer, note, payment fields, status.
  **It has no product columns and never touches the stock ledger.**
- `sale_returns` and `sale_return_items` (Phase 9): reference the original sale line; refund mode
  (cash, UPI, khata); cost copied from the original line.

**Expenses (Phase 11)**
- `expense_categories`, `expenses`: date, category, description, amount > 0, payment method, status.

**Cross-cutting**
- `document_sequences` (Phase 2): shop, document type, financial year, last number.
- `idempotency_keys`: makes repeated submits (double taps, retries) safe.
- `audit_log`: entity, action, before/after JSON, user, timestamp.

## Always derived, never stored

- Current stock, and the Opening / Purchased / Sold / Returned / Adjusted columns of the inventory screen
- Customer outstanding balance
- Returned quantity per sale or purchase line
- Detailed versus Quick totals
- A supplier's "products supplied"

The single deliberate cache is `products.average_cost`, updated in the same transaction as each stock
movement and rebuildable from the ledger.

## Editing and deleting

Ledger rows and posted documents are never updated in place. A wrong entry is **voided**, which writes
`REVERSAL` rows and sets `status = 'void'` with a reason. An **edit** is a void plus a new document linked
by `replaces_id`, in one transaction. Business events such as customer returns are separate documents,
not voids. See [BUSINESS_RULES.md](BUSINESS_RULES.md).

## SQLite now, PostgreSQL later

No MVP feature requires PostgreSQL. The known SQLite differences and how they are handled:

| SQLite behaviour | Handling (works on both databases) |
|---|---|
| No true `NUMERIC`; decimals become floats | Integer paise and integer thousandths (see Conventions) |
| No `SELECT ... FOR UPDATE` | Write transactions open with `BEGIN IMMEDIATE` (one writer at a time). Services call `.with_for_update()`, which SQLite ignores and PostgreSQL honours |
| Limited `ALTER TABLE` | Alembic batch mode |
| Foreign keys off by default | `PRAGMA foreign_keys=ON` on every connection; WAL mode and a busy timeout |
| Timestamps lose timezone | `UTCDateTime` type |
| Trigger syntax differs | Ledger-protection triggers are added per dialect in the migration |

Portability rules for application code: no dialect-specific SQL (`FILTER`, `JSONB`, `ILIKE`, native
sequences); use SQLAlchemy expressions such as `SUM(CASE ...)`; select the database only through
`KIRANA_DATABASE_URL`.

PostgreSQL becomes necessary when many shops write concurrently in a hosted deployment (SQLite allows
one writer at a time), when Row-Level Security is wanted, or when managed backups and replicas are needed.
Migration steps: point `KIRANA_DATABASE_URL` at PostgreSQL, run Alembic, run the full test suite
(including the concurrency test) on PostgreSQL, and move data with an export/import script.
This is verified at the checkpoint after Phase 10 and again in Phase 15.
