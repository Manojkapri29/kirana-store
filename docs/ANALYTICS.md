# Advanced reporting, business intelligence and executive analytics (Phase 16)

One reporting layer over the data that already exists. **It adds no second data source**: every figure comes from the same
services and ledgers the rest of the application uses (sales, quick sales, purchases, the inventory ledger and costing, khata, the
Phase 15 finance ledger and profit and loss, CRM, loyalty and promotions). Nothing here writes business data.

## Layout

| Piece | Where |
| --- | --- |
| Filters and periods | `backend/app/reporting/filters.py` |
| KPI framework (31 KPIs) | `reporting/kpis.py` - see [ANALYTICS_KPIS.md](ANALYTICS_KPIS.md) |
| Executive dashboard | `reporting/executive.py` |
| Sales / inventory / customers / cohorts / suppliers / finance analytics | `reporting/sales.py`, `inventory.py`, `customers.py`, `cohorts.py`, `suppliers.py`, `finance.py` |
| Cross-module observations | `reporting/crosslinks.py` |
| Custom report builder | `reporting/builder.py`, `services/saved_report_service.py` - see [REPORT_BUILDER.md](REPORT_BUILDER.md) |
| Drill-down | `reporting/drilldown.py` |
| Report catalog (shared by exports, schedules and the assistant) | `reporting/catalog.py` |
| Exports (CSV, XLSX, PDF) | `services/analytics_export_service.py`, `services/export_service.py` |
| Scheduled advanced reports | `services/advanced_report_service.py` - see [SCHEDULED_REPORTS.md](SCHEDULED_REPORTS.md) |
| AI tools | `services/ai_bi_tools.py` - see [ANALYTICS_AI_TOOLS.md](ANALYTICS_AI_TOOLS.md) |
| API | `backend/app/api/v1/analytics.py` |
| Screens (English and Hindi) | `frontend/src/features/analytics/` (menu: **Analytics**) |

`app/reporting/` is read-only: `tests/test_architecture.py` lets it read the ledger models but no service may be imported the other
way round, and routers never import models.

## The rules every analytics figure follows

1. **Backend-generated, shop-scoped, permissioned.** The browser sends a preset (or two dates) and filters; it never sends SQL and
   never computes a period. The shop comes from the session. A route needs an analytics permission **and** the permission of the data
   it shows.
2. **Drafts, voided and rejected documents never count.** Corrections are reversals, so a reversed document does not count twice.
3. **Money is integer paise** (`Money`); no floats. Sums of expressions are typed explicitly (a bare SQL sum of two money columns is
   untyped on SQLite; `type_coerce(..., Money)` is used, and a regression test covers the bug this found in
   `analytics_service.product_sales`).
4. **Never fabricate.** No cost -> *Profit Not Available (Insufficient Cost Data)*; no comparison base -> *Insufficient comparison
   data*; no online orders exist -> *Not Available*. A blank is unknown, never zero.
5. **Quick Sales are money-only.** They appear in revenue, transaction counts, payment methods and customers, and never in
   product-level analysis. Online orders are not connected, so nothing is added and nothing is double counted.
6. **Deterministic.** The same input gives the same output (rows have a total order: ties break by name or id).
7. **Read-only and factual.** Cross-module text says *observed together / associated with / during the same period*; it never claims
   a cause and never predicts or recommends an action.

## Filters and periods

Common filters: `preset` or `date_from`/`date_to`, `compare` (`previous_period`, `previous_year`, `none`, or explicit
`compare_from`/`compare_to`), `product_id`, `category_id`, `brand`, `supplier_id`, `customer_id`, `payment_method`, `channel`
(`ALL`, `DETAILED`, `QUICK`, `ONLINE`), `active`, `business_type`, plus `limit` (1-200, default 50) and `offset`. Each report says which
filters it **used** and which it **ignored** (`filters_applied`, `filters_ignored`): a filter that cannot apply is never silently
dropped. `business_type` is accepted for completeness but no report behaves differently by type (a shop has one type).

