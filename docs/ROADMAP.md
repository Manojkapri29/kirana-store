# Roadmap

The project is built in small, testable phases. Each phase ends with passing tests and a working
demo, and the next phase starts only after review. Later phases build on the ones before them, so the
order matters: for example, the stock ledger exists (Phase 3) before purchases and sales use it.

| # | Phase | Status |
|---|---|---|
| 1 | Project setup and foundation | Done |
| 2 | Database/models + SQLite + Alembic | Done |
| 3 | Products + inventory ledger core + export framework | Done |
| 4 | Suppliers | Done |
| 5 | Purchases + weighted average cost | Done |
| 6 | Customers + Khata | Done |
| 7 | Detailed Sales | Done |
| 8 | Quick Sales, barcode scanning, price intelligence, offers and coupons, plans | Done |
| 9 | Returns, smart error recovery, smart photo capture | Done |
| 10 | AI business assistant, document intelligence, smart automation | Done, awaiting review |
| — | Inventory views + adjustments screen + stock count + reliability indicators (was Phase 10) | Deferred, not scheduled |
| ✔ | **PostgreSQL checkpoint** (after the inventory views phase) | Planned |
| 11 | Expenses | Planned |
| 12 | Dashboard | Planned |
| 13 | Reports | Planned |
| 14 | Authentication | Planned |
| 15 | Testing, validation and PostgreSQL gate | Planned |
| 16 | Deployment preparation | Planned |

## Phase details

### Phase 1: Project setup and foundation
FastAPI app with a `GET /health` endpoint, environment-based configuration, and a router structure.
React + Vite + TypeScript + Tailwind shell with a responsive sidebar, dashboard placeholder,
English/Hindi switching, and an API client layer. Documentation, `.gitignore`, `.env.example` files.
**Done when:** both apps start, `/health` works, the frontend builds and type-checks, tests and lint pass.

### Phase 2: Database/models + SQLite + Alembic
Database engine driven by `KIRANA_DATABASE_URL` with SQLite settings (foreign keys on, WAL, busy timeout,
explicit transactions, `BEGIN IMMEDIATE` for writes), `read_session` and `write_transaction`, the `Money`,
`Quantity` and UTC timestamp types, all 23 tables (including the insert-only stock and customer ledgers and
the money-only `quick_sales`), composite tenant foreign keys, the first Alembic migration (`0001`), seeded
units, a development seed script, and the `inventory_service` / `khata_service` placeholders.
**Done when:** migrations apply and roll back on an empty database and match the models; money and quantity
values round-trip exactly; ledgers cannot be updated or deleted; a row cannot reference another shop; the
migration renders valid PostgreSQL DDL. No services, endpoints or screens use the database yet.

### Phase 3: Products + inventory ledger core + export framework
The temporary development context (current shop and user until Phase 14); categories and products (create,
edit, search, filter, activate/deactivate, MRP, barcode field) with the JSON API and shop isolation;
`inventory_service` as the only ledger writer with opening stock, adjustments (service level), derived
current stock, the inventory list with In/Low/Out of stock, and transaction history with a running balance;
the audit log for product changes; the reusable CSV/XLSX export engine with Products, Inventory and
Inventory-history exports; and the Products and Inventory screens. Default `mrp_validation_mode` decided:
`WARN`.
**Done when:** a product created with opening stock 20 reads 20 from the ledger; two shops can hold the same
SKU/barcode but one shop cannot repeat them; a shop cannot see or change another's products or stock;
exports open cleanly in Excel with formulas neutralised.
*Not in this phase:* screens and API for adjustments and stock count (Phase 10).

**Phase 3 extension: a generic platform.** The product is a multi-business small-shop management SaaS, with
Grocery / Kirana as one business type. Added: `business_types` and `shops.business_type` (migration `0002`,
existing shops become GROCERY), 15 business types, four more units (metre, pair, bottle, tray), suggested
categories and units per type (defaults only, never restrictions), `GET/PATCH /shop`, `/business-types`,
`/shop/template`, a Settings screen, and generic UI wording. **Done when:** products and inventory behave
identically for every business type and every unit, two shops can have different types, and the core code
contains nothing business-specific.

### Phase 4: Suppliers
Generic supplier management for every business type: add, list, search (name, phone, email, GSTIN), view,
edit, and activate/deactivate suppliers (never deleted). Optional details: phone, alternate phone, email,
address, GSTIN, notes. A product may name an optional **default supplier**; one supplier may supply many
products, and a supplier's screen lists them. Migration `0003` adds the new columns and two indexes. The
supplier detail page is laid out for the sections that later phases fill in (purchase history, purchase
returns, payments and outstanding); none of those exist yet.
**Done when:** suppliers can be created, found, edited and deactivated; products can have or not have a default
supplier; an inactive supplier cannot be newly chosen but existing links survive; a shop can never see or use
another shop's suppliers.
*Not in this phase:* purchases, supplier payments or ledger, and a supplier export (the export engine can
add one at any time).

