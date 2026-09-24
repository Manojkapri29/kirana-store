# Cohort and retention analytics: definitions

Implemented in `backend/app/reporting/cohorts.py`; screen: Analytics > Reports > Cohort retention; endpoint `GET /analytics/cohorts?months=`.

* **Cohort.** Identified customers grouped by the **calendar month of their first-ever posted purchase** (a detailed or quick sale that names
  them). Walk-in sales belong to nobody and are not part of any cohort. The "January 2026 cohort" is the customers whose first purchase was in
  January 2026.
* **Month N.** The calendar month N months after the cohort month; Month 0 is the cohort month itself.
* **Active customers (month N).** Cohort customers with at least one purchase in that month.
* **Repeat purchasers (month N).** Active customers who made two or more purchases in that month (month 0), or who had already purchased in
  an earlier month (months 1 and later).
* **Retention rate (month N)** = active customers in month N / cohort size x 100.
* **Revenue** = sales to those customers in that month, before returns. **Average purchase value** = revenue / purchases.

Output is one row per cohort and month: `cohort, cohort_size, month_offset, month, active_customers, retention_pct, repeat_purchasers,
purchases, revenue, average_purchase_value, partial_month, note`.

Honesty rules: only months that exist are reported (a cohort is never extended past the current month); the current month is marked
**partial**; nothing is extrapolated or predicted; a cohort smaller than 5 customers carries a warning because one customer moves its
percentages a lot. The period filter chooses which cohort months are listed; retention is always measured over every month since.
