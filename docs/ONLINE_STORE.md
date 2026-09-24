# Online store

A shop can publish a web page where customers order from it. It was built after the Phase 20 audit found that ordering did not exist; it follows
BUSINESS_RULES **O1** and **O2**: same products, customers, pricing and inventory as the counter, **no second inventory, no second sales ledger**.

## How it works

1. **Setup** (screen *Online store*; needs `STORE_SETTINGS_MANAGE`): choose a web address (`/store/<slug>`), a name, whether it is open, whether you offer delivery,
   pickup or both, whether you take cash and/or UPI, a minimum order, a phone number and a notice. Then switch on the products to show. A product with no
   switch is never shown. A new store starts **closed**.
2. **A customer orders** without signing in: they see the listed, active products with the shop's price and whether each is in stock (never the quantity, the cost,
   the SKU or anyone's data), fill a cart kept in their own browser, and send name, phone, delivery or pickup, and how they will pay.
3. **The order is a request.** Placing it moves **no stock and no money** and creates no sale. The server prices it from its own product data; the customer
   cannot send a price, total or status (the API refuses those fields). The order keeps a snapshot of name, unit and price. Numbers look like `ORD/2026-27/0001`.
4. **The shop** (screen *Online orders*): accept (this finds the customer by phone or adds them as an *Online* customer) or reject with a reason; then preparing
   → ready → out for delivery → **delivered**. It can cancel with a reason any time before delivery; the customer can cancel only while it is still new.
5. **Delivered creates the sale.** In the same transaction the ordinary Detailed Sale is created and posted through `sale_service` at the order's prices, paid in
   full in cash (COD) or UPI, taking the stock out at cost, earning loyalty, and appearing in every report. If the stock has gone, the delivery is refused, nothing
   changes, and the shop can cancel. An order becomes at most one sale (database-enforced), and a delivered order can be returned like any sale.

**Nothing is charged online.** There is no payment gateway in this flow: the customer pays by hand when the order reaches them. (Online-payment *evidence* records
from Phase 17 are separate and unchanged.)

## Public safety

* Public routes (`/api/v1/public/stores/...`) need no sign-in and are outside RBAC by design; every route is rate limited per client address, and placing orders has
  its own limit (`KIRANA_RATE_LIMIT_STORE_ORDER`, default 10 per minute).
* A request key (`Idempotency-Key`) is required: the same key and the same order returns the same order once, even for simultaneous requests; the same key with a
  different order is refused.
* An order is followed with its **tracking token**, shown once in the answer that creates it (and derived from the request key, so a repeated request gets the same
  token). Only the token's SHA-256 is stored. A wrong number, wrong token or another shop's store all answer the same "not found". The tracking page shows the
  items, total, status and timeline, and no phone number, address or customer record.
* One phone number can have at most 5 orders waiting to be accepted. Text is trimmed and control characters removed; lengths and quantities are bounded; 30 lines at most.
* No DELETE route exists. Order history rows are insert-only (database triggers on SQLite and PostgreSQL).

## Permissions

`STORE_SETTINGS_MANAGE` (the existing settings permission, now used for the store's setup and product listing; Owner and Manager), and the existing `ONLINE_ORDER_VIEW`, `ONLINE_ORDER_ACCEPT`, `ONLINE_ORDER_REJECT`, `ONLINE_ORDER_STATUS_UPDATE`. Marking an
order delivered also needs `SALE_CREATE` and `SALE_POST`, because it sells.

## Reports and AI

Delivered orders are ordinary detailed sales, so their revenue is **already** inside sales, profit and cash figures and is never added again. The *online orders*
KPI, the insights card, the customer report and the assistant tool report the real counts (orders placed, delivered, rejected or cancelled, delivered value).
The customer analytics `online_order_count` is the number of orders the shop accepted for that customer. The report builder does not offer an online dataset.

## Not built

Delivery fees, delivery slots, order-by-order stock reservation (stock is checked when ordering and again when delivering, not held), customer accounts or logins,
online payment, SMS/WhatsApp order updates (the customer follows the tracking page; message providers from Phase 17 are not wired to order events), and product photos on the
storefront (photos are private to the shop).

## Tests

`backend/tests/test_online_store.py` (57 tests: setup, catalogue, ordering, idempotency and races, tracking, workflow, delivery/stock failure, returns, reports, isolation, RBAC)
and `frontend/src/onlineStore.test.tsx`.