### Phase 5: Purchases + weighted average cost
Supplier → purchase → purchase items → inventory ledger → moving weighted average cost. Purchases are drafted,
posted (numbered `PUR/2026-27/0001`, stock added, average cost updated, all in one transaction) and voided
(stock reversed, average rebuilt from history) or corrected as a new linked draft. Line discounts are
supported and costing uses the net line total. Screens: purchase list with filters, entry form with product
search (barcode friendly), detail page with the stock and cost effect, purchase history on the supplier page,
and links from a product's stock history. CSV/XLSX exports of purchases, purchase items and one purchase.
Migration `0004`. New services: `purchase_service`, `costing_service`, `numbering_service`.
**Done when:** 100 at ₹20 then 50 at ₹30 gives 150 units at ₹23.33; opening 20 + purchase 30 = 50 and voiding the
purchase returns stock to 20; posting twice is refused; a failed posting leaves no stock or number behind.
*Not in this phase:* supplier payments and the supplier ledger (the `amount_paid` columns stay unused), purchase
returns (Phase 9), a purchase-order or goods-receipt step, and unit conversion between purchase and stock units.

### Phase 6: Customers + Khata
Customer management (create, edit, search, balance and status filters, activate/deactivate; never deleted) and
the khata: `khata_service` as the only reader and writer of the insert-only customer ledger, with opening
balance, payments (an overpayment becomes an advance), controlled adjustments, reversals, and the service
methods for credit sales and return credit that Phases 7 and 9 will call. Outstanding is `SUM(amount_delta)`,
never stored. Customer list with balances, detail page with the ledger and running balance, CSV/XLSX exports of
the customer list and of one customer's khata. Migration `0005` (customer email, name index).
**Done when:** outstanding balance always equals the sum of ledger entries; an entry is corrected only by a
linked reversal or an adjustment; another shop's customers and ledger are unreachable.
*Not in this phase:* Detailed or Quick Sales, Sales Returns (the service methods exist, nothing calls them yet),
payment allocation to specific bills, payment reminders, and any payment gateway.

