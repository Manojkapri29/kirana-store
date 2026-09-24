# Finance dashboard and alerts

`GET /finance/dashboard?date_from&date_to&compare=` - one backend answer; the frontend only displays it and never adds, subtracts
or averages money. Figures come from the services that own them (P&L, cash flow, receivables from khata, payables, tax, expenses),
so the dashboard cannot disagree with a report. Comparison = the equal-length period immediately before; a change against a zero or
unknown base is null. Charts: revenue, net profit, expense and cash-flow trends, receivables/payables ageing, expenses by category,
payment-method mix. Ageing is by transaction date (labelled): no due dates exist.

## Alerts (`GET /finance/alerts`, `POST /finance/alerts/notify`)

Neutral and factual ("Unusual variance detected", "Review recommended", "Missing cost data"); nothing is ever called fraud. Computed
on demand from the books with the shop's own thresholds: expense variance (`expense_spike_pct`), customer balances aged beyond
`overdue_after_days` (from the charge's date), payable increasing, cash-count variance (`cash_variance_alert_amount`), falling
margin (`margin_drop_points`), missing cost data, unreconciled payments, refused period changes. `notify` offers them to the
notification centre once per alert per day.
