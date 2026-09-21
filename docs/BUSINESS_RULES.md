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
  Phase 8 implements the workflow: see section QS.
- **S7.** Exact stock is reliable **only** for products whose movements were recorded at product level.

## SL. Detailed Sales (Phase 7, implemented)

- **SL1. Lifecycle: DRAFT, POSTED, VOID.** A **draft** is a cart: customer (optional), date, notes, lines and an
  optional bill discount. It has no number and no payment, is freely editable, and **never affects stock,
  khata, revenue or cost**. **Posting** is the only step that does. A **posted** sale is never edited or
  deleted. **Void** is the only way out (there is no DELETE): it needs a reason and keeps the record and its
  number. A discarded draft is VOID too, with no number and no effects.
- **SL2. Posting is atomic and happens once.** In one database transaction: the payment is settled, the sale is
  numbered, each line takes its quantity out of the stock ledger (one negative `SALE` row per line, through
  `inventory_service`), each line stores the product's cost at that moment, any unpaid part is charged to the
  customer's khata (through `khata_service`), and the audit entry is written. If anything fails, nothing is kept:
  no stock, no number, no khata entry. The sale row is locked, so two requests posting the same draft cannot both
  succeed; the second gets a conflict.
- **SL3. Numbers** look like `INV/2026-27/0001`: per shop, per document type, per financial year, gapless,
  assigned at posting. Drafts have none. A voided sale keeps its number, which is never reused.
- **SL4. No overselling.** Stock is read from the ledger, never from a stored column. Posting is refused if any
  product's total quantity (across all its lines) is more than is on hand, unless the shop allows negative stock
  (L8). Every short line is reported at once, by position (`items.2.quantity`). The check is repeated under each
  product's lock (products are locked in ascending id order), so two simultaneous sales cannot both take the last
  unit. Selling 7 of 10 is allowed; 11 of 10 is refused and writes nothing; nothing ever goes negative.
- **SL5. Line rules.** The product must belong to the shop and be active; quantity is greater than zero (whole
  numbers for units that cannot be split); the price is zero or more and defaults to the product's selling
  price; a discount is an **amount**, not more than the line, so a line can reach zero but never go negative.
  Above the product's MRP the shop's setting decides: warn (the sale is saved and shown a warning) or block (P2).
  The MRP in force is copied onto the line (P3). Every bad field is reported at once with its line.
- **SL6. Arithmetic** lives in one place (`sale_calculation`), in whole paise, half up:
  `line gross = quantity x price`, `line total = gross - line discount`, `subtotal = sum of line totals`,
  `bill total = subtotal - bill discount`. The client never supplies totals (they are refused as unknown fields);
  the server recomputes them at every save and again when posting. The billing screen shows what
  `/sales/calculate` returns and does no money arithmetic of its own. **There is no tax**: nothing in the
  schema supports it yet, so it is not modelled.
- **SL7. Cost and profit.** A line's cost of goods sold is `round(quantity x product average cost)` using the
  average in force when the sale is posted (maintained by purchases, C1); a sale never changes the average. The
  snapshot is never rewritten. **Unknown cost stays `NULL`, never 0**, and then so does that line's profit.
  Sale gross profit = bill total - cost of goods, and is `NULL` unless **every** line's cost is known: a partly
  known cost never produces a partial profit. Example: 5 kg at an average of 23.33 costs 116.65. The bill
  discount reduces the sale's profit but is not spread over the lines, so a line's profit is before it.
- **SL8. Payment** is chosen when posting. **Paid in full** (the default) needs a method (cash, UPI or other).
  **Less than the total** makes a `CREDIT` sale: a customer is required, an active one, and the unpaid part is a
  `CREDIT_SALE` on their khata referencing the sale. Paying nothing now is allowed (all on credit, no method).
  **Paying more than the total is refused** (the sale model has no overpayment); the message says to take extra
  money as a payment on the customer's khata, where it becomes an advance (KH4). A fully paid sale never touches
  khata. The bill total must be greater than zero. A UPI reference is optional and the shop's UPI id is shown on
  the screen; there is no payment gateway.
- **SL9. Void of a posted sale** writes a `REVERSAL` ledger row per line (the goods go back), reverses the
  `CREDIT_SALE` if there was one (the document path is allowed to, KH9), and is refused while the sale has live
  returns (L6). It rebuilds each product's average cost from its history without the voided sale, so an
  emptied-and-restocked shelf is treated as if the sale had never happened.