### Phase 7: Detailed Sales
Product-wise billing: a cart (draft) that is posted once. Posting numbers the sale (`INV/2026-27/0001`), takes
the stock out through `inventory_service` (overselling refused, checked under lock), records each line's cost of
goods from the weighted average (`NULL` when unknown, so profit is "Not available" rather than a wrong number),
and charges any unpaid part to the customer's khata through `khata_service`; all in one transaction. Paid or
credit sales (cash, UPI, other), line and bill discounts, MRP warn or block, void by reversal, and a corrected
copy. Screens: sale list with filters, a billing screen with product and barcode search and a payment panel
(totals come from the server's `/sales/calculate`), and a detail page with profit and stock effect. CSV/XLSX
exports of sales, sold items and one sale. Migration `0006`. New services: `sale_service`, `sale_calculation`.
**Done when:** 50 - 5 = 45; selling 46 is rejected; two simultaneous sales of the last unit cannot both succeed;
a failed posting leaves no stock, number or khata entry behind; unknown cost never becomes a zero profit.
*Not in this phase:* Quick Sales (Phase 8), Sales Returns (Phase 9, the void guard for returns is already in
place), tax or GST invoicing (nothing in the schema supports it), overpayment on a bill, and a printed invoice.

### Phase 8: Quick Sales, scanning, price intelligence, offers, plans
Seven pieces, all on the existing architecture (routers, services, ledgers, idempotent-safe transactions):

* **Quick/Daily Sales.** Money-only entries with the same Draft, Posted, Void lifecycle as a bill, an optional
  customer, payment method, part payment and credit (through `khata_service`), one transaction-level discount,
  numbering `QS/2026-27/0001`, and CSV/XLSX export. No product, no stock movement, no cost: profit is
  "Not Available". Migration `0008`.
* **Barcode / product lookup.** One reusable lookup: exact barcode (UPC-A and EAN-13 twins), exact SKU, exact
  normalised name, then search. Shop-scoped, never creates a product, "Barcode not found" for an unknown code.
  The billing screen uses it for scanning (USB and Bluetooth scanners type like keyboards), with stock and
  active checks and a product card. Service `product_lookup_service`.
* **Price intelligence.** Outside prices as information only, through a provider interface (Open Food Facts for
  identity, Open Prices for crowd-sourced shelf prices, UPCitemdb when a key is configured). EXACT vs POSSIBLE
  matches with a confidence, a typed city/state/market (no GPS) that is either applied or plainly reported as not
  applied, a per-shop cache with stale fallback, a monthly plan limit, and a saved history. It never changes a
  price or a cost and never blocks billing. Migration `0010`. Services `price_providers`, `price_comparison_service`.
* **Offers, discounts and coupons.** One generic engine (`promotion_service`, `promotion_calculation`): percentage,
  amount, offer price and buy-X-get-Y, on the whole bill, chosen products or categories, with minimums, a maximum,
  usage limits, coupon codes, first-order and customer-specific audiences, priority and explicit stacking. The
  server is authoritative; what each sale got is frozen in `sale_promotions`. Migration `0009`.
* **Plans and limits.** `plans`, `plan_features`, `shop_subscriptions`, `subscription_usage`; an entitlement
  service the backend enforces (the frontend only hides); a read-only plan screen with "Contact admin". No
  payments. Migration `0007`.
* **Reports.** Detailed, Quick and Combined; Gross, Discount and Net; discount analytics by offer, coupon and day.
* **Exports.** Quick sales, offers, offer and coupon usage, price history, sales summary, discount report.

**Done when:** a quick sale never touches stock; a scanned unknown code adds nothing; an outside price never
changes a price; editing or ending an offer never changes a posted invoice; a usage limit of one cannot be used
twice by simultaneous sales; a plan without a feature is refused by the server; every outside failure leaves
billing untouched. *Not in this phase:* online ordering, a payment gateway, real subscription billing, loyalty,
AI agents, deployment.

### Phase 9: Returns, smart error recovery, smart photo capture
Three pieces on the existing architecture (routers, services, ledgers, one idempotency mechanism):

* **Returns.** Sales returns and purchase returns that reference the original line, with refund modes (cash, UPI,
  khata credit) and credit modes (cash, UPI, supplier credit), quantity caps across all live returns of a line, a
  refund worked out by the server in proportion to what was paid (net of offers and any bill discount, cumulative
  rounding so the last return of a line refunds the exact remainder), and the original line's cost. A sales return
  is a costed receipt (`SALE_RETURN`), a purchase return an issue (`PURCHASE_RETURN`, checked under product locks).
  Void by reversal with a reason; a sale or purchase with a live return cannot be voided. Numbers `SRT/...` and
  `PRT/...` (migration `0011`). Reports show returns and net-after-returns, and profit is adjusted only when the
  cost is known. Screens: Returns list (sales and purchase tabs, CSV/XLSX), "Return items" from a posted sale or
  purchase with a server-calculated preview, return detail with void, and the returns made against a document.
* **Smart error recovery.** One error format and one place that builds it (`api/errors.py`): `success`,
  `error_code`, `message`, `category`, `retryable`, `reference_id`, plus the old `detail` so every screen keeps
  working. Unexpected failures give the user a plain message and a reference (`ERR-YYYYMMDD-XXXXX`); the real
  cause goes, redacted, to the internal diagnostics log (`core/diagnostics.py`). One idempotency mechanism
  (`Idempotency-Key`) for creating and posting. Frontend: `describeError` decides what a person sees and can do,
  `ErrorNotice` shows it, `ErrorBoundary` catches crashes, reads are retried (only when transient) and writes never
  are, forms keep what was typed (`useFormBackup`) and retry with the same key. See BUSINESS_RULES `ER`.
* **Smart photo capture (foundation).** Optional. Take or choose a photo; the server validates the real content
  (magic bytes, type, size, dimensions); a provider interface for OCR or image analysis with no provider bundled, so
  the honest answer today is "Image analysis is not configured yet."; results are Detected or Suggested, never
  confirmed; a duplicate check before creating; a barcode goes through the Phase 8 lookup; creation only through an
  explicit "Confirm & Create Product"; the photo is kept only if the owner asks (private, per shop, one per product;
  migration `0012`, plan feature `image_intelligence`). A photo never changes stock, prices, orders, khata or money.
  See BUSINESS_RULES `IM`.

**Done when:** return quantity caps, proportional refunds, void rules and history preservation are tested; a
failed operation shows a reference and no internal detail; a repeated request never posts twice; a photo never
creates or changes anything without a confirmation.
*Not in this phase:* returns without an original document (use adjustments, Phase 10), a supplier ledger (supplier
credit is recorded on the return only), a bundled image-analysis provider, and the future photo uses below.
*Future, not built:* supplier invoice photo to purchase draft, handwritten stock list to adjustment draft, shelf
photo, damaged or expired product photo to a suggested adjustment reason. Each will produce a draft for review; an
image will never change stock by itself. Priority: Data Integrity > User Safety > Security > Correctness >
Recovery > Intelligence > Convenience.

### Phase 10: AI business assistant, document intelligence, smart automation
The AI is an assistant, never the source of truth and never a writer. Everything it says is read from the database by
existing reports and services; everything it proposes needs a person's confirmation and then runs an existing service.

* **Assistant.** "Ask your Business Assistant" (dashboard and its own page): sales, top and slow products, stock, Khata,
  purchases, profit where cost is known, discounts and offers, price comparison, unusual activity, reorder and purchase
  suggestions, offer ideas and a monthly summary, in English, Hinglish and Hindi. Twenty fixed read-only tools
  (`ai_tools`) call `sales_report_service`, `inventory_service`, `khata_service`, `analytics_service` and friends. No
  arbitrary SQL, no shop argument, no write. Dates are turned into ranges by the backend (`ai_dates`).
* **Confirmation system.** `ai_action_service`: the AI (or a document) proposes a structured action, the person sees an
  exact preview (Confirm / Edit / Cancel), and only Confirm runs `purchase_service.create_purchase` (a DRAFT),
  `inventory_service.record_adjustment` (with a reason code) or `promotion_service.create_promotion` (a DRAFT). Each step
  is audited.
* **Document intelligence.** A photographed supplier invoice becomes a purchase draft, a counted stock list an
  adjustment draft: read by a provider, every field validated, rows matched to products (Matched, Possible Match, New
  Product Candidate), reviewed and confirmed. Text on a document is data and is never followed. Product images keep
  using the Phase 9 flow ("Add from photo", Confirm & Create Product).
* **Provider abstraction.** One interface, one provider written (Anthropic's Messages API), none required. With none
  configured the assistant says "AI Assistant is not configured." and keeps answering its ready-made questions.
* **Usage and plans.** `ai_usage` records requests (feature, provider, model, tokens when given; never text); plan
  features `ai_assistant`, `ai_insights`, `ai_documents` and a monthly limit `max_ai_requests_per_month`. Migration `0013`.

**Done when:** every number in an answer comes from a service; no question, question wording or document text can make
the AI write, delete, post, refund or reprice; a confirmed action creates exactly what its preview showed; another
shop's data is unreachable; a provider failure leaves the rest of the app working.
*Not in this phase:* online orders (the application has no online ordering yet, so those answers say so and invent
nothing), automatic anything, a bundled provider key, more than one provider, seasonal-demand analysis, conversation
memory on the server, and the Inventory screens/stock-count phase that used to be numbered 10.

### PostgreSQL checkpoint (after the inventory views phase)
Run the whole test suite, including the concurrency test, against PostgreSQL and fix any dialect drift.
No PostgreSQL is installed before this point; the cheapest way to run it is decided then.

### Phase 11: Expenses
Expense categories and entries, today and monthly totals, and export.

### Phase 12: Dashboard
KPI cards and charts (Recharts), with sales trends split by mode and stock caveats.
**Done when:** dashboard numbers match a hand-calculated dataset.

### Phase 13: Reports
Sales and purchase reports (daily, weekly, monthly, custom range; Detailed/Quick/Combined),
inventory, product performance (detailed sales only), and the profit estimate under the profit rules.

### Phase 14: Authentication
Registration, login, token refresh, roles, and real shop scoping replacing the development context.
**Done when:** one shop can never read or write another shop's data.

### Phase 15: Testing, validation and PostgreSQL gate
Invariant and reconciliation tests over random transaction sequences, Hindi and mobile review,
and the full suite green on both SQLite and PostgreSQL.

### Phase 16: Deployment preparation
Containers, production configuration, backups, and a security review.

## Future specialised modules (not scheduled)

Optional add-ons that reuse the core (products, the inventory ledger, purchases, sales) and are enabled per shop,
often by business type:

- **Halwai / Bakery:** production, recipes, ingredients, finished goods, wastage.
- **Fruit / Vegetable:** wastage and spoilage tracking, variable purchase cost.
- **Garments / Footwear:** size and colour variants.
- **Electronics:** serial number / IMEI and warranty.
- **Per-shop custom units.**

## Explicitly out of MVP scope
Payment reminders, GST invoicing, UPI/payment-gateway integration, barcode scanner integrations,
multiple shops per account, subscription billing, WhatsApp notifications, **customer online ordering
(storefront, cart, COD/UPI orders, delivery)**, and the AI assistant. The architecture leaves room for each
(see [ARCHITECTURE.md](ARCHITECTURE.md) and [DATABASE.md](DATABASE.md#future-online-ordering)). Online
ordering will be generic for every business type and will reuse the same products, customers, pricing and
inventory; it will not get its own inventory.
