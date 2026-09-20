# Business Rules

These rules are approved and binding for every later phase. Each rule has an ID so that tests, code
comments and reviews can refer to it. If a rule must change, change it here first.

Items marked **(open)** are deliberately undecided and will be settled in the phase named.

## L. Stock ledger

- **L1.** Stock is derived, never stored as an editable number:
  `Current Stock = Opening + Purchases - Sales + Sales Returns - Purchase Returns ± Adjustments`.
  It equals the sum of `inventory_transactions.qty_delta` for the product.
- **L2.** `inventory_transactions` is **insert-only**. Nothing updates or deletes its rows (a database
  trigger aborts any attempt). `inventory_service` is the **only** code allowed to write to it; a test fails
  if any other module references the table.
- **L3.** Transaction types and signs: `OPENING` (+), `PURCHASE` (+), `SALE` (-), `SALE_RETURN` (+),
  `PURCHASE_RETURN` (-), `ADJUSTMENT` (±, with a reason code), `REVERSAL` (undoes an earlier entry).
  The database enforces the sign for each type.
- **L4.** Stock may not go negative (a future shop setting may allow it). The check happens inside the
  same database transaction as the write, after locking, so two simultaneous sales cannot both take the last unit.
- **L5.** Only product-level events change stock: opening stock, purchases, purchase returns, detailed
  sales, sales returns, and adjustments. **Quick Sales never change stock.**
- **L6.** A **return** is a real-world event: the original document and the return both remain valid and
  visible. A **void** corrects a data-entry mistake and is recorded with `REVERSAL` entries. A document that
  has returns cannot be voided until those returns are voided.

- **L7. Opening stock** (Phase 3). The starting balance of a product, recorded as one `OPENING` ledger row:
  - the quantity must be greater than zero, in whole numbers for units that cannot be split (pieces) and
    up to 3 decimals for units that can (kg, litre);
  - a product has **at most one** opening row, and it must be recorded **before any other stock movement**,
    so it is always the true starting balance. Later corrections are adjustments;
  - the date defaults to today (shop timezone), may be in the past, never in the future;
  - the product must be active;
  - an optional cost per unit becomes the product's average cost. **No cost means the cost stays unknown
    (`NULL`), never 0**;
  - it can be entered while creating the product (both happen in one transaction: if the opening stock is
    refused, the product is not created) or later from the product page.
- **L8. Negative stock.** `shops.allow_negative_stock` (default false) governs every *removal* of stock (an
  adjustment now; sales in Phase 7). Opening stock is never negative, whatever the setting.
- **L9. Stock status.** Out of Stock when stock is 0 or below; Low Stock when stock is above 0 and at or
  below the product's reorder level; otherwise In Stock.

## S. Sales modes

- **S1. Detailed Sale:** a product-wise bill with at least one line (product, quantity, selling price,
  discount). It decreases exact stock and stores a cost snapshot per line, so COGS and profit can be computed.
- **S2. Quick/Daily Sale:** a money-only entry (date, amount, payment). It has **no product lines**, never
  changes inventory, never produces product-level COGS, and its profit is shown as **"Not Available"**.
  Several may be entered per day, or one daily total.
- **S3.** Reports always distinguish **Detailed**, **Quick** and **Combined**. Combined totals are allowed
  for revenue and cash only. Product analytics and profit are never computed by mixing in Quick Sales.
- **S4.** Best sellers, slow movers, product performance and sales-by-category use Detailed Sales only,
  and state their coverage, e.g. "Detailed sales are 62% of sales in this period".
- **S5.** The UI must make the distinction plain:
  - The Quick Sale screen says: this records money only, stock will not reduce, and the same bill must not
    also be entered as a Detailed Sale.
  - Inventory and low-stock screens show a caution when Quick Sales in the last 30 days are above zero:
    displayed stock may be higher than the shelf because some sales were not entered product-wise.
  - The Stock Count feature is the way to reconcile.
