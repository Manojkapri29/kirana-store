# Reorder recommendations

`ai_insights_service.reorder_recommendations()` (extended in Phase 13, not duplicated) is the single engine behind
every reorder suggestion — the advanced dashboard, the purchase-planning workspace, and the AI assistant's
`get_purchase_suggestions` tool all call the same function. Recommendation-only: nothing here creates a purchase.

## The formula, in full

For each active product:

1. `per_day = units sold in the last window_days / window_days` (default window 30 days).
2. `cover = current_stock / per_day` (days of stock left at the recent pace), or `None` if nothing sold recently.
3. A product qualifies if it is **at or below its reorder level**, or its cover is **under 7 days** ("running
   out").
4. `target = per_day × cover_days + reorder_level` (default cover 21 days) — brings stock up to that many days of
   cover, plus the reorder level as a buffer. A product with **no recent sales** targets `2 × reorder_level`
   instead (there is no sales rate to project from).
5. `suggested_quantity = max(target - current_stock, 0)`, rounded up to a whole unit (or to one decimal place for
   products whose unit allows fractions).

Both `window_days` and `cover_days` are keyword parameters — a caller can ask "what if I only look at the last 7
days?" and get an honestly different answer, always labelled with the window it used.

## Pack size and MOQ

If the product has a `pack_size` set, the suggestion is rounded up to a whole number of packs, and a reason line
says so ("Rounded up to whole packs of N units"). If it has a `moq` (minimum order quantity) and the suggestion is
below it, the suggestion is raised to the MOQ, with its own reason line. Both are optional product columns —
`NULL` means not set, and neither is invented.

## What is never invented

- **Supplier lead time**: `suppliers.lead_time_days` is optional. If the product's default supplier has no lead
  time set, a reason line says explicitly: *"The supplier's lead time is not set: this does not account for
  delivery time."* The formula never assumes a default lead time.
- **Cost**: `unit_cost_used` is `product.purchase_price` if known, else `product.avg_cost` if known, else `None`.
  `estimated_cost` is `None` whenever the cost is unknown — never `0`, and `cost_basis` names which figure was
  used ("latest purchase price" or "average cost") so the number is never presented as more certain than it is.
- **Demand**: no forecasting model, no seasonality — just the actual recent sales rate. `low_history=True` flags a
  product with fewer than 5 units sold in the window, so a screen can visually warn that the projection rests on
  very little data.

## Purchase suggestions (grouped by supplier)

`purchase_suggestions()` groups the same recommendations by `default_supplier_id` (products with none are grouped
last), and totals `estimated_cost` per group — again `None`/skipped lines are counted separately
(`lines_without_price`), never folded into the total as zero.

## Known limitations

- The reorder level and pack size/MOQ are per-product settings the shop must maintain; a stale reorder level
  produces a stale (but still transparently-explained) suggestion.
- Reorder does not account for goods already on an open, unposted purchase draft — a recommendation can suggest a
  quantity that overlaps with a draft not yet posted. This is a scope decision, documented here rather than
  silently handled: the reorder engine only reads posted stock and sales, matching how `inventory_service` defines
  "current stock."
