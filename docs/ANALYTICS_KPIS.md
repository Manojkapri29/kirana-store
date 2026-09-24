# Analytics KPIs: formula and source of every KPI

Generated from the KPI registry in `backend/app/reporting/kpis.py` (a test, `tests/test_phase16_docs.py`, fails if this page and the registry drift apart). 
Every KPI has a name, description, formula, source, unit, period behaviour, comparison behaviour, availability rules and limitations. 
A KPI is never invented: when the data is not there it is reported as **Not Available** or **Insufficient Data**, never as zero.

## How a KPI is reported

* **Period.** The period comes from a preset or two dates (see [ANALYTICS.md](ANALYTICS.md)). Flow KPIs (revenue, expenses...) are for the period; *position* KPIs (inventory value, receivables, payables) are *as of the end of the period* and are marked `as_of_only`.
* **Comparison.** The previous comparable period (or the same period last year) is computed with the same function. Change is shown as an absolute difference and a percentage (percent KPIs change in *percentage points*). If the earlier value is zero or unknown the change is **Insufficient comparison data**, never an invented growth figure. Position KPIs are not compared.
* **Availability.** `AVAILABLE`, `NOT_AVAILABLE` (the system has no such data, for example online orders), or `INSUFFICIENT_DATA` (there is data but not enough to compute honestly).
* **Permission.** Each KPI is visible only to a role that holds the permission of the data behind it; others are listed as hidden, never blanked to zero.

## Sales

| KPI (key) | Unit | Description | Formula | Source | Permission | Limitations |
| --- | --- | --- | --- | --- | --- | --- |
| **Revenue** (`revenue`) | money | Money earned from sales after returns. | Detailed sales + Quick sales - Sales returns | Posted sales, quick sales and sales returns | `REPORT_VIEW` | Combined revenue; Quick Sales have no product detail. |
| **Orders** (`orders`) | count | Number of posted sales transactions. | Count of posted detailed sales + posted quick sales | Posted sales and quick sales | `REPORT_VIEW` | One quick sale is one transaction, however many items it covered. |
| **Units sold** (`units_sold`) | quantity | Units of count-sold products sold, less returned. | Sum of sold quantity - returned quantity, for products sold by whole units | Detailed sale lines and sales return lines | `REPORT_VIEW` | Products sold by weight or volume are not summed with pieces. Quick Sales have no product quantity. |
| **Average transaction value** (`average_transaction_value`) | money | Average size of a sale. | (Detailed net + Quick net) / number of transactions | Posted sales and quick sales | `REPORT_VIEW` | Before returns. |
| **Sales growth** (`sales_growth`) | percent | Change in revenue against the comparison period. | (Revenue - comparison revenue) / comparison revenue x 100 | Revenue KPI for both periods | `REPORT_VIEW` | Needs a comparison period with revenue above zero. |

## Profitability

| KPI (key) | Unit | Description | Formula | Source | Permission | Limitations |
| --- | --- | --- | --- | --- | --- | --- |
| **Cost of goods sold** (`cogs`) | money | Cost of the goods sold. | Cost snapshots stored on detailed-sale lines, less cost of returned goods | Detailed sale line cost snapshots (costing service) | `FINANCE_VIEW` | Available only when every sale in the period has a known cost; Quick Sales have no cost. |
| **Gross profit** (`gross_profit`) | money | Revenue less cost of goods sold. | Revenue - COGS | Profit and loss | `FINANCE_VIEW` | Profit Not Available when any cost is unknown. |
| **Gross margin** (`gross_margin`) | percent | Gross profit as a share of revenue. | Gross profit / Revenue x 100 | Profit and loss | `FINANCE_VIEW` | Profit Not Available when any cost is unknown. |
| **Net profit** (`net_profit`) | money | Gross profit less posted expenses. | Gross profit - posted operating expenses | Profit and loss | `FINANCE_VIEW` | Profit Not Available when any cost is unknown. |
| **Expense ratio** (`expense_ratio`) | percent | Posted expenses as a share of revenue. | Posted expenses / Revenue x 100 | Finance ledger and revenue | `FINANCE_VIEW` | Only posted expenses count. |

## Inventory

| KPI (key) | Unit | Description | Formula | Source | Permission | Limitations |
| --- | --- | --- | --- | --- | --- | --- |
| **Inventory value** (`inventory_value`) *(as of period end)* | money | Stock on hand valued at average cost. | Sum of (current stock x average cost) | Inventory ledger and the costing service | `INVENTORY_VIEW` | Current position only: past stock values are not recorded. Products with no known cost are left out. |
| **Stock turnover** (`stock_turnover`) | ratio | How many times the average stock was sold in the period. | Units sold / ((opening units + closing units) / 2) | Inventory transaction ledger and detailed sales | `INVENTORY_VIEW` | Counts only products sold by whole units; not annualised. |
| **Fast-moving products** (`fast_moving`) *(as of period end)* | count | Products that sold in the period and still have stock. | Count of products with units sold > 0 | Inventory intelligence over the period | `INVENTORY_VIEW` | Stock is the current position. |
| **Slow-moving products** (`slow_moving`) *(as of period end)* | count | Stock that would take unusually long to sell at the period's pace. | Count of products whose days of cover exceed 3x the period | Inventory intelligence over the period | `INVENTORY_VIEW` | A statement about pace, not about quality. |
| **Dead stock** (`dead_stock`) *(as of period end)* | count | Stock with no sales at all in the period. | Count of products with stock > 0 and no units sold | Inventory intelligence over the period | `INVENTORY_VIEW` | Depends on the chosen period length. |
| **Stock-outs** (`stock_out_count`) *(as of period end)* | count | Products currently out of stock. | Count of active products with stock <= 0 | Inventory ledger | `INVENTORY_VIEW` | Current position only. |