Presets: today, yesterday, this/last week (weeks start on **Monday**), this/last month, this/last quarter (calendar quarters), this/last
year (calendar), custom (at most five years). A "this ..." preset is compared with the **same number of elapsed days** of the previous
unit (clipped to its length), so a half-finished month is never compared with a whole one; other presets are compared with the equal-length
window immediately before. Both ends of a period are inclusive.

## API

<!-- API-TABLE -->
| Method | Path (under `/api/v1`) | Permissions (all needed) |
| --- | --- | --- |
| POST | `/scheduled-reports/advanced` | `ANALYTICS_SCHEDULE`, `SCHEDULED_REPORT_MANAGE` |
| GET | `/scheduled-reports/{report_id}/runs` | `ANALYTICS_SCHEDULE` |
| GET | `/analytics/kpis/definitions` | `ANALYTICS_VIEW` |
| GET | `/analytics/kpis` | `ANALYTICS_VIEW` |
| GET | `/analytics/executive` | `ANALYTICS_EXECUTIVE` |
| GET | `/analytics/inventory/summary` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/inventory/stock` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/inventory/categories` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/inventory/turnover` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/inventory/movers` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/inventory/aging` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/inventory/movement` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/inventory/purchase-vs-sales` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/inventory/adjustments` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/inventory/count-variance` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/inventory/reorder` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/inventory/stock-outs` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/customers/overview` | `ANALYTICS_ADVANCED`, `CRM_ANALYTICS_VIEW` |
| GET | `/analytics/customers/list` | `ANALYTICS_ADVANCED`, `CRM_ANALYTICS_VIEW` |
| GET | `/analytics/customers/segments` | `ANALYTICS_ADVANCED`, `CRM_ANALYTICS_VIEW` |
| GET | `/analytics/customers/loyalty` | `ANALYTICS_ADVANCED`, `CRM_ANALYTICS_VIEW` |
| GET | `/analytics/customers/campaigns` | `ANALYTICS_ADVANCED`, `CRM_ANALYTICS_VIEW` |
| GET | `/analytics/customers/referrals` | `ANALYTICS_ADVANCED`, `CRM_ANALYTICS_VIEW` |
| GET | `/analytics/cohorts` | `ANALYTICS_ADVANCED`, `CRM_ANALYTICS_VIEW` |
| GET | `/analytics/insights` | `ANALYTICS_ADVANCED` |
| GET | `/analytics/export` | `ANALYTICS_EXPORT` |
| GET | `/analytics/export/{report_key}` | `ANALYTICS_EXPORT` |
| GET | `/analytics/drill/revenue/{level}` | `ANALYTICS_ADVANCED`, `REPORT_VIEW` |
| GET | `/analytics/drill/inventory/{level}` | `ANALYTICS_ADVANCED`, `INVENTORY_VIEW` |
| GET | `/analytics/drill/customers/{level}` | `ANALYTICS_ADVANCED`, `CRM_ANALYTICS_VIEW`, `CUSTOMER_VIEW` |
| GET | `/analytics/drill/expenses/{level}` | `ANALYTICS_ADVANCED`, `FINANCE_EXPENSE_VIEW` |
| GET | `/analytics/drill/suppliers/{level}` | `ANALYTICS_ADVANCED`, `SUPPLIER_VIEW`, `PURCHASE_VIEW` |
| GET | `/analytics/drill/finance/{level}` | `ANALYTICS_ADVANCED`, `FINANCE_VIEW` |
| GET | `/analytics/builder/datasets` | `ANALYTICS_CUSTOM_REPORT` |
| POST | `/analytics/builder/preview` | `ANALYTICS_CUSTOM_REPORT` |
| GET | `/analytics/reports` | `ANALYTICS_CUSTOM_REPORT` |
| POST | `/analytics/reports` | `ANALYTICS_CUSTOM_REPORT` |
| GET | `/analytics/reports/{report_id}` | `ANALYTICS_CUSTOM_REPORT` |
| PUT | `/analytics/reports/{report_id}` | `ANALYTICS_CUSTOM_REPORT` |
| POST | `/analytics/reports/{report_id}/archive` | `ANALYTICS_CUSTOM_REPORT` |
| POST | `/analytics/reports/{report_id}/restore` | `ANALYTICS_CUSTOM_REPORT` |
| GET | `/analytics/reports/{report_id}/run` | `ANALYTICS_CUSTOM_REPORT` |
| GET | `/analytics/suppliers/overview` | `ANALYTICS_ADVANCED`, `SUPPLIER_VIEW`, `PURCHASE_VIEW` |
| GET | `/analytics/suppliers/spend` | `ANALYTICS_ADVANCED`, `SUPPLIER_VIEW`, `PURCHASE_VIEW` |
| GET | `/analytics/suppliers/products` | `ANALYTICS_ADVANCED`, `SUPPLIER_VIEW`, `PURCHASE_VIEW` |
| GET | `/analytics/suppliers/cost-trend` | `ANALYTICS_ADVANCED`, `SUPPLIER_VIEW`, `PURCHASE_VIEW` |
| GET | `/analytics/suppliers/returns` | `ANALYTICS_ADVANCED`, `SUPPLIER_VIEW`, `PURCHASE_VIEW` |
| GET | `/analytics/finance/summary` | `ANALYTICS_ADVANCED`, `FINANCE_VIEW` |
| GET | `/analytics/finance/trend` | `ANALYTICS_ADVANCED`, `FINANCE_VIEW` |
| GET | `/analytics/finance/expenses` | `ANALYTICS_ADVANCED`, `FINANCE_VIEW` |
| GET | `/analytics/finance/expense-trend` | `ANALYTICS_ADVANCED`, `FINANCE_VIEW` |
| GET | `/analytics/finance/payment-mix` | `ANALYTICS_ADVANCED`, `FINANCE_VIEW` |
| GET | `/analytics/finance/receivables-aging` | `ANALYTICS_ADVANCED`, `FINANCE_VIEW` |
| GET | `/analytics/finance/payables-aging` | `ANALYTICS_ADVANCED`, `FINANCE_VIEW` |
| GET | `/analytics/finance/tax` | `ANALYTICS_ADVANCED`, `FINANCE_VIEW` |
| GET | `/analytics/sales/summary` | `ANALYTICS_VIEW`, `REPORT_VIEW` |
| GET | `/analytics/sales/trend` | `ANALYTICS_VIEW`, `REPORT_VIEW` |
| GET | `/analytics/sales/products` | `ANALYTICS_VIEW`, `REPORT_VIEW` |
| GET | `/analytics/sales/categories` | `ANALYTICS_VIEW`, `REPORT_VIEW` |
| GET | `/analytics/sales/brands` | `ANALYTICS_VIEW`, `REPORT_VIEW` |
| GET | `/analytics/sales/channels` | `ANALYTICS_VIEW`, `REPORT_VIEW` |
| GET | `/analytics/sales/payment-methods` | `ANALYTICS_VIEW`, `REPORT_VIEW` |
| GET | `/analytics/sales/discounts` | `ANALYTICS_VIEW`, `REPORT_VIEW` |
| GET | `/analytics/sales/promotions` | `ANALYTICS_VIEW`, `REPORT_VIEW` |
<!-- /API-TABLE -->

