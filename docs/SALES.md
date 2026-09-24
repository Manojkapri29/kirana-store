# Sales

* **Detailed sales:** product-wise bills (draft → post). Posting takes stock at cost, records payment (cash/UPI/credit; a method is required whenever money is received),
  writes credit to the customer's khata, and earns loyalty points. A posted sale is never edited or deleted: use a return or void.
* **Quick sales:** money only (no products, no cost, so no profit is ever attributed to them).
* **Returns:** refund proportional to what was paid, as cash/UPI or khata credit; goods return at the line's cost.
* **Khata:** the customer balance is the sum of an append-only ledger (`amount_delta`); payments, adjustments and reversals are entries.
* **Offers/coupons, barcode scanning, price lookups (information only)** as in [BUSINESS_RULES.md](BUSINESS_RULES.md) (SL, R, K, KH, QS, PM).
* **Online orders:** the [online store](ONLINE_STORE.md) lets customers send order requests; delivering one creates and posts an ordinary Detailed Sale, so it is inside every sales report. Nothing is charged online. Online *payments* remain evidence records ([INTEGRATIONS.md](INTEGRATIONS.md)).
* **Offline:** the till can queue quick sales, sales and customer payments and sync later ([PWA_OFFLINE.md](PWA_OFFLINE.md)).
