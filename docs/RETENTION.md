# Retention and churn (Phase 14)

Transparent rules, never an unexplained score. Every flag carries its reason.

* **At risk** — the days since the last purchase exceed `at_risk_multiplier` (default 2×, a parameter) × the customer's own
  average gap between purchases. Reason: "No purchase recorded for 74 days; this customer's typical interval is 28.0 days."
  A customer with fewer than two purchases has no interval, so is never flagged.
* **Reactivated** — returned after a gap larger than the multiplier × their earlier average, with the numbers stated.
* **Repeat purchase rate** — customers with ≥ 2 lifetime purchases ÷ customers with any purchase; `null` when nobody has purchased.
* **Cohort retention** — reports `NOT_ENOUGH_DATA` for a small cohort instead of a fabricated percentage.
* **Reactivation candidates** — purchased before, nothing in the last `inactive_days`; deactivated customers excluded.

Endpoints: `GET /crm/retention`, `/crm/purchase-patterns`, `/crm/reactivation-candidates`, `/crm/reactivation/preview`
(see [CAMPAIGNS.md](CAMPAIGNS.md) for the outreach workflow).