- **SL10. Correcting a mistake:** void the sale, then "make a corrected copy": a new draft with the same
  customer, notes, bill discount and lines (prices as billed), linked through `replaces_id`. Once per sale.
- **SL11.** Every create, edit, post, void, discard and correction is audited with before and after values.
  Stock is never stored on a product or a sale; the stock ledger and the khata ledger are reached only through
  `inventory_service` and `khata_service` (architecture tests enforce it).
- **SL12.** Search covers invoice number, customer name or phone, and notes; filters cover status, payment type,
  customer and dates. Exports cover the sale list (with payment, cost of goods and profit, empty where unknown),
  every sold line, and one sale. Product history and a customer's khata link back to the invoice.

## R. Returns (Phase 9, implemented)

- **R1.** Formal returns reference the original sale or purchase **line**. The quantity returned across all live
  (not void) returns of a line cannot exceed the quantity on that line.
- **R2.** A sales return adds stock back (`SALE_RETURN`) at the **original line's cost** and rebuilds the average
  cost. The refund is the line's net revenue (line total less its offer share and its share of any bill
  discount) in proportion to the quantity; the refund is rounded cumulatively, so the last return of a line
  refunds the exact remainder and the refunds of a line never exceed what it earned. Refund modes: cash, UPI, or
  reduce the customer's khata (needs a customer on the sale).
- **R2a.** A cash or UPI refund cannot exceed the money actually received for that sale less what was already
  refunded that way. The rest can only go to the khata.
- **R3.** A purchase return removes stock (`PURCHASE_RETURN`) at the original cost. It cannot exceed the quantity
  purchased, the quantity still returnable, or the stock on hand (goods already sold cannot be sent back); the
  stock check is made under the product locks. Credit modes: cash, UPI, or supplier credit (recorded on the
  return; there is no supplier ledger yet).
- **R4.** A return date cannot be before the original document or in the future.
- **R5.** Profit and reports use revenue and cost net of returns; a return whose cost is unknown does not invent
  a profit. Purchase reports show net purchases.
- **R6.** An **unbilled** return, or any stock correction, is an `ADJUSTMENT` with an explicit reason code
  (see A1).
- **R7.** Void is a reversal with a reason. It puts the stock back the other way and takes back any khata credit;
  the return and its number stay. A sale or purchase that has a live return cannot be voided (void the return
  first). Nothing about a return edits or deletes the original document.
- **R8.** Creating a return accepts an idempotency key: repeating the same request (a double tap, a lost answer)
  returns the same return and never refunds twice; simultaneous full returns of one line cannot both succeed.

## A. Adjustments and stock count

- **A1.** Adjustment reason codes: `CUSTOMER_RETURN_NO_BILL`, `COUNT_CORRECTION`, `DAMAGED`, `EXPIRED`,
  `LOST`, `OTHER`. A reason code is mandatory; `OTHER` also requires a note.
- **A2.** The stock-count screen takes the physically counted quantity and posts the difference as an
  `ADJUSTMENT` with reason `COUNT_CORRECTION`. Nothing overwrites stock.

## C. Costing and missing cost

- **C1.** Cost method is **moving weighted average**. On a purchase:
  `new average = (stock × current average + value received) / (stock + quantity)`, where **value received is
  the net cost of the line: `round(quantity × price) − discount`**. A discount therefore lowers the cost of the
  goods. Averages are whole paise, rounded half up. The example 100 at ₹20 then 50 at ₹30 gives 150 units at
  ₹23.33. `products.avg_cost` is a cached value: it can always be rebuilt by replaying the ledger (see PU9).
  When **nothing is on hand** (stock zero or below) the new average is simply the incoming cost.
- **C2.** Each sale line stores `unit_cost` and `cogs_amount` **at the time of sale**. Later cost changes
  never rewrite past profit.
- **C3.** **Missing cost stays `NULL`, never `0`.** This applies to `products.purchase_price`,
  `products.avg_cost`, ledger `unit_cost` and sale-line cost. If the average cost is unknown (for example
  opening stock entered without a cost), the sale line's cost is `NULL`, and any profit that depends on it is
  flagged "incomplete: cost missing on N lines". Unknown cost is never treated as free.
