# Financial ledger

`GET /finance/ledger` is **one normalised view over every money event**, not a table of copies. Each row has `source_type` +
`source_id` (the document it came from), `source_module`, date, `amount`, `payment_method`, `direction`, status and user.

| Event | Read from | `amount` | `settled_amount` (money that really moved) |
|---|---|---|---|
| SALE | posted Sale and QuickSale | total | paid part (a credit sale's unpaid part is in khata) |
| SALE_RETURN | posted SalesReturn | refund | refund if CASH/UPI, 0 if KHATA credit |
| PURCHASE | posted Purchase | total | `amount_paid` recorded on it (normally 0) |
| PURCHASE_RETURN | posted PurchaseReturn | total | total if CASH/UPI, 0 if supplier credit |
| CUSTOMER_PAYMENT | khata PAYMENT (not reversed) | payment | payment |
| EXPENSE, SUPPLIER_PAYMENT, OWNER_CAPITAL, OWNER_WITHDRAWAL, OTHER_INCOME, ADJUSTMENT | `finance_entries` | amount | amount |

Voided documents are left out. Cash reports use `settled_amount`, never `amount`.

## Finance-owned entries

`finance_entries` is **insert-only** (database triggers on SQLite and PostgreSQL). Corrections:

* **Reversal** (`POST /finance/entries/{id}/reverse`): a mirror row (opposite direction, `reverses_entry_id`). Reversing twice
  does nothing; a reversal cannot be reversed. If the original's period is locked or closed the reversal is dated *today* (a
  controlled correction) so a closed period is never altered.
* **Adjustment** (`POST /finance/adjustments`): explicit, with a reason; above the configured limit it answers `202` and records
  nothing until a second person approves it (single use, bound to the exact amount/date/method/reason).

**Idempotency**: the same source (`event_type`, `reference_type`, `reference_id`) is recorded once (unique constraint; a race
returns the winner). `POST /finance/entries` also accepts an `Idempotency-Key`.

Manual events are limited to supplier payments, owner capital, owner withdrawals and other income. Filters: date, event type,
payment method, customer, supplier, status, reference, source type.
