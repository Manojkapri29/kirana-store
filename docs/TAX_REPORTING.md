# Tax reporting foundation (GST/VAT/sales-tax ready)

**Not a tax return, not a compliance claim, and no country's rules are built in.** Reports carry the disclaimer "Tax reporting
foundation ... not a tax return or a statement of legal or tax compliance."

Configure (`PUT /finance/tax/settings`, `POST /finance/tax/rates`, `FINANCE_MANAGE`): tax type, registration number, state/location,
whether prices include tax, and rates in basis points per product category plus an optional default. Rates are switched off, never
deleted. Until settings exist the report says `NOT_CONFIGURED` and every tax figure is null.

Method (`GET /finance/tax/summary`): lines of Detailed Sales, Sales Returns, Purchases and Purchase Returns take their category's
active rate, else the default, else none (reported as *unclassified*, untaxed - never an assumed rate). A bill-level discount is
spread pro rata over its lines. Inclusive: taxable = amount x 10000 / (10000 + rate_bp); exclusive: tax = amount x rate_bp / 10000;
rounded half-up. `tax_collected` = tax on sales - tax on returns; `tax_paid` likewise for purchases; `net_tax` is indicative. Quick Sales
have no product lines, so their tax is **Not Available**.
