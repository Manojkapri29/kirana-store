# Supplier analytics

`supplier_intelligence_service.py` reads only from **posted** purchases (`purchases`/`purchase_items`) — the only
record of what a shop actually paid a supplier. It writes nothing and creates no new supplier data.

## Figures

`list_analytics()` / `analytics_for()` (bulk `GROUP BY` queries, not one query per supplier):

| Field | Meaning |
| --- | --- |
| `purchase_count` | Number of posted purchases from this supplier |
| `total_value` | Sum of `total_amount` across posted purchases |
| `average_purchase_value` | `total_value / purchase_count`, or `None` if there have been no purchases (never `0`) |
| `supplied_product_count` | Distinct products bought from this supplier in a posted purchase |
| `first_purchase_date` / `last_purchase_date` | From posted purchases only |

A supplier with no posted purchases gets a real, honest empty row (`purchase_count=0`,
`average_purchase_value=None`) rather than being hidden or defaulted to zero-looking numbers that could be
mistaken for "confirmed to have sold nothing at a price."

## Suppliers are never ranked "best"

The service returns every supplier's numbers side by side (sorted by total value only, as a default list
ordering — not a verdict). There is no "best supplier" score, index, or label anywhere in this module: a person or
a screen draws its own conclusion from the facts shown.

## Delivery performance: honestly unavailable

The architecture has no separate receiving/delivery date — a purchase's `purchase_date` is when it was **entered**,
not necessarily when goods **arrived**. `DELIVERY_PERFORMANCE_NOTE` is attached to every `SupplierAnalytics` row:

> "Delivery performance data unavailable: the system does not record a separate receiving date."

No on-time percentage, lateness score, or reliability rating is calculated from the purchase date, because doing
so would misrepresent data-entry timing as delivery timing.

## Price history

`price_history(product_id, supplier_id)` returns every recorded `unit_cost` from that supplier's posted purchase
lines for that product, oldest first, with `lowest`/`highest`/`latest`/`average`. These are the actual entered
purchase costs — not the MRP, not the selling price, and not the product's blended `avg_cost` (which mixes every
supplier together). A product/supplier pair with no purchase history returns an honest empty history (all zeros,
not fabricated numbers).

## Known limitations

- Figures reflect only posted purchases; a draft purchase (not yet posted) contributes nothing.
- No delivery/receiving-date tracking exists yet — see above. Adding it would need a schema change and is
  explicitly out of scope for Phase 13 (documented as a limitation, not silently worked around).