Every list report returns `{columns, rows, total, limit, offset, title, period, comparison, notes, filters_applied,
filters_ignored, source}`; money and quantities are strings. The exports are `GET /analytics/export/{report_key}` (see below).

## Executive dashboard

Eleven sections (overview, sales, profitability, inventory, customers, suppliers, finance, online orders, promotions, CRM, operational health).
Each shows the current value, the value for the previous comparable period, the absolute and percentage change and, where useful, a
trend. A KPI whose data permission the caller lacks is **hidden and counted**, not shown as zero. The dashboard uses the same
`kpis.compute` as `GET /analytics/kpis` and the KPI export, so the three always agree (tested).

## Analytics by area

* **Sales.** Trends by day/week/month, top and slowest products, category, brand, channel, payment method, discounts, and promotion-linked
  sales (the revenue of whole bills that used a promotion, "associated with", never "caused by"). Average transaction value, units per
  transaction (detailed bills, whole-unit products only), return rate. Profit per product appears only when every sold and returned line
  has a known cost.
* **Inventory.** The inventory ledger is the source of truth. Stock, value (average cost from the costing service; stock with unknown cost
  is left out and counted), movement, turnover, fast/slow/dead movers, ageing, purchases against sales, adjustment trends, stock-count
  variance, reorder recommendations and stock-out history (rebuilt from the running ledger balance).
