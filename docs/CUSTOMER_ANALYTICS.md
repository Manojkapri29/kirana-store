# Customer analytics

`customer_intelligence_service.py` reads sales (`sales`, `quick_sales`) and khata (through `khata_service`) to
build one analytics row per customer. It writes nothing, and it makes **no creditworthiness judgement** — an
outstanding balance is shown as a plain fact ("Outstanding balance: ₹X"), never as a rating, risk score, or
recommendation to extend or deny credit.

## Performance: bulk queries, not one per customer

`list_analytics()` computes every customer's figures with a handful of `GROUP BY` queries total
(`_bulk_rows()`), so it stays fast with thousands of customers. `analytics_for()` (a single customer) is built on
the same bulk function rather than a separate, slower per-customer code path — this was corrected during
development specifically to avoid an N+1 query pattern (a per-customer call into `khata_service.list_accounts`)
before it ever shipped.

## Figures

Purchases (detailed + quick sales, gross billed — not profit), transaction counts, first/last purchase date,
average transaction value, outstanding balance, advance, last payment date, and days since last purchase/payment.
A customer with no history at all gets an honest empty row (`total_purchases=0`, `average_transaction_value=None`,
`segments=[]`) rather than a fabricated figure.

`online_order_count` is always `0` — there is no online store in this codebase yet; the field is kept so the
shape does not need to change later.

## Segments: factual buckets, configurable thresholds

`CustomerSegment`: `NEW`, `ACTIVE`, `INACTIVE`, `CREDIT`, `HIGH_FREQUENCY`, `LOW_FREQUENCY`. Each is a statement
about *when* the customer last bought something or *how many* times, never a judgement about the person:

| Segment | Condition |
| --- | --- |
| `NEW` | First purchase within `new_days` of today (default 30) |
| `ACTIVE` | Last purchase within `active_days` of today (default 90) |
| `INACTIVE` | Has purchased before, but not within `active_days` |
| `CREDIT` | Outstanding balance > 0 |
| `HIGH_FREQUENCY` | Active and at least `frequent_visits` transactions within `active_days` (default 4) |
| `LOW_FREQUENCY` | Active but fewer than `frequent_visits` transactions |

`new_days`, `active_days`, and `frequent_visits` are all keyword parameters — a shop can tune what counts as
"active" for its own rhythm (a garments shop and a daily-grocery shop have very different natural purchase
cadences); nothing is hardcoded to one business type.

## Known limitations

- Segmentation reflects sales + khata history only; it has no signal for a customer's behaviour outside this
  shop.
- `advance`/`outstanding` come from `khata_service`'s existing account view; a customer with no khata account at
  all is treated as balance `0`.
