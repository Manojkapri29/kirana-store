# Finance, accounting and business control (Phase 15)

Finance is a **consolidation layer over the events the system already records**. It is deliberately not a second
accounting system: sales, quick sales, purchases, returns, khata (customer balances), suppliers and inventory stay in the
tables and services that own them, and finance reads them.

## What owns what

| Question | Source of truth | Finance's role |
|---|---|---|
| What does a customer owe? | **Khata** (`khata_service`, `customer_ledger`) | Reads it (receivables, ageing). No second balance. |
| How much stock is there / what did it cost? | **Inventory** and the costing service (cost snapshots on sale lines) | Reads the stored cost snapshots for COGS. Writes nothing to stock. |
| What was sold / bought / returned? | Sales, Quick Sales, Purchases, Returns | Reads posted documents by their own date. |
| Expenses, supplier payments, owner capital/withdrawal, other income, adjustments | **Finance** (`expenses`, `finance_entries`) | Owns these. Insert-only, reversed not edited. |
| Physical cash counts | Finance (`cash_counts`, insert-only) | Owns it. |

A quick sale **never** creates inventory or product-level cost. A missing cost is **never** turned into zero.

## Layers

`routers (app/api/v1/finance*.py)` → `services` → models. Services: `finance_settings_service`, `finance_period_service`,
`finance_ledger_service`, `expense_service`, `cash_service`, `payables_service`, `receivables_service`, `pnl_service`,
`cashflow_service`, `tax_service`, `reconciliation_service`, `finance_dashboard_service`, `finance_alert_service`. They reuse
`approval_service` (the Phase 13 queue), `audit_service`, `notification_service`, `numbering_service`, `idempotency`
(`run_idempotent`), `export_datasets` and the existing AI tool architecture.

## Money

Integer paise in the database (`Money` type), exact `Decimal` in code, strings in JSON. Floats and text amounts are refused by
the services. Every calculation is deterministic (same data, same answer).

## Data model (migration `0018_finance`)

`finance_entries` (insert-only), `cash_counts` (insert-only), `reconciliation_marks` (insert-only), `financial_periods`,
`tax_settings`, `tax_rates`, `finance_settings`; `expenses` is rebuilt (the Phase 1 table was never written to; the migration
refuses to run if it holds rows) and `expense_categories` gains an optional `cash_flow_class`. Payment methods for
finance-owned records: CASH, UPI, CARD, BANK_TRANSFER, OTHER. **CREDIT is not a payment method**: a credit sale is unpaid and
lives in khata. Sales, purchases and khata keep their existing CASH/UPI/OTHER.

## Approval and control

Reuses the Phase 13 approval queue. Configurable (per shop, `finance_settings`; blank = off, no built-in numbers):
`expense_approval_threshold`, `adjustment_approval_threshold`, `cash_adjustment_threshold`, `period_reopen_requires_approval`.
The submitter/requester can never approve. The shared `/approvals` endpoints now authorise **by request kind**
(stock counts, campaigns, loyalty, expenses, finance adjustments, period reopening each need their own permission), so a stock
counter can no longer decide an expense.

## Phase 15 implementation checklist (P0 findings)

* Existing before Phase 15: sales/quick sales/purchases/returns with payment method (CASH/UPI/OTHER) and amount paid; khata;
  cost snapshots; `sales_report_service` (revenue, costed profit); the approval queue; audit log; notifications
  (`BUSINESS_ALERT`); exports; the AI tool pipeline; an **empty** `expenses` table with no service.
* Not existing: expense workflow, supplier payments, cash counts, periods, tax, reconciliation, finance dashboards, online
  orders (none exist in this application, so nothing is added for them).
* Built: P1–P16 as described in the documents linked below; P17 tests; P18 documentation; P19 audit (see the README).

Documents: [Ledger](FINANCE_LEDGER.md), [Expenses](EXPENSES.md), [Cash](CASH_MANAGEMENT.md), [Profit and loss](PROFIT_AND_LOSS.md),
[Cash flow](CASH_FLOW.md), [Tax](TAX_REPORTING.md), [Periods](PERIOD_CONTROLS.md), [Reconciliation](RECONCILIATION.md),
[Dashboard and alerts](FINANCE_DASHBOARD.md), [AI tools](FINANCE_AI_TOOLS.md), [RBAC](RBAC.md).

## Known limitations

* No frontend for approving a request outside the expense screen (the shared `/approvals` API exists).
* Sales/purchases record ONE payment method per document; a split payment across methods cannot be shown per method.
* Suppliers have no payment terms and customers no due dates, so ageing is by transaction date (labelled).
* Purchases carry no payment write path: supplier payments are recorded as finance entries.
* No bank integration; nothing is matched to a bank statement automatically.
* Tax is a reporting foundation: rates per product category (plus one default); quick sales cannot be classified.
* Voiding a source document later changes past days of derived reports (until the period is closed, which forbids it).