* **Customers and CRM.** New, returning, average value, frequency, retention (the retention service), segments (they overlap: rows must
  not be added), loyalty, campaigns (sends are `NOT_CONFIGURED`, never "delivered"; no revenue is attributed to a campaign) and referrals.
  Only recency, frequency, spend and credit are used: nothing sensitive is inferred.
* **Cohorts.** See [ANALYTICS_COHORTS.md](ANALYTICS_COHORTS.md).
* **Suppliers and purchases.** Spend, purchases, returns, products supplied, average and latest unit cost, cost change between the first and
  latest purchase in the period, and how concentrated the spend is. **No quality, reliability or delivery scores exist** (no such data is
  recorded) and none is computed or implied.
* **Finance.** Wrappers over Phase 15: revenue, COGS, gross profit and margin, posted expenses, net profit, cash in/out/net, receivables and
  payables ageing, payment mix, tax-ready summary. Traceable through drill-down to the ledger and the source document. A trend is limited
  to 62 steps (each step is a full profit-and-loss calculation), so a daily view of several years is refused with a clear message.
* **Observations across modules.** Seven factual observations (best sellers with low stock, revenue and purchase-cost movement, revenue by
  segment, promotion-associated revenue, online repeat customers (**Not Available**), revenue and gross profit, inventory value and sales).
  Each names its sources and limitations; one whose data permission is missing is hidden, not computed.

## Drill-down

`revenue`: months > days > bills > bill lines; `inventory`: categories > products > stock movements; `customers`: segments > customers >
purchases > bill; `expenses`: categories > expenses > the expense and its ledger entries; `suppliers`: suppliers > purchases > lines;
`finance`: figures > ledger rows > source document. Each row carries a `drill` value that opens the next level (`null` at the last level),
each level re-checks shop and permission, and totals reconcile down the path (tested).

## Exports

`GET /analytics/export/{report_key}?format=csv|xlsx|pdf&columns=a,b&group_by=col&title=...` plus the usual filters. It runs the *same*
function as the screen, so a file cannot differ from the screen. It needs `ANALYTICS_EXPORT` **and** the report's own permissions; each
export is metered, rate limited and audited like the older exports; custom reports export as `saved-<id>`. `GET /analytics/export` lists
what the caller may export.

* **CSV:** UTF-8 with BOM, a header block (title, shop, period, comparison, filters used/ignored, source, generated time), the table, then
  the notes. Text that could be read as a formula (`=`, `+`, `-`, `@`, tab, carriage return) gets a leading apostrophe.
* **XLSX:** real number and date cells, number formats, frozen header row, fitted column widths, the same header block and notes.
* **PDF:** built in, no extra dependency; A4 landscape, monospaced font so columns line up, repeated header row, period, generated time and
  "Page X of Y". Only Latin-1 text can be drawn with the standard fonts: other scripts (for example Devanagari) print as `?`, so use CSV
  or Excel for such data.
