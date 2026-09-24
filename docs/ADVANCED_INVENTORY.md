# Advanced inventory intelligence

`inventory_intelligence_service.py` computes read-only metrics from the existing inventory ledger (through
`inventory_service`) and sales history (through `analytics_service`). It is not a second source of truth: current
stock still comes only from `inventory_service.list_inventory` / `get_stock`, and nothing in this module writes
anything.

## Configurable periods

Every function takes a `days` parameter (or `window_days`/`cover_days` for reorder). There is no one "correct"
definition of fast, slow or dead moving — a screen or the AI assistant can ask for 7, 30, 60 or 90 days, and the
result always says which period it used. Defaults: `fast_moving`/`inventory_health` 30 days, `slow_moving` 60 days,
`dead_stock` 90 days.

## Fast / slow / dead moving

All three are built on `_movers()`, which pairs each active product's current stock with units sold in the period
(`analytics_service.product_sales`):

- **Fast moving**: highest `quantity_sold` among products that still have stock, sorted descending.
- **Slow moving**: stock on hand with either no sales, or more than `cover_multiplier` × `days` days of cover at the
  recent pace (default multiplier 3). Wording is neutral: "low recent sales velocity", never "bad" or "write off".
- **Dead stock**: a stricter case of slow moving — stock on hand with *zero* sales at all in the period.

## Stock value

`stock_value()` sums `current_stock × avg_cost` across held products. If **every** held product has an unknown
cost, the total is `None` (never `0`) with a count of how many products are missing a cost. If only some are
missing, the total is the sum of what is known and the count says how many are left out — the number is never
silently short.

## Stock aging — a documented estimate

The architecture has no lot or batch tracking, so `stock_aging()` cannot report the true age of the units
currently on the shelf. What it reports instead: **days since the most recent inbound transaction** (an opening
entry, a purchase, or a positive adjustment) for each product. This is the oldest a single unit *could* be, not
necessarily the age of what's actually left once a product has been part-sold and part-restocked. The module
docstring and every place this number is shown say so explicitly.

## Inventory health summary

`inventory_health()` returns one dataclass combining stock counts by status (in stock / low stock / out of stock),
total stock value (or "Not Available"), and counts of fast/slow/dead movers and stockout/overstock risk, all for
the same period — so a dashboard never mixes numbers computed at different windows without saying so.

## Reuse

- Current stock, product/unit/category joins: `inventory_service.list_inventory`.
- Recent sales velocity: `analytics_service.product_sales` / `sales_velocity`.
- Last-inbound date (bulk, not per-product): `inventory_service.last_inbound_dates`.

No new inventory table was created; Phase 13 only reads what Phases 1–9 already write.

## Known limitations

- Stock aging is an estimate (see above), not batch/lot tracking.
- "Fast"/"slow"/"dead" thresholds are algorithmic defaults, not judgements about the business; a shop can always
  ask for a different period.
- Money figures depend on `avg_cost`, which itself depends on purchases having a `unit_cost` recorded.