- **S6.** A Quick Sale can be voided but not returned in the MVP (void it and enter a corrected one).
- **S7.** Exact stock is reliable **only** for products whose movements were recorded at product level.

## R. Returns

- **R1.** Formal returns reference the original sale or purchase **line**. Cumulative returned quantity
  cannot exceed the original quantity.
- **R2.** A sales return adds stock back (`SALE_RETURN`) at the **original line's cost**. The refund is
  proportional to the original line total (discount included); the final return of a line refunds the exact
  remainder so no rounding difference is left. Refund modes: cash, UPI, or reduce the customer's khata
  (only if the sale had a customer).
- **R3.** A purchase return removes stock (`PURCHASE_RETURN`) at the original cost. It cannot exceed the
  quantity purchased, nor the stock currently on hand. Credit modes: cash, UPI, or supplier credit.
- **R4.** A return date cannot be before the original document or in the future.
- **R5.** Returns adjust average cost, and profit uses revenue and COGS **net of returns**. Purchase
  reports show net purchases.
- **R6.** An **unbilled** return, or any stock correction, is an `ADJUSTMENT` with an explicit reason code
  (see A1). It is never recorded as a generic adjustment when a specific reason exists.

## A. Adjustments and stock count

- **A1.** Adjustment reason codes: `CUSTOMER_RETURN_NO_BILL`, `COUNT_CORRECTION`, `DAMAGED`, `EXPIRED`,
  `LOST`, `OTHER`. A reason code is mandatory; `OTHER` also requires a note.
- **A2.** The stock-count screen takes the physically counted quantity and posts the difference as an
  `ADJUSTMENT` with reason `COUNT_CORRECTION`. Nothing overwrites stock.

## C. Costing and missing cost

- **C1.** Cost method is **moving weighted average**. On a purchase:
  `new average = (stock × current average + quantity × purchase cost) / (stock + quantity)`.
- **C2.** Each sale line stores `unit_cost` and `cogs_amount` **at the time of sale**. Later cost changes
  never rewrite past profit.
- **C3.** **Missing cost stays `NULL`, never `0`.** This applies to `products.purchase_price`,
  `products.avg_cost`, ledger `unit_cost` and sale-line cost. If the average cost is unknown (for example
  opening stock entered without a cost), the sale line's cost is `NULL`, and any profit that depends on it is
  flagged "incomplete: cost missing on N lines". Unknown cost is never treated as free.
- **C4.** Returns reverse at the original cost, and both sales and purchase returns recompute the
  average. **(open, Phase 5):** exact handling when known-cost and unknown-cost stock are on hand together.
- **C5.** Stock value = stock × average cost (not selling price), with the same missing-cost flag.
- **C6.** Purchases are not expenses. Buying stock does not reduce profit until that stock is sold.

## SP. Suppliers (Phase 4)

- **SP1.** A supplier belongs to one shop and is the same for every business type. Only the **name** is
  required. Phone, alternate phone, email, address, GSTIN and notes are optional.
- **SP2. Lenient validation, consistent storage.** Phone numbers may be typed any way (spaces, dashes, brackets,
  `+91`, Devanagari digits) and are stored compactly; 6 to 15 digits are accepted. Email must look like an
  address and is stored lower-case. GSTIN must have the 15-character structure and is stored upper-case; its
  check character is deliberately **not** verified, so a genuine number is never rejected. Every invalid field
  is reported at once. Names are not restricted: any script, punctuation and digits are fine.
- **SP3. Duplicates warn, they never block.** Two real suppliers can share a name, a phone or even a GSTIN
  (branches). A likely duplicate (same name ignoring case, same phone, same GSTIN, within the shop) is saved
  with a warning. On edit only the details that changed are re-checked. Other shops are never consulted.
  Known limit: `9876543210` and `+919876543210` are stored differently and are not recognised as the same phone.
- **SP4.** Suppliers are **never deleted**, only deactivated. An inactive supplier is hidden from default lists
  and pickers, cannot be newly chosen as a product's default supplier, keeps its products and history, can
  still be edited, and can be reactivated. Repeating activate or deactivate is harmless.
