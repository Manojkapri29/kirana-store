# AI business-intelligence tools

`backend/app/services/ai_bi_tools.py`, registered in `ai_tools.py`. The assistant reads the *same* reporting functions the analytics screens
and exports use, so it can only repeat what they report. **All read-only.** No tool accepts SQL, a shop id, an amount, a status or any other
write-shaped argument (`extra="forbid"`, tested), and the assistant's guard still refuses writes and SQL before any tool runs. It never
modifies inventory, prices, balances, expenses, refunds, purchases or approvals.

| Tool | Answers | Primary permission | Also needs |
| --- | --- | --- | --- |
| `get_kpi` | one KPI with formula, source, change | `ANALYTICS_VIEW` | (the KPI's own permission, else "unknown KPI") |
| `get_executive_dashboard` | every KPI the caller may see, by section | `ANALYTICS_EXECUTIVE` | |
| `get_sales_analytics` | summary, trend, products, categories, channels, payment methods | `ANALYTICS_VIEW` | `REPORT_VIEW` |
| `get_inventory_analytics` | summary, stock, turnover, fast/slow/dead, reorder, stock-outs | `ANALYTICS_ADVANCED` | `INVENTORY_VIEW` |
| `get_customer_analytics` | overview, segments, loyalty | `ANALYTICS_ADVANCED` | `CRM_ANALYTICS_VIEW` |
| `get_supplier_analytics` | overview, spend, returns | `ANALYTICS_ADVANCED` | `SUPPLIER_VIEW`, `PURCHASE_VIEW` |
| `get_finance_analytics` | summary, trend, expenses, payment mix | `ANALYTICS_ADVANCED` | `FINANCE_VIEW` |
| `get_cohort_report` | cohort retention | `ANALYTICS_ADVANCED` | `CRM_ANALYTICS_VIEW` |
| `get_cross_module_insights` | facts observed together | `ANALYTICS_ADVANCED` | (each observation's own data permission) |
| `get_saved_report` | runs a saved custom report by id or name | `ANALYTICS_CUSTOM_REPORT` | the dataset's permissions, checked now |

The extra permissions mirror the matching analytics route rules (a test compares them), so the assistant never shows what the screen would
refuse. `get_supplier_analytics` already existed (Phase 13, a lifetime supplier list under `REPORT_VIEW`); it is now this period-aware tool
with the analytics and supplier permissions, and the Phase 13 test was updated accordingly.

Every answer states the **period**, the **metrics**, the **source**, the **calculation** and the **limitations** (as notes), says
*Not Available* / *Profit Not Available* rather than guessing, and words cross-module answers as things seen together, never causes.
The planner routes clear phrases ("kpi", "executive dashboard", "sales analytics", "stock turnover", "supplier spend", "cohort",
"observed together", "saved report #3", ...) to these tools; anything else falls through to the existing tools.