## Customers

| KPI (key) | Unit | Description | Formula | Source | Permission | Limitations |
| --- | --- | --- | --- | --- | --- | --- |
| **New customers** (`new_customers`) | count | Customers whose first purchase fell in the period. | Count of identified customers with first purchase in the period | Posted sales and quick sales that name a customer | `CUSTOMER_VIEW` | A walk-in sale with no customer is not counted. |
| **Returning customers** (`returning_customers`) | count | Customers who bought in the period and had bought before it. | Count of purchasers in the period whose first purchase is before the period | Posted sales and quick sales that name a customer | `CUSTOMER_VIEW` | Only identified customers. |
| **Repeat purchase rate** (`repeat_purchase_rate`) *(as of period end)* | percent | Customers who have bought at least twice. | Customers with 2+ purchases / customers with any purchase x 100 | Customer purchase history (retention service) | `CRM_ANALYTICS_VIEW` | Lifetime figure as of today, not limited to the period. |
| **Average customer value** (`average_customer_value`) | money | Average spend of a purchasing customer in the period. | Sales to identified customers / distinct identified purchasing customers | Posted sales and quick sales that name a customer | `CUSTOMER_VIEW` | Only identified customers; before returns. |
| **Inactive customers** (`inactive_customers`) *(as of period end)* | count | Customers who used to buy but have stopped. | Retention service definition (no recent purchase) | Customer purchase history (retention service) | `CRM_ANALYTICS_VIEW` | Current position as of today. |

## Finance

| KPI (key) | Unit | Description | Formula | Source | Permission | Limitations |
| --- | --- | --- | --- | --- | --- | --- |
| **Receivables** (`receivables`) | money | What customers owe the shop at the end of the period. | Sum of positive khata balances as of the period end | Khata (the customer ledger) | `FINANCE_VIEW` | Khata is the only source; ageing has no due dates. |
| **Payables** (`payables`) | money | What the shop owes suppliers at the end of the period. | Purchases - returns - supplier payments, as of the period end | Purchases, purchase returns and supplier payments | `FINANCE_VIEW` | Suppliers have no payment terms. |
| **Net cash flow** (`net_cash_flow`) | money | Money that actually came in less money that went out. | Inflows - outflows (settled amounts) | The financial ledger view | `FINANCE_VIEW` | A credit sale is not an inflow until it is paid. |
| **Expenses** (`expense_value`) | money | Posted expenses in the period. | Posted expense entries less reversals | The finance ledger | `FINANCE_EXPENSE_VIEW` | Drafts and unapproved expenses are not counted. |

## Suppliers

| KPI (key) | Unit | Description | Formula | Source | Permission | Limitations |
| --- | --- | --- | --- | --- | --- | --- |
| **Purchase value** (`purchase_value`) | money | Money spent on purchases after returns. | Posted purchases - purchase returns | Posted purchases and purchase returns | `PURCHASE_VIEW` | Purchase returns settled in cash or supplier credit both reduce it. |

## Operations

| KPI (key) | Unit | Description | Formula | Source | Permission | Limitations |
| --- | --- | --- | --- | --- | --- | --- |
| **Discounts given** (`discount_given`) | money | Discounts allowed on sales (line, bill and offers). | Line + bill + promotion discounts on detailed sales, plus quick-sale discounts | Posted sales and quick sales | `REPORT_VIEW` | A quick sale's discount is its single transaction discount. |
| **Online orders** (`online_orders`) | count | Orders placed online. | Not available | None: this application has no online orders | `REPORT_VIEW` | There is no online-order module. |
| **Return rate** (`return_rate`) | percent | Refunds as a share of sales. | Sales-return value / (Detailed net + Quick net) x 100 | Posted sales and sales returns | `REPORT_VIEW` | By value, not by number of returns. |
| **Promotion usage** (`promotion_usage`) | percent | Share of detailed sales that used an offer or coupon. | Sales with at least one promotion / detailed sales x 100 | Posted sales and their promotion snapshots | `PROMOTION_VIEW` | Quick Sales cannot use offers. |
| **Loyalty points issued** (`loyalty_activity`) | count | Loyalty points earned in the period. | Sum of EARN ledger points dated in the period | The loyalty ledger | `LOYALTY_VIEW` | Points, not money. |