- **SP5.** A product may have **no** default supplier or one, and one supplier may supply many products. The
  supplier must belong to the same shop (enforced by the database and the service). A product that already uses
  a supplier which later becomes inactive keeps the link and can still be edited.
- **SP6.** Every create, update, activate and deactivate is written to the audit log with before and after values;
  an update that changes nothing writes nothing.
- **SP7.** Purchases, purchase returns, supplier payments and a supplier ledger belong to later phases. A
  supplier's "products" today means products that name it as their default supplier.

## B. Business types (Phase 3 extension)

- **B1.** The product is a **multi-business small-shop platform**. Grocery / Kirana is one business type. Types:
  `GROCERY`, `GENERAL_STORE`, `SWEET_SHOP`, `BAKERY`, `DAIRY`, `FRUIT`, `VEGETABLE`, `MEAT_FOOD`, `GARMENTS`,
  `FOOTWEAR`, `COSMETICS`, `ELECTRONICS`, `HARDWARE`, `STATIONERY`, `OTHER`. A type is a database row, so
  new ones can be added without changing code or schema.
- **B2.** Every shop has exactly one business type, chosen explicitly (there is no silent default). The
  owner can change it at any time; changing it changes only *suggestions*.
- **B3. No lock-in.** A business type provides defaults and suggestions, never restrictions. Any shop may create
  any category, any product and use any unit. A grocery shop may sell flowers; a fruit shop may sell bottled water.
- **B4. One engine.** Products, units, prices, and the inventory ledger are identical for every business type.
  There is no per-type stock logic and no separate inventory system for any business. Current stock is always
  derived from `inventory_transactions`.
- **B5. Units decide fractions.** Whether a quantity may be fractional depends only on its unit: kg, litre,
  dozen and metre allow it; piece, gram, millilitre, packet, box, pair, bottle and tray do not. Examples that
  must work: 25.5 kg potatoes, 8.5 kg apples, 5 kg gulab jamun, 100 pieces samosa, 37.25 m cloth, 20 T-shirts.
- **B6.** What a type suggests (categories, unit order, later labels and templates) lives only in
  `business_type_service`. Product, inventory, purchase, sales and report logic must not read the type.
- **B7. Specialised modules are future and optional** (recipes/production, wastage, size and colour variants,
  IMEI/serial numbers). They reuse the core and write stock only through `inventory_service`.
- **B8.** Online ordering, when built, is generic for every type and reuses products, customers, pricing and
  inventory (see O1, O2).

## PR. Products (Phase 3)

- **PR1.** SKU is required, trimmed and stored upper-case (`rice-5kg` and `RICE-5KG` are the same SKU),
  and unique within a shop. The same SKU may exist in different shops. Barcode is optional (blank counts as
  none), unique within a shop when present, and may repeat across shops.
- **PR2.** Products are **never deleted**, only deactivated. An inactive product is hidden from the default
  lists, cannot be given new stock, keeps its history and its SKU/barcode, can still be edited, and can be
  activated again. Deactivating twice is harmless.
- **PR3.** Purchase price and MRP are optional; when left empty they are stored as unknown, not 0. Selling
  price and reorder level are required (reorder level defaults to 0).
- **PR4.** `avg_cost` is calculated, never typed in: it is set by the opening cost now and maintained by the
  costing service from Phase 5. `current_stock` is not a field at all; sending it is an error.
- **PR5.** A unit cannot be changed once the product has any stock movement (it would change what the
  numbers mean). A reorder level must respect the unit (no fractions of a piece).
- **PR6.** A category is required. Inactive categories cannot be chosen for new products. Category names are
  unique per shop, ignoring case.
- **PR7.** Every create, update, activate/deactivate and opening-stock action writes an audit log entry with
  the values before and after. An update that changes nothing writes nothing.

## P. Pricing and MRP

- **P1.** A product has four separate values: **purchase price**, **average cost**, **selling price** and
  **MRP** (optional). MRP is not the selling price and does not affect cost.