* A blank cell means **Not Available**, never zero; the file says so. `group_by` sorts by one column and adds subtotals and a grand
  total (percent, date and text columns are not added; a subtotal is blank if any value in the group is unknown).

## RBAC

| Permission | Meaning | Owner | Manager | Accountant |
| --- | --- | --- | --- | --- |
| `ANALYTICS_VIEW` | KPIs and standard sales analytics | yes | yes | yes |
| `ANALYTICS_ADVANCED` | Inventory, customer, supplier, finance analytics, cohorts, observations, drill-down | yes | yes | yes |
| `ANALYTICS_EXECUTIVE` | Executive dashboard | yes | yes | no |
| `ANALYTICS_CUSTOM_REPORT` | Build, save and run custom reports | yes | yes | no |
| `ANALYTICS_EXPORT` | Analytics exports | yes | yes | yes |
| `ANALYTICS_SCHEDULE` | Schedule advanced reports | yes | yes | no |

Finance analytics also need `FINANCE_VIEW`; customer analytics `CRM_ANALYTICS_VIEW` (and `CUSTOMER_VIEW` for names in drill-down and
builder rows); inventory `INVENTORY_VIEW`; suppliers `SUPPLIER_VIEW` and `PURCHASE_VIEW`; sales `REPORT_VIEW`. Migration `0019` grants them to
the system roles; custom roles are edited on the Roles screen.

## Performance

Measured by `tests/test_phase16_performance.py` (run it with `-s` to print the numbers) on SQLite with 3,000 products, 1,500 customers,
20,000 detailed sales, 5,000 quick sales, 600 purchases and about 23,000 stock-ledger rows spread over 20 months, for a full-year period with a
comparison:

| Report | Time | Queries |
| --- | --- | --- |
| All 31 KPIs (current and previous period) | about 1.3 s | about 170 |
| Executive dashboard | about 1.4 s | about 180 |
| Sales summary / trend / products / categories | 5-15 ms | 2-5 |
| Inventory stock / turnover / dead / ageing / stock-outs | 40-100 ms | 2-7 |
| Customers overview / list / segments; cohorts | 4-75 ms | 2-10 |
| Suppliers spend / products | 1-2 ms | 1-4 |
| Finance summary | about 0.5 s | 59 |
| Finance trend by month (12 steps) | about 0.3 s | about 240 |
| Cross-module observations | about 0.8 s | about 110 |
| Custom report over 25,000 sales rows (grouped) | about 50 ms | 3 |

Findings and decisions:

* **No index was added.** The hot filters (sales, quick sales and purchases by shop, status and date; the stock ledger by product and date; lines
  by document) are already served by indexes (`EXPLAIN QUERY PLAN` is asserted in the test).
* **No N+1 by product or customer.** Query counts do not grow with the number of products or customers (asserted).
* **The one real cost driver was a trend over many steps.** A Phase 15 trend runs a full profit-and-loss and cash-flow calculation per step:
  a *daily* trend of a year measured 3.7 s and about 9,900 queries. It is now refused beyond 62 steps, and the builder's Finance dataset picks
  day, week or month by the length of the period.
* **Caching and background generation were not added**: nothing measured needs them at this size. The KPI snapshot memoises inside one
  request. On PostgreSQL the same queries apply; revisit with real data volumes.
* The builder reads at most 20,000 rows per run and says so when a period is truncated.

## Known limitations

* No online orders exist, so online figures are **Not Available** everywhere they would appear.
* No delivery provider is bundled: scheduled reports are kept in the app; an email request is recorded as *Delivery Channel Not Configured*.
* Ageing is by transaction date (no due dates or payment terms are stored), so nothing is called "overdue".
* Supplier quality, reliability and delivery performance are not measured; no scores are shown.
* Split payments across methods are not recorded per method, so payment-method analytics use each bill's single recorded method.
* PDF uses standard Latin-1 fonts (see above).
* A KPI or report cannot be filtered by a dimension that does not apply to it; the report says which filters were ignored.
