# Shop Manager

A multi-business **small shop management** platform for India: stock, purchases, detailed and quick sales,
returns, suppliers, customer credit (khata), expenses, reports and exports, for many kinds of shops and
vendors. Grocery / Kirana is one supported business type, alongside sweet shops, bakeries, fruit and vegetable
vendors, dairies, garments, footwear, electronics, hardware, stationery and more. Built to start as a
single-shop app and grow into a multi-shop SaaS.

> The project folder and a few internal names (`kirana-store/`, the `KIRANA_` environment prefix,
> `kirana.db`) are the original working name. They are internal only and will be renamed later.

> **Status: Phase 7 of 16 (detailed sales).** You choose your type of business, manage suppliers, products
> and categories, record opening stock, **make product-wise bills** (search or scan, discounts, cash, UPI or
> credit; stock and profit are worked out for you), keep **customers and their khata** (opening balance, payments, advances,
> adjustments, reversals, a full ledger history), and **record purchases**: draft a purchase, post it (the stock is added and
> the average cost updated), void or correct it. You see current stock (In / Low / Out of stock), stock history
> (each purchase is linked) and the average cost, and can export products, inventory, history and purchases to
> CSV or Excel. Sales, khata and the rest come in later phases.
> See [docs/ROADMAP.md](docs/ROADMAP.md).

## Stack

| Layer | Technology |
|---|---|
| Frontend | React 19, Vite, TypeScript, Tailwind CSS 4, react-i18next (English/Hindi) |
| Backend | Python, FastAPI, Pydantic settings |
| Database | SQLAlchemy 2 + Alembic on SQLite for the MVP; PostgreSQL later (nothing to install now) |

TanStack Query (server state) and openpyxl (Excel export) arrived in Phase 3; Recharts is added in Phase 12, when the first chart is built.

## Prerequisites

- Python 3.11+ (developed on 3.13)
- Node.js 20+ (developed on 22) and npm

No database server and no Docker are needed.

## Quick start

Run the backend and the frontend in two terminals, starting from this folder.

**Backend** (http://127.0.0.1:8000)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head        # creates backend/data/kirana.db (git-ignored)
python -m app.seed          # optional: development shop + owner user
uvicorn app.main:app --reload
```

Check it: `curl http://127.0.0.1:8000/health` returns `{"status":"ok"}`.
Interactive API docs are at http://127.0.0.1:8000/docs (development only).

**Frontend** (http://localhost:5173)

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

In development the Vite dev server forwards `/api` and `/health` to the backend, so the browser
needs no CORS setup. The header shows a green "Server connected" badge when the backend is reachable.

There is no login yet: until Phase 14 every request acts as the development owner created by
`python -m app.seed`, so run the seed once. The backend refuses to serve business data in production
mode without real authentication.

**Try it:** open http://localhost:5173, add a category and a product (with opening stock if you like), then
look at Inventory and the product page. API documentation for every endpoint is at
http://127.0.0.1:8000/docs while the backend runs.

## Checks

```bash
# Backend (from backend/, venv active)
pytest                 # tests
alembic check          # models and migrations agree
ruff check .           # lint
ruff format --check .  # formatting

# Frontend (from frontend/)
npm run typecheck      # TypeScript
npm run lint           # oxlint
npm run build          # type-check + production build
```

## Project layout

```
kirana-store/
├── backend/     FastAPI app (app/), migrations/, tests/, requirements, .env.example
├── frontend/    React + Vite + TypeScript app (src/), .env.example
└── docs/        ARCHITECTURE, DATABASE, BUSINESS_RULES, ROADMAP
```

## Configuration

All configuration comes from environment variables; nothing secret is committed.

- Backend: `backend/.env` (copy from `.env.example`), variables prefixed `KIRANA_`.
- Frontend: `frontend/.env.local` (copy from `.env.example`), variables prefixed `VITE_`.
  Anything prefixed `VITE_` is visible to the browser, so never put secrets there.

## Documentation

- [Architecture](docs/ARCHITECTURE.md): layers, service boundaries, ledgers, SaaS readiness
- [Database](docs/DATABASE.md): planned schema and SQLite to PostgreSQL strategy
- [Business rules](docs/BUSINESS_RULES.md): the approved rules every phase must follow
- [Roadmap](docs/ROADMAP.md): Phases 1 to 16

## What Phase 8 added

Quick Sales (money only), barcode scanning on the billing screen, outside price checks (information only), offers,
discounts and coupons, plans with limits (no payments), and Detailed/Quick/Combined sales reports. Outside
lookups are optional: the app works fully with none of them configured.

### Optional external lookups (backend only)

Set these in `backend/.env` (see `backend/.env.example`; the file is git-ignored). They are never sent to the
browser, never stored in the database and never put in a `VITE_*` variable.

| Variable | Meaning |
|---|---|
| `UPCITEMDB_API_KEY` | UPCitemdb key. Leave the placeholder or empty and the provider shows "not configured". |
| `KIRANA_EXTERNAL_LOOKUPS_ENABLED` | `false` stops every outside request. |
| `KIRANA_EXTERNAL_TIMEOUT_SECONDS` | How long to wait for a provider (default 4). |
| `KIRANA_PRICE_CACHE_TTL_HOURS` | How long a saved outside price is reused (default 24). |
| `KIRANA_OFF_USER_AGENT` | The name and contact Open Food Facts asks apps to send. |

Open Food Facts (product identity) and Open Prices (crowd-sourced shelf prices) need no key; their coverage in India
is limited, and a barcode API does not promise a price. Prices found are information only and never change your own.

### Plans

Plans, features and limits are data. In development, from `backend/`:

```bash
python -m app.subscription_admin plans
python -m app.subscription_admin assign --shop 1 --plan pro
python -m app.subscription_admin show --shop 1
```

There is no payment processing anywhere; the plan screen says "Contact admin".
