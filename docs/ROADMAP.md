# Roadmap

The project is built in small, testable phases. Each phase ends with passing tests and a working
demo, and the next phase starts only after review. Later phases build on the ones before them, so the
order matters: for example, the stock ledger exists (Phase 3) before purchases and sales use it.

| # | Phase | Status |
|---|---|---|
| 1 | Project setup and foundation | Done, awaiting review |
| 2 | Database/models + SQLite + Alembic | Planned |
| 3 | Products + inventory ledger core + export framework | Planned |
| 4 | Suppliers | Planned |
| 5 | Purchases + weighted average cost | Planned |
| 6 | Customers + Khata | Planned |
| 7 | Detailed Sales | Planned |
| 8 | Quick/Daily Sales | Planned |
| 9 | Returns | Planned |
| 10 | Inventory views + adjustments + stock count + reliability indicators | Planned |
| ✔ | **PostgreSQL checkpoint** (after Phase 10) | Planned |
| 11 | Expenses | Planned |
| 12 | Dashboard | Planned |
| 13 | Reports | Planned |
| 14 | Authentication | Planned |
| 15 | Testing, validation and PostgreSQL gate | Planned |
| 16 | Deployment preparation | Planned |

## Phase details

### Phase 1: Project setup and foundation
FastAPI app with a `GET /health` endpoint, environment-based configuration, and a router structure.
React + Vite + TypeScript + Tailwind shell with a responsive sidebar, dashboard placeholder,
English/Hindi switching, and an API client layer. Documentation, `.gitignore`, `.env.example` files.
**Done when:** both apps start, `/health` works, the frontend builds and type-checks, tests and lint pass.

### Phase 2: Database/models + SQLite + Alembic
Database engine driven by `KIRANA_DATABASE_URL`, SQLite connection settings (foreign keys on, WAL,
`BEGIN IMMEDIATE` write transactions), the `Money`, `Quantity` and UTC timestamp column types,
all models from [DATABASE.md](DATABASE.md), the first Alembic migration (batch mode), the document
sequence table, seed data (units), and a temporary single-shop development context.
**Done when:** migrations apply and roll back on an empty database; money and quantity values
round-trip exactly; insert-only ledger protection is tested.

### Phase 3: Products + inventory ledger core + export framework
Categories and products (including MRP and barcode field), `OPENING` ledger entries,
`inventory_service` as the only ledger writer, the stock query, and the shared CSV/XLSX exporter with
Products and Inventory exports.
**Done when:** a product created with opening stock 20 reads 20; MRP validation follows the shop setting;
exports open cleanly in Excel.

### Phase 4: Suppliers
Supplier create/edit/deactivate, search, and export.
**Done when:** validation and duplicate-handling tests pass.

### Phase 5: Purchases + weighted average cost
Purchase entry with paid/unpaid amounts, void, the `costing` service (moving weighted average), and export.
**Done when:** opening 20 + purchase 30 = 50; voiding the purchase returns stock to 20; average cost is correct.

### Phase 6: Customers + Khata
Customers, `khata_service` as the only customer-ledger writer, opening balance, payment received,
running balance, customer history, and export.
**Done when:** outstanding balance always equals the sum of ledger entries.

### Phase 7: Detailed Sales
Product-wise cart, invoice numbering, oversell prevention, cost snapshot per line, paid or credit sales,
MRP checks, and export.
**Done when:** 50 - 5 = 45; selling 46 is rejected; two simultaneous sales of the last unit cannot both succeed.

### Phase 8: Quick/Daily Sales
Money-only entries (paid or credit) with a clear "stock is not reduced" notice, and export.
**Done when:** creating a quick sale never changes stock; exports show the sale mode.

### Phase 9: Returns
Sales returns and purchase returns that reference the original line, refund/credit modes, and cost
handling. Unbilled returns use adjustments (Phase 10).
**Done when:** return quantity caps, proportional refunds, void rules, and history preservation are tested.

### Phase 10: Inventory views + adjustments + stock count + reliability indicators
Inventory table (opening, purchased, sold, returns, adjustments, current), adjustments with reason
codes, a stock-count screen, the product detail history page, and the stock-reliability indicator
that reflects Quick Sales.
**Done when:** the product detail page reproduces the "what came in, what was sold, what remains" example exactly.

### PostgreSQL checkpoint (after Phase 10)
Run the whole test suite, including the concurrency test, against PostgreSQL and fix any dialect drift.
No PostgreSQL is installed before this point; the cheapest way to run it is decided then.

### Phase 11: Expenses
Expense categories and entries, today and monthly totals, and export.

### Phase 12: Dashboard
KPI cards and charts (Recharts), with sales trends split by mode and stock caveats.
**Done when:** dashboard numbers match a hand-calculated dataset.

### Phase 13: Reports
Sales and purchase reports (daily, weekly, monthly, custom range; Detailed/Quick/Combined),
inventory, product performance (detailed sales only), and the profit estimate under the profit rules.

### Phase 14: Authentication
Registration, login, token refresh, roles, and real shop scoping replacing the development context.
**Done when:** one shop can never read or write another shop's data.

### Phase 15: Testing, validation and PostgreSQL gate
Invariant and reconciliation tests over random transaction sequences, Hindi and mobile review,
and the full suite green on both SQLite and PostgreSQL.

### Phase 16: Deployment preparation
Containers, production configuration, backups, and a security review.

## Explicitly out of MVP scope
Payment reminders, GST invoicing, UPI/payment-gateway integration, barcode scanner integrations,
multiple shops per account, subscription billing, WhatsApp notifications, and the AI assistant. The
architecture leaves room for each (see [ARCHITECTURE.md](ARCHITECTURE.md)).
