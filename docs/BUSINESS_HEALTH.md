# Business health and anomaly detection

`business_health_service.py` compares the current period against the previous comparable one, and wraps (never
duplicates) the anomaly/insight logic already in `ai_insights_service`. It assigns no cause to a change and makes
no accusation — never the word "fraud," never "theft," never "suspicious."

## Period comparison

`compare_periods(session, shop_id, current, previous)` compares: net sales, transaction count, average bill,
discounts given, purchases, sales returns, and (as a point-in-time-only fact, with no earlier comparison)
customer outstanding. Each metric is a `MetricChange(label, current, previous, change_percent, note)`.

`health_report(session, shop_id, today, days=7)` is the convenience wrapper: the current `days`-day period against
an equal-length period ending just before it (default 7 days; the length is a parameter, not fixed).

## "Not enough historical data" — never a fabricated 0%

Before computing a percentage change, `compare_periods` counts how many days in the **previous** period had any
sales at all. If that count is below `MIN_DAYS_FOR_COMPARISON` (3), every percentage-based metric returns
`change_percent=None` and `note="Not enough historical data."` instead of comparing against a mostly-empty period
and reporting a misleading (often huge or undefined) percentage. A brand-new shop's first weeks are never
disguised as "0% change" or "100% up."

## Anomalies and insights: reused, not reinvented

`health_report()` calls `ai_insights_service.anomalies()` and `ai_insights_service.insights()` directly — the
exact same detection logic the AI assistant and Phase 10 already use. Phase 13 adds no second anomaly engine.
Existing thresholds (documented in `BUSINESS_RULES.md` under "AI"): a >40% sales drop, a >60% spike, discount rate
more than double the usual rate and at least 10% of gross, sales-return rate above 15%, a stock adjustment
touching more than 25% of a product's starting stock (and at least 5 units), and a quick-sale-to-detailed-sale
ratio spike. Every anomaly names *what* changed and *how to check it* — never *why* it happened.

## Known limitations

- Statistical methods here are simple period-over-period comparisons and the existing threshold-based anomaly
  rules — no machine-learning forecasting, no seasonality adjustment.
- A shop that has been open fewer than `MIN_DAYS_FOR_COMPARISON` days in its comparison window will see "Not
  enough historical data" on most metrics; this is intentional honesty, not a bug.