- **C4.** Returns reverse at the original cost, and both sales and purchase returns recompute the
  average. **Known cost arriving on top of unknown-cost stock leaves the average unknown** (`NULL`): the old
  units' cost is not guessed. It stays unknown until the shelf empties, and then the next purchase sets it.
  (Decided in Phase 5.) Adjustments change stock but never the average.
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
- **SP7.** Purchases arrived in Phase 5 (see PU). Purchase returns and supplier payments and ledger belong to
  later phases. A supplier's "products" means products that name it as their default supplier; its purchase
  history lists its purchases.

## PU. Purchases (Phase 5)

- **PU1. Lifecycle: DRAFT, POSTED, VOID.** A **draft** is work in progress: no number, no stock or cost effect,
  freely editable (header and lines). **Posting** is the only step that changes stock. A **posted** purchase is
  never edited or deleted. **Void** is the only way out (there is no DELETE): it needs a reason and keeps the
  record and its number. A draft that is voided is "discarded" (it never had a number or stock effect).
- **PU2. Posting is atomic and happens once.** In one database transaction: the purchase gets its number, each
  line adds one positive `PURCHASE` row to the stock ledger, each product's average cost is updated, the
  stock/cost snapshot is stored on each line, and the audit entry is written. If anything fails nothing is
  kept (no stock, no number). The purchase row is locked, so two simultaneous posts cannot both succeed; the
  second gets a conflict. Posting an empty purchase is refused.
- **PU3. Numbers** look like `PUR/2026-27/0001`: per shop, per document type, per financial year (April to
  March), gapless (a rolled-back posting gives its number back), assigned at posting from the posting date in
  the shop's timezone. Drafts have no number. A voided purchase keeps its number, which is never reused.
- **PU4. Validation.** The supplier must belong to the shop and be active. Each product must belong to the shop
  and be active. Quantity must be greater than zero (fractions only for units that allow them); price is
  zero or more (zero allowed for free goods, never negative); a discount is an amount, not more than the line.
  The date cannot be in the future. The line unit is the product's own unit. Every bad field is reported at
  once, with the line it belongs to. Amounts travel as strings, never floats.
- **PU5. Line total** = `round_half_up(quantity × price) − discount`, in whole paise. The purchase total is the
  sum of the line totals.
- **PU6. Supplier invoice number** is optional. Among live (draft or posted) purchases it cannot repeat for the
  same supplier (case-insensitive). A voided purchase **releases** its invoice number, so a corrected copy can
  reuse it. Enforced by a partial unique index as well as the service.
- **PU7. Void of a posted purchase** writes a `REVERSAL` ledger row for each line and rebuilds the average cost
  of each product from its history **without** the voided purchase, so it leaves no trace in the average. It is
  **refused** if it would take stock below zero (some of that stock was already sold or removed), unless the
  shop allows negative stock. The check covers every line before anything is changed.
- **PU8. Correcting a mistake:** void the purchase, then "make a corrected copy": a new draft with the same
  supplier, invoice number, date, notes and lines, linked through `replaces_id`. A voided purchase can be
  corrected once.
- **PU9. The average is rebuildable.** `rebuild_average_cost` replays a product's ledger in the order it was
  recorded, leaving out movements that were reversed, and must reproduce the stored value; if the replayed
  stock differs from the ledger stock it stops instead of guessing. Later purchases keep their own historical
  snapshot (`stock_before`, `avg_cost_before/after`) even if an earlier purchase is voided.
- **PU10.** Stock is never stored on a product or a purchase. Only `inventory_service` touches the ledger and
  assigns `avg_cost`; purchase code goes through it (enforced by architecture tests).
- **PU11.** Supplier payments, the amount paid and the supplier ledger are **not** part of Phase 5. The
  `amount_paid` columns exist but nothing writes them.
- **PU12.** Every create, edit, item change, post, void, discard and correction is audited with before and
  after values.

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
  its only reader and writer. Outstanding balance
  is the sum of entries (positive = the customer owes the shop); it is never stored.
- **K2.** Entry types: `OPENING_BALANCE`, `CREDIT_SALE`, `PAYMENT`, `RETURN_CREDIT`, `ADJUSTMENT`, `REVERSAL`.
- **K3.** A credit sale needs a customer and `amount paid < total`; the unpaid part posts a `CREDIT_SALE`.
  A fully paid sale posts nothing to khata. Quick Sales may also be on credit.
- **K4.** A payment received posts a `PAYMENT` (cash, UPI or other, with an optional reference). It reduces
  the running balance and is not tied to specific bills. Paying more than is owed asks for confirmation on the
  screen and is kept as an advance (KH4).
