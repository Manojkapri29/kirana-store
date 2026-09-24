# Profit and loss

    Revenue         = Detailed Sales + Quick Sales - Sales Returns   (posted, by their own date)
    COGS            = stored cost snapshots on detailed-sale lines - cost of goods returned
    Gross profit    = Revenue - COGS
    Operating exp.  = posted expenses (a void is netted out; drafts/submitted/approved/rejected never count)
    Net profit      = Gross profit - Operating expenses
    Gross margin %  = Gross profit / Revenue x 100

Data: `sales_report_service.sales_summary` (the same figures as the Reports screen) and posted expense ledger entries.

**Honesty rules** (`status`): `ACTUAL` only when *every* rupee of revenue has a known cost. A Quick Sale has revenue but **no
cost** and no product-level profit is ever invented; a detailed sale with any unknown line cost is left out (an unknown cost is never
zero). Otherwise `NOT_AVAILABLE` ("Insufficient Cost Data"): COGS, gross profit, net profit and margin are null and the note says
"Profit Not Available". `costed_sales` (status `PARTIAL`) separately reports the profit on the fully-costed detailed sales and the
share of revenue it covers, clearly labelled as a subset. Other income is shown but is not part of net profit as defined. There are no
online orders in this application, so nothing is added for them (and none could be double-counted).

`GET /finance/pnl`, `GET /finance/pnl/trend?granularity=day|week|month`.
