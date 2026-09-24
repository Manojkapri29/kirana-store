# Inventory

**Stock is derived, never an editable number:** `Current stock = Opening + Purchases − Sales + Sales returns − Purchase returns ± Adjustments`, the sum of
`inventory_transactions.qty_delta` (quantities are integer thousandths). Every row is insert-only; a mistake is corrected by a reversal or adjustment
(with a mandatory reason). A document line can post each transaction type once (database-enforced), so a replay cannot double-post.

* **Costing:** weighted average, kept to 2 decimals. A posted sale keeps its own cost; later purchases change the average for future sales only.
  Unknown cost is `null` ("Not Available"), never zero; profit built on unknown cost says so.
* **Negative stock:** refused unless the shop allows it. Concurrent sales of the last units: exactly the available quantity succeeds (tested with 14 buyers / 10 units).
* **Verified in Phase 20:** the formula above was recomputed from raw ledger rows and matched the API (`tests/test_phase20_journeys.py`); `integrity_cli` is clean after the drill.
* Details: [BUSINESS_RULES.md](BUSINESS_RULES.md) (L, C, A), [ADVANCED_INVENTORY.md](ADVANCED_INVENTORY.md), [STOCK_COUNTING.md](STOCK_COUNTING.md), [REORDER_PLANNING.md](REORDER_PLANNING.md), [PURCHASE_PLANNING.md](PURCHASE_PLANNING.md).