- **K5.** An existing paper-notebook balance is entered once as `OPENING_BALANCE`.
- **K6.** A customer's history is the ledger merged with links to the originating sale, quick sale or return.
- **K7.** Customers with entries are deactivated, never deleted. Phone numbers are unique per shop.
  Collect only the personal data that is needed.

## KH. Khata ledger rules (Phase 6, implemented)

- **KH1. Sign convention.** `amount_delta` is signed. **Positive = the customer owes the shop more; negative
  = owes less.** `OPENING_BALANCE` and `CREDIT_SALE` are positive; `PAYMENT` and `RETURN_CREDIT` are negative;
  `ADJUSTMENT` is either sign; `REVERSAL` is exactly the opposite of the entry it undoes. The database checks
  the sign against the type. The API takes positive amounts and the operation decides the sign
  (`/adjustments` takes a positive amount plus `INCREASE` or `DECREASE`).
- **KH2. Outstanding = `SUM(amount_delta)`** for the customer. It is computed on every read and never stored
  (there is no balance column on `customers`). Example: opening 1,000 + credit sale 500 - payment 300 = 1,200
  owed; a further payment of 1,500 gives -300.
- **KH3. Balance status.** `OUTSTANDING` (sum > 0), `SETTLED` (= 0), `ADVANCE` (< 0). The API returns the signed
  `balance` plus `outstanding` (never below 0) and `advance` (never below 0).
- **KH4. Advance.** A negative balance is money the shop holds for the customer. It is never capped or
  discarded, and it is used up automatically by later credit sales (it is all one sum). Overpaying is allowed
  by the API; the screen asks the shopkeeper to confirm.
- **KH5. Insert-only, one writer.** Ledger rows are never updated or deleted (trigger). Only `khata_service`
  reads or writes the table (source-scanning test). There is no generic "insert a ledger row" endpoint: money
  moves only through the controlled operations. The customer record service (`customer_service`) knows nothing
  about balances; balances always come through `khata_service`.
- **KH6. Opening balance.** A positive amount, at most **one live** per customer (a repeat is a 409). If it was
  wrong, reverse it and enter the right one. It can be given when the customer is created, in the same
  transaction: if it is invalid, no customer is created.
- **KH7. Payments** take an amount above zero (at most 2 decimals), a date (default today in the shop's
  timezone, never in the future, backdating allowed), an optional method (`CASH`/`UPI`/`OTHER`), an optional
  reference (at most 100 characters) and a note. An inactive customer can still pay.
- **KH8. Adjustments** are the controlled way to correct a balance: positive or negative, never zero, and a
  **reason is mandatory**. They are permanent like every entry.
- **KH9. Reversal.** A mistake is undone by a `REVERSAL` that references the original and carries the opposite
  amount; the original is untouched and is shown as reversed. An entry can be reversed **once** (checked by the
  service and by a unique constraint), a reversal cannot itself be reversed, and a reason is required. Entries
  that came from a sale or a return (`CREDIT_SALE`, `RETURN_CREDIT`) cannot be reversed by hand (the API returns
  409): the document has to be cancelled instead, so khata and sale never disagree. The service supports this
  for the document workflows of later phases through an explicit flag that no screen passes.
- **KH10. Credit sale and return credit are a foundation only.** `record_credit_sale` (customer must be active;
  reference `SALE` or `QUICK_SALE`) and `record_return_credit` (reference `SALES_RETURN`) exist for Detailed
  Sales (Phase 7) and Sales Returns (Phase 9). A reference is required and the same document can be recorded
  only once. Phase 6 creates no sales and no endpoint calls them; tests call them directly.
- **KH11. History.** Shown by (date, id) with a **running balance** computed over the customer's whole
  history, so filters narrow the rows but never change the balance column. A backdated entry slots into its
  date and changes later running balances; the last row always equals the current balance.
- **KH12. Customers.** Only the name is required. Phone (6 to 15 digits, stored compactly) is unique **within
  the shop**, never across shops, and the message names who has it; email and address are optional. A repeated
  name warns and saves. Customers are deactivated, never deleted; an inactive customer is hidden by default,
  gets no new credit, can still pay, and can be reactivated. Search covers name, phone, email and address.
