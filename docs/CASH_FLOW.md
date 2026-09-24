# Cash-flow reporting

From actual money movements (the ledger view's `settled_amount`): inflows, outflows, net, by class, by event and by payment method
(`GET /finance/cash-flow`, `/finance/cash-flow/trend`). A credit sale is not an inflow until the customer pays.

Classes are used only where justified: OPERATING (sales, purchases, returns, payments, expenses, other income), FINANCING (owner
capital/withdrawal), INVESTING or OTHER only when an expense category or entry says so, and **UNCLASSIFIED** for adjustments and
anything else - never forced into a class.
