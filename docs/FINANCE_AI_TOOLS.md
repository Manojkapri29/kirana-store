# Finance AI tools

Read-only, through the existing assistant pipeline. Each answer states the **period**, its **source**, the **calculation** and the
**limitations**, and every number is what the finance service returned - a value that cannot be known says "Not Available".

| Tool | Permission |
|---|---|
| `get_revenue_summary`, `get_cash_flow_summary`, `get_receivables_summary`, `get_payables_summary`, `get_financial_ledger`, `get_tax_summary`, `get_reconciliation_summary`, `get_financial_dashboard` | `FINANCE_VIEW` |
| `get_pnl_summary` | `FINANCE_VIEW` |
| `get_expense_summary` | `FINANCE_EXPENSE_VIEW` |

`get_profit_summary` (Phase 10, gross profit on costed detailed sales) is unchanged; net profit and expenses are `get_pnl_summary`.

The assistant **cannot** post or approve expenses, modify balances, refund, change tax rates, unlock periods, reconcile, transfer money,
create postings or run SQL: there is no such tool or action kind, the planner refuses these requests, and tool arguments reject
anything undeclared (`shop_id`, `sql`, ...).