- **KH13. Atomic and audited.** Every khata operation runs in the request's single transaction and writes an
  audit entry; a failure after the entry is written rolls the entry back. The customer row is locked
  (`FOR UPDATE` on PostgreSQL) so two operations on one customer line up.
- **KH14. Generic.** Nothing in customer or khata code depends on the business type (tested for every type).

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
- **X8.** *(Phase 7.)* Sales (one row per sale: payment, khata part, cost of goods and gross profit, both empty
  where unknown), Sale items (one row per line with the cost snapshot and line profit) and one sale with its
  lines. Owner only, shop-scoped, formula-safe. The inventory-history export names the invoice for sale rows.
- **X7.** *(Phase 6.)* Customers (name, contact details, balance, owes, advance, status) and one customer's khata
  (oldest first, with separate Debit and Credit columns and the running balance; reversals point at their
  originals). Owner only, shop-scoped, formula-safe, UTF-8 BOM in CSV.
- **X6.** Available now: Products, Inventory (current stock), Inventory history (the ledger with running
  balance and the purchase each row came from), and (Phase 5) Purchases (one row per purchase), Purchase items
  (one row per line, with stock and average-cost snapshots) and a single purchase with its lines. The engine is
  generic; each later module supplies its own columns and rows.

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
| R7 a return number is unique per shop and not blank; the idempotency key is unique per shop | R1-R4 return caps, refunds and dates, R7 void rules, R8 repeats |
| IM7 one kept photo per product (unique), a valid hash, positive size | ER1-ER9 error handling, IM1-IM6 image rules |

## API conventions (Phase 3)

- Money and quantities are **strings** in JSON (`"250.00"`, `"2.500"`). A JSON number with a fraction is
  refused, so no client can send a float. Money allows 2 decimals, quantities 3; neither may be negative.
  Responses always show money with 2 decimals and quantities with 3.
- Unknown fields are errors, not ignored.
- Errors: **404** for a missing thing and for another shop's data (the two look identical); **409** for a
  conflict such as a duplicate SKU or a second opening stock; **422** for invalid input or a broken rule.
  Field problems name the field.
- Every query is scoped to the caller's shop. There is no endpoint that changes or deletes a ledger row.

## QS. Quick Sales (Phase 8, implemented)

- **QS1.** A Quick Sale is a money-only entry: a positive amount, an optional transaction-level discount
  (`total = gross - discount`, always above zero), a date that is not in the future, an optional customer and a
  note. It has no product, quantity, cost or per-product discount, and no offer or coupon.
- **QS2.** Lifecycle: DRAFT (no number, no payment, no effect), POSTED (numbered `QS/<fiscal year>/<n>`, payment
  settled), VOID (reversed; the entry and its number stay). Nothing is ever deleted. A posted entry cannot be
  edited; void it and enter a new one. Discarding a draft uses no number.
- **QS3.** Payment is chosen when posting. Paid in full needs a method. Paying less is a credit sale: a customer is
  required and the unpaid part is charged to their khata through `khata_service` (reference `QUICK_SALE`). Paying
  more than the total is refused. Voiding reverses the khata charge.
- **QS4.** A Quick Sale never touches the stock ledger and never has a cost. Its profit is **"Not Available"**, and
  it is excluded from every cost and profit figure.
- **QS5.** Posting counts against the plan's monthly bill allowance, in the same transaction.

## BC. Barcode and product lookup (Phase 8, implemented)

- **BC1.** One lookup, first hit wins: exact barcode (a 12-digit UPC-A and its 13-digit EAN-13 twin are the same),
  exact SKU, exact name (ignoring case, punctuation and repeated spaces), then search.
- **BC2.** Shop-scoped. An exact match may be an inactive product (the screen says so); a search never returns one.
- **BC3.** An unknown code answers "Barcode not found". Lookup never creates or changes a product.
- **BC4.** Scanning on the billing screen adds a product only if it is active and has stock; a repeat scan adds one
  more of a whole-unit product. Needs the plan's barcode feature; ordinary product search does not.
- **BC5.** The product card shows the offer price next to MRP and selling price, never in place of them.

## PI. Price intelligence (Phase 8, implemented)

- **PI1.** Outside prices are information for a person. They **never** change MRP, selling price, purchase price or
  average cost, and a price check can never block or slow billing.
- **PI2.** Matching: exact barcode, exact SKU, exact normalised name + brand + pack size are **EXACT MATCH**; a
  similar name (same pack size when both are known) is **POSSIBLE MATCH** with a lower confidence; a different
  pack size is never a match.