- **P2.** MRP validation is **configurable per shop**, not hard-coded: when a selling price (on the product
  or on a sale line) exceeds MRP, the shop setting decides whether to **warn** or **block**. Selling above
  the printed MRP on packaged goods is generally not allowed in India, which is why the check exists.
  The setting is `shops.mrp_validation_mode` (`WARN` or `BLOCK`). The database does not enforce
  `selling price <= MRP`, because in `WARN` mode a higher price is allowed.
  **Decided in Phase 3:** the default for a new shop is `WARN` (the price is saved and the user is shown a
  warning), because a stale MRP should not stop a shopkeeper from saving a product. A shop can be set to
  `BLOCK`. In `BLOCK` mode the check applies only when the selling price or MRP is being changed. Changing
  the setting has no screen yet; it is a shop setting read by the API.
- **P3.** The MRP in force is copied onto each sale line as a snapshot.

## K. Khata (customer credit)

- **K1.** The customer ledger is **insert-only** (enforced by a database trigger) and `khata_service` is
  its only writer. Outstanding balance
  is the sum of entries (positive = the customer owes the shop); it is never stored.
- **K2.** Entry types: `OPENING_BALANCE`, `CREDIT_SALE`, `PAYMENT`, `RETURN_CREDIT`, `ADJUSTMENT`, `REVERSAL`.
- **K3.** A credit sale needs a customer and `amount paid < total`; the unpaid part posts a `CREDIT_SALE`.
  A fully paid sale posts nothing to khata. Quick Sales may also be on credit.
- **K4.** A payment received posts a `PAYMENT` (cash or UPI). It reduces the running balance and is not
  tied to specific bills. Paying more than is owed needs confirmation and is kept as an advance.
- **K5.** An existing paper-notebook balance is entered once as `OPENING_BALANCE`.
- **K6.** A customer's history is the ledger merged with links to the originating sale, quick sale or return.
- **K7.** Customers with entries are deactivated, never deleted. Phone numbers are unique per shop.
  Collect only the personal data that is needed.

## F. Profit

- **F1.** **Detailed gross profit** = detailed revenue - detailed COGS, both net of returns.
- **F2.** **Quick Sales contribute revenue only.** Their profit is "Not Available". No number, and no
  assumed margin, is shown for them.
- **F3.** **Net profit estimate** = gross profit - expenses. It is shown as a single number **only** when
  every sale in the period is a Detailed Sale and every cost is known. Otherwise the report shows the
  components (detailed gross profit and its coverage, Quick Sales revenue, expenses) and says why there
  is no single figure.
- **F4.** Profit is always labelled an **estimate**; real accounting may need further adjustments.

## V. Validation and integrity

- Reject duplicate product SKUs, barcodes and sales invoice numbers; purchase invoice numbers are unique per supplier.
- Reject zero or negative quantities, negative prices, and discounts larger than the line amount.
- Reject invalid or future dates, and decimal quantities for units that do not allow them.
- Reject missing or inactive products, suppliers and customers.
- Reject overselling (see L4).
- Repeated submits (double taps, retries) are absorbed with idempotency keys.
- Invoice numbers come from the document-sequence service, per shop and financial year.

## E. Editing and deleting

- **E1.** Posted documents and ledger rows are never edited or deleted in place. An **edit** is a void plus a
  new linked document, done in one transaction. A void requires a reason.
- **E2.** Every change is written to the audit log. Products, suppliers and customers with history are
  deactivated rather than deleted.

## X. Data export

- **X1.** Exports cover Products, Inventory (stock summary and ledger), Purchases and returns, Sales
  (detailed lines and quick sales, each row marked with its sale mode) and returns, Expenses, and
  Customers with the khata ledger.
- **X2.** Formats are CSV (UTF-8 with BOM so Excel shows Hindi text and ₹ correctly) and XLSX. Voided rows are
  included with a status column. Dates are ISO in CSV and real date cells in XLSX.
