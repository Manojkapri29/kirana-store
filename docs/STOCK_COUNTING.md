# Stock counting (cycle counting)

`stock_count_service.py` implements a physical-count workflow reconciled against the inventory ledger. It never
writes to `inventory_transactions` except at the final `post` step, and always through the existing
`inventory_service.record_adjustment` — there is no second stock-writing path.

## Workflow

```
CREATE (choose scope)  ->  COUNTING (enter quantities)  ->  REVIEW (differences computed and shown)
    ->  APPROVED (a different person confirms them)  ->  POSTED (inventory_service writes the adjustments)
```

A count can be **CANCELLED** at any point before POSTED (`DRAFT`/`COUNTING`/`REVIEW`/`APPROVED`), with no stock
effect. A reason is required. POSTED counts are never deleted or reopened.

| Step | Function | What happens |
| --- | --- | --- |
| Create | `create()` | Picks products by scope (FULL / CATEGORY / PRODUCTS), snapshots each product's current stock into `expected_quantity` |
| Start counting | `start_counting()` | DRAFT → COUNTING |
| Enter counts | `enter_counts()` | Records `counted_quantity` per product; can be called repeatedly, later entries replace earlier ones |
| Submit for review | `submit_for_review()` | Refuses if any product is uncounted; computes `variance = counted - expected` and snapshots `unit_cost_snapshot`; emits a `STOCK_COUNT_VARIANCE` notification if any line differs |
| Approve | `approve()` | REVIEW → APPROVED, or routes to the approval queue (see below) if the variance is large |
| Post | `post()` | For every line with a non-zero variance, calls `inventory_service.record_adjustment(..., reason_code=AdjustmentReason.COUNT_CORRECTION)`, one row per product, noting the count it came from |
| Cancel | `cancel()` | Any pre-POSTED status → CANCELLED, with a mandatory reason |

## Why a snapshot, not a live number

`expected_quantity` is captured once, when the count is created. Counting can take hours, and the ledger keeps
moving (other sales, other adjustments) while it happens. The variance a person reviews is always against that
fixed snapshot, so what they see explains itself — it is never "the ledger changed underneath the count."

## Authorization rules

- **Creator ≠ approver**: `approve()` refuses (`ForbiddenError`) if the approving user is the same as
  `count.created_by`.
- Permissions: `STOCK_COUNT_VIEW`, `STOCK_COUNT_CREATE`, `STOCK_COUNT_REVIEW`, `STOCK_COUNT_APPROVE`,
  `STOCK_COUNT_POST` (see `app/core/permissions.py`; enforced per-route in `ROUTE_RULES`).
- Posting requires `STOCK_COUNT_POST` and the count must be APPROVED — `post()` before approval is refused
  (`ConflictError`).

## Large-variance approval

A shop can set `shops.stock_count_variance_threshold` (money; `NULL` means no extra approval is configured — never
a hardcoded number). At `approve()`, if the total absolute variance value (using each line's `unit_cost_snapshot`,
only for lines where the cost is known) reaches the threshold, the count is **not** approved directly. Instead:

1. An `approval_request` is created (`kind="STOCK_COUNT_LARGE_VARIANCE"`) via the generic `approval_service`.
2. `count.requires_approval` is set and the count stays in REVIEW.
3. Someone with the approval permission decides it through `POST /api/v1/approvals/{id}/decide`.
4. The decision calls `stock_count_service.apply_approval_decision()`: approved moves the count to APPROVED;
   rejected sends it back to COUNTING (a recount is needed, not a repost).

If no threshold is configured, every approval goes through `approve()` directly and the approval queue is never
touched.

## Data model

`stock_counts` / `stock_count_items` (migration `0016`). `expected_quantity`/`counted_quantity`/`variance` are
`Quantity` columns (thousandths, matching every other quantity in the schema); `unit_cost_snapshot` is a `Money`
column and is `NULL`, never `0`, when the product's cost is unknown at review time.

## Known limitations

- A count's `unit_cost_snapshot` only reflects `avg_cost` at review time; a product with an unknown cost
  contributes `NULL` to the variance-value total, which can therefore understate the true variance value.
- There is no partial/incremental approval — a count is approved or rejected as a whole.