- **PI3.** Location is a typed city, state or market. No GPS. It is never sent to a provider. It either lists
  matching prices first, or the answer says plainly that location was not applied. Without a location it still works.
- **PI4.** A price is shown in the currency the source reported. It is never converted, and no difference to our
  price is shown across currencies.
- **PI5.** A provider that is not configured, switched off, slow, limited or broken shows its status. The shop's last
  saved prices are shown as stale when a provider is down. A saved copy fresher than the cache time is used
  without asking again.
- **PI6.** A check that asked a provider counts against the plan's monthly price-check limit; a cached one is free.
- **PI7.** Provider keys stay in the backend. The API reports only whether a provider is configured.

## PM. Offers, discounts and coupons (Phase 8, implemented)

- **PM1.** One discount system for Detailed Sales now and online orders, campaigns and coupons later. Kinds:
  percentage, amount, offer price, buy X get Y. Scope: the whole bill, chosen products, or chosen categories.
  Audience: everyone, first-time customers (from the shop's own posted sales and quick sales), or chosen customers.
  A coupon is the same offer with a code; a code is unique per shop and is compared without case.
- **PM2.** An offer applies only if the plan includes offers, it is ACTIVE, inside its dates, its audience and
  minimum bill or quantity fit, and its usage limits are not reached (usage counts posted sales; a void gives the
  use back). Draft, paused, expired and future offers never apply.
- **PM3.** Order of application: offers on products or categories first, then whole-bill offers; higher priority
  first, a tie to the older offer; each is worked out on what is left after the earlier ones. An offer that is not
  stackable applies only if nothing applied before it and stops any after it. At most three apply to one bill. The
  total discount can never take the bill below zero after the cashier's own bill discount.
- **PM4.** An offer never changes MRP, selling price or cost. The discount is its own amount on the bill; the bill
  shows Subtotal, discounts, offers with their reason, and Grand total. A line's share of the discount is kept so
  its net revenue is `line_total - promotion_discount`.
- **PM5.** The server is authoritative: the billing screen displays `/sales/calculate`, and posting works the
  offers out again under a lock. A coupon that was entered but no longer applies stops the sale with a reason; it is
  never dropped silently. A bill whose total reaches zero cannot be posted.
- **PM6.** What each posted sale got (offer id, name, terms, amount, reason, coupon) is frozen in `sale_promotions`.
  Editing, pausing or ending an offer never changes a past invoice or report. Offers are never deleted.
- **PM7.** Only the owner creates, changes, activates, pauses or ends an offer.

## PL. Plans and entitlements (Phase 8, implemented)

- **PL1.** Plans, their features and limits are data, not code. Free, Basic and Pro are only examples. A shop's plan
  is its current TRIAL or ACTIVE subscription, else the default. No payment is processed and no payment record is
  ever created; changing a plan is an administrator's action.
- **PL2.** The backend enforces every feature (barcode lookup, offers, price intelligence, advanced reports, online
  store later) and limit (products, users, bills a month, price checks a month). The frontend only hides or disables.
- **PL3.** Monthly counters are per shop, per calendar month in the shop's timezone, and are counted in the same
  transaction as the thing counted.

## RP. Sales reports (Phase 8, implemented)

- **RP1.** Only POSTED sales count. Gross sales = quantity x price before any discount (Quick: the amount).
  Discount = item discounts + bill discounts + offers and coupons. Net = Gross - Discount = the sum of sale totals.
- **RP2.** Detailed, Quick and Combined are always shown apart. Profit exists only for Detailed Sales whose every
  cost is known; Combined has no profit and says "Not Available".
- **RP3.** Offer analytics read the frozen snapshots, so history never changes. Discount analysis needs the plan's
  advanced reports.

## ER. Error handling and recovery (Phase 9, implemented)

- **ER1.** A normal user never sees a traceback, SQL or database text, an internal path, a key, a token, a
  connection detail or any server detail. An expected problem (validation, a missing record, a conflict, a plan
  limit) shows its own plain message; an unexpected failure shows only "Something went wrong while completing
  this action." and a reference.
- **ER2.** Every unexpected failure gets a safe reference id (`ERR-YYYYMMDD-XXXXX`: random, no database id, no
  person or shop). The real exception is written to the internal diagnostics log under that id with the time,
  endpoint (numeric ids masked), HTTP method, shop and user ids, category, exception type, redacted details and
  the request's correlation id (`X-Request-ID`). Passwords, keys, tokens, database addresses, paths, e-mail
  addresses and long digit runs (phone, card numbers) are redacted before anything is written. There is no screen
  that shows the log.
- **ER3.** One error format from one place: `success:false`, `error_code`, `message`, `category`, `retryable`,
  `reference_id`, and `detail` (kept so screens can place a message beside the input it belongs to). Categories:
  validation, not found, conflict, duplicate, insufficient stock, inventory conflict, authentication,
  authorization, plan limit, network, external API, timeout, database, image upload, offer calculation, checkout,
  unexpected. Not every error is recoverable: a screen offers only the recovery that fits.
- **ER4.** Only **reads** are repeated automatically (search, lookup, price check, list, image details), only when
  the failure can pass by itself (network, timeout, an outside service), and at most twice. A **write** (posting a
  sale, moving stock, a payment, a khata entry, a return, an order) is never repeated automatically and is offered
  as "Try again" only when it carries an idempotency key, so the server does the work once. A validation, stock or
  duplicate problem is never retried.
- **ER5.** Idempotency: a create or post may carry an `Idempotency-Key`. The key row is written in the same
  transaction as the work, so a failed attempt leaves nothing behind and a successful one is remembered with its
  answer. The same key with a different request is refused; a repeat returns the stored answer
  (`Idempotent-Replay: true`).
- **ER6.** Data is never thrown away by an error. A form keeps what was typed, says so, and lets the person try
  again; an unsaved form is also backed up in the browser (form fields only, for a day) and offered back after a
  crash or reload. Nothing is restored silently.
- **ER7.** "Success" (or a saved or completed page) is shown only after the server has answered that the
  transaction was committed. A lost connection is shown as a failure to confirm, never as success.
- **ER8.** A part of a screen that crashes is replaced by "This section couldn't be loaded." with Try again, Go
  to Dashboard and Reload; it never shows the error.
- **ER9.** Outside services never break the shop's work: a barcode service that is down offers manual entry, a
  price service that is down offers "Continue without comparison", and billing continues either way. A stock
  conflict at checkout shows the updated available quantity and lets the cart be adjusted.

## IM. Photo capture and image intelligence (Phase 9, implemented)

- **IM1.** Image intelligence is optional and never required: a shop can always add a product by hand. It is a plan
  feature (`image_intelligence`), enforced by the backend.
- **IM2.** A photo is validated by its real content, not its name or declared type: JPEG, PNG or WebP only,
  a size limit, a pixel limit, and a disguised program or a broken file is refused.
- **IM3.** Analysing a photo changes nothing and stores nothing. Every result is labelled **Detected** (read from
  the picture or a barcode) or **Suggested** (a guess), never confirmed, and is shown for review. A barcode is
  checked (its checksum) and looked up through the same Phase 8 lookup as scanning; there is no second barcode
  system.
- **IM4.** A product is created from a photo only through an explicit confirmation of the reviewed boxes
  ("Confirm & Create Product"), never from the suggestions directly. Before creating, the server looks for a
  duplicate (barcode, SKU, normalised name, brand, pack size). "Possible existing product found" is shown, an
  exact barcode or SKU match blocks creation, and a possible match needs the person to say it is a different
  product. A photo never creates a stock movement (no opening stock here).
- **IM5.** A photo never changes stock, prices, purchase price, cost, orders, khata, money or an adjustment, and
  it never changes an existing product. If uncertain, the app asks; it never silently decides.
- **IM6.** Nothing is sent outside without the person's explicit action, a configured provider and a need. The
  picture goes to an analysis provider only when the person ticks that; product lookup sends only the barcode.
  No provider is bundled: until one is configured the answer is "Image analysis is not configured yet." and
  everything else works. A provider's key is a backend setting, never in the frontend, a response, a log or Git.
- **IM7.** A photo is kept only if the owner chooses to keep it: one per product, private to the shop (never
  public, never served by address, fetched only through the API for the owning shop), with a per-shop limit. The
  database holds only its details; the file lives in a private store that can later be object storage.
- **IM8.** Future uses (not built): a supplier invoice photo to a purchase draft, a handwritten stock list to an
  adjustment draft, a shelf photo, a damaged or expired product photo to a suggested adjustment reason. Each will
  produce a draft that a person reviews; none will change stock by itself.