- **X3.** Cells starting with `=`, `+`, `-` or `@` are neutralised to prevent spreadsheet-formula injection.
- **X4.** Exports are scoped to the shop and available to the owner role. Responses are marked `no-store`.
- **X5.** *(Implemented in Phase 3.)* Formula injection: text starting with `=`, `+`, `-`, `@`, tab or carriage
  return is prefixed with an apostrophe in CSV and XLSX so a spreadsheet shows it as text. Real numbers,
  including negative ones, are not changed. Amounts are written from exact decimals. Money columns use
  `#,##0.00`, quantity columns `#,##0.000`, dates are real Excel date cells shown as `dd/mm/yyyy`; recorded-at
  times are converted to the shop's timezone. CSV dates are ISO. Files are named like `products_2026-09-20.csv`.
- **X6.** Available now: Products, Inventory (current stock) and Inventory history (the ledger with running
  balance). The engine is generic; each later module supplies its own columns and rows.

## T. Tenancy and security

- **T1.** Every query is scoped by `shop_id`; one shop must never read or write another shop's data.
- **T2.** Secrets come from the environment, are never committed, and never appear in the frontend.
- **T3.** Passwords are hashed with a modern algorithm, login attempts are rate-limited, and
  sensitive actions are audited.

## Architecture-ready, not in the MVP

Barcode scanner integrations and UPI/payment gateways: the schema carries a nullable barcode and payment
reference fields, and the `UPI` payment method exists, but no integrations are built. Also out of scope:
GST invoicing, payment reminders, multi-shop accounts, subscriptions, WhatsApp notifications, and the AI assistant.

## O. Online ordering (future, not in the MVP)

- **O1.** A future customer storefront, cart, online orders (COD/UPI), delivery and order history must reuse
  the same products, customers, pricing and inventory as in-store sales, and must work for every business
  type (order stages: accepted, preparing, ready, out for delivery, delivered).
- **O2.** There is **no second inventory system**. Stock remains the sum of `inventory_transactions`. An
  accepted online order is fulfilled by creating a normal Detailed Sale, so stock, cost snapshots, MRP checks,
  returns and reports all behave exactly as for a shop sale.

## Where each rule is enforced

Some rules are guaranteed by the database itself (they hold even if application code has a bug); others
need business logic in services, which arrive in later phases.

| Enforced by the database now (Phase 2) | Enforced by services later |
|---|---|
| L2 insert-only ledgers (triggers) | L4 no negative stock, and locking |
| L3 sign matches transaction type | L6 return and void behaviour |
| A1 adjustments need a reason code; `OTHER` needs a note | R1-R5 return caps, proportional refunds, dates |
| S2 quick sales have no product or cost columns | S5 warnings shown to the user |
| K3 PAID/CREDIT shape (customer required for credit, paid < total) | K2-K6 khata entries and balances |
| C3 cost and COGS are both set or both `NULL` | C1, C2 average cost and cost snapshots |
| V unique SKU/barcode/invoice/phone, non-negative prices, positive quantities, valid enum values, non-blank required text | V decimal quantities per unit, active parents, dates, idempotency |
| T1 a row cannot reference another shop's row (composite foreign keys) | T1 every query filters by shop; authorization |
| E1 void needs a reason; a row is reversed once | E1 edit = void + new document; audit entries |
| | P2 MRP warn/block |
| | F1-F4 profit, X1-X4 exports |

## API conventions (Phase 3)

- Money and quantities are **strings** in JSON (`"250.00"`, `"2.500"`). A JSON number with a fraction is
  refused, so no client can send a float. Money allows 2 decimals, quantities 3; neither may be negative.
  Responses always show money with 2 decimals and quantities with 3.
- Unknown fields are errors, not ignored.
- Errors: **404** for a missing thing and for another shop's data (the two look identical); **409** for a
  conflict such as a duplicate SKU or a second opening stock; **422** for invalid input or a broken rule.
  Field problems name the field.
- Every query is scoped to the caller's shop. There is no endpoint that changes or deletes a ledger row.
