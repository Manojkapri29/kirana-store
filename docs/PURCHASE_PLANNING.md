# Purchase planning workspace

`purchase_planning_service.py` is a thin layer over two existing pieces — it adds no new business logic, no new
inventory or financial write path, and no second way to enter a purchase.

## What it does

- `suggestions()` delegates straight to `ai_insights_service.purchase_suggestions()` — the same supplier-grouped
  reorder list the AI assistant and the dashboard use (see `REORDER_PLANNING.md`).
- `build_draft()` delegates straight to the existing `purchase_service.create_purchase()`, given a supplier and a
  list of (possibly hand-edited) lines. The result is a **DRAFT** purchase, exactly like one entered by hand
  through the ordinary purchases screen.

## What it never does

- **Never posts.** Posting a purchase (the step that changes stock and cost) remains the separate,
  already-permissioned `purchase_service.post_purchase` — this workspace only ever builds a draft. Confirmed by
  `tests/test_phase13_reorder_purchase.py::TestPurchasePlanning::test_building_a_draft_never_posts_it`.
- **Never invents a price.** `purchase_service.create_purchase` already refuses a line with no `unit_cost` — this
  module does not bypass that. A recommendation only *suggests* a price (when a purchase price or average cost is
  on record); the person reviewing the draft can accept it or change it, but an unpriced line must get a price
  before the draft can be built at all.

## Typical flow

1. `GET /api/v1/intelligence/purchase-suggestions` — supplier-grouped recommendations, read-only.
2. A person reviews the lines, adjusts quantities or prices as needed.
3. `POST /api/v1/intelligence/purchase-suggestions/draft` — the one write endpoint in this module — creates the
   DRAFT purchase via `purchase_planning_service.build_draft()`.
4. From there the purchase is an ordinary purchase: reviewed and posted through the existing
   `/api/v1/purchases/{id}/post` endpoint, under its own existing permission (`PURCHASE_POST`).

## Known limitations

- No batching across suppliers into one purchase — one draft per supplier, matching how `purchase_service` models
  a purchase (one supplier per document).
- A recommendation reflects reorder logic at the moment it is fetched; if stock changes between fetching
  suggestions and building the draft, the draft still uses whatever quantities the caller submits (the same
  behaviour as a person hand-typing a purchase).
