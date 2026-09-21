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
python -m app.seed          # development shop + owner; PRINTS the owner's password once
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

**Sign in** with `owner@dev.kirana.local` and the password the seed printed (set `KIRANA_DEV_OWNER_PASSWORD`
before seeding to choose your own; if you lost it, delete nothing: run `python -m app.account_cli set-password
--email owner@dev.kirana.local`). Every screen needs a sign-in; see [Authentication](docs/AUTHENTICATION.md).

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
npm test               # unit and component tests (vitest)
```

## Project layout

```
kirana-store/
├── backend/     FastAPI app (app/), migrations/, tests/, requirements, .env.example
├── frontend/    React + Vite + TypeScript app (src/), .env.example
└── docs/        ARCHITECTURE, DATABASE, BUSINESS_RULES, ROADMAP, PRODUCTION, SECURITY, ...
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
- [Roadmap](docs/ROADMAP.md): Phases 1 to 16 (Phase 12 is the latest)
- [Production](docs/PRODUCTION.md), [Security](docs/SECURITY.md), [Observability](docs/OBSERVABILITY.md)
- [Backup and restore](docs/BACKUP_AND_RESTORE.md), [Notifications](docs/NOTIFICATIONS.md), [SaaS administration](docs/SAAS_ADMIN.md)
- [PostgreSQL migration checklist](docs/POSTGRES_MIGRATION_CHECKLIST.md)
- [Authentication](docs/AUTHENTICATION.md), [Roles and permissions](docs/RBAC.md), [Staff management](docs/STAFF_MANAGEMENT.md)
- [Production deployment](docs/PRODUCTION_DEPLOYMENT.md), [Monitoring](docs/MONITORING.md), [Background jobs](docs/BACKGROUND_JOBS.md)

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

## What Phase 9 added

**Returns.** Take goods back from a completed sale, or send goods back to a supplier, from the sale or purchase page
("Return items") or the new **Returns** page. The refund is worked out for you, in proportion to what was paid, and can
be given as cash, UPI or a credit on the customer's khata. Nothing is deleted: a return can be voided, and both the return
and the original stay on record.

**Smart error recovery.** If something goes wrong, you see a plain message, what to do next and (for an unexpected
problem) a reference such as `ERR-20260921-A82F5` to quote. The real cause is written, with secrets removed, to the
server's log; nobody sees a stack trace. What you typed is kept, and "Try again" is offered only when repeating is safe:
the same attempt carries a key, so a sale or return is never recorded twice. Unsaved forms are also kept in the browser for a
day and offered back after a crash or reload. If a part of a page crashes, you get "This section couldn't be loaded."
with Try again, Go to Dashboard and Reload.

**Add a product from a photo (optional).** Take or choose a photo on **Products > Add from photo**. What the photo shows
is offered as *Detected* or *Suggested*, checked against your existing products ("Possible existing product found"), and
nothing is created until you review the boxes and press **Confirm & Create Product**. A photo never changes stock,
prices or khata. You can keep the photo with the product (private to your shop) or not.

Image analysis needs a provider, and **none is bundled**: until one is configured the screen says "Image analysis is not
configured yet." and everything else (including reading a barcode in the browser and looking it up) works.

### Settings added in Phase 9 (backend only)

| Variable | Meaning |
|---|---|
| `KIRANA_DIAGNOSTICS_LOG_FILE` | Optional file for the redacted error log (JSON lines). Keep it private. |
| `KIRANA_IMAGE_MAX_BYTES`, `KIRANA_IMAGE_MAX_SIDE` | The largest photo accepted (bytes, and pixels on the longest side). |
| `KIRANA_IMAGE_STORAGE_DIR` | Where kept photos are stored (private, git-ignored, never served directly). |
| `KIRANA_IMAGE_MAX_PER_SHOP` | How many photos a shop may keep. |
| `KIRANA_IMAGE_ANALYSIS_PROVIDER`, `IMAGE_ANALYSIS_API_KEY` | For a future provider. The key stays in `backend/.env`; it is never sent to the browser or committed. |

The `image_intelligence` plan feature controls who sees the photo tools (on for the example `pro` plan).

## What Phase 10 added

**Business Assistant.** Ask about your sales, stock, Khata, purchases, profit, discounts and more, in English, Hinglish
or Hindi ("Aaj kitni sale hui?"). Every number comes from your own records through fixed read-only reports; the assistant
cannot run database commands, cannot see another shop, and cannot change anything. Where data is missing it says so
("Profit Not Available because cost data is missing."). It also gives reorder and purchase suggestions, unusual-activity
notices, offer ideas and a monthly summary, each labelled **AI Recommendation** and for you to review.

**Confirm before anything happens.** From a suggestion you can prepare a *draft*: a purchase draft, a stock adjustment, or
an offer draft. You see exactly what it will do (Confirm / Edit / Cancel). Only Confirm runs the normal purchase, inventory
or offer code, and the result is a draft (it is never posted or activated for you). Every step is in the audit log.

**Documents.** Photograph a supplier invoice or a counted stock list, or type the lines yourself. Each line is checked and
matched to your products (Matched, Possible Match, New Product Candidate); nothing is created until you confirm. Text on
a document is only copied, never followed.

**AI provider (optional).** No provider is required. Without one, the ready-made questions, reports and typed document
lines all work, and the assistant says "AI Assistant is not configured." for free-form wording. To turn on free-form
questions and photo reading, set these in `backend/.env` (never in the frontend or Git):

| Variable | Meaning |
|---|---|
| `KIRANA_AI_PROVIDER` | `anthropic` today. |
| `AI_API_KEY` | The provider key. Backend only; never sent to the browser, stored, or logged. |
| `KIRANA_AI_MODEL` | The provider's model name (a default is used when unset). |
| `KIRANA_AI_TIMEOUT_SECONDS`, `KIRANA_AI_MAX_OUTPUT_TOKENS` | Limits for one request. |

AI is controlled by plan: `ai_assistant` (basic questions), `ai_insights` (recommendations and analysis) and
`ai_documents` (document photos), with a monthly limit of requests. The conversation is kept only in your browser tab.
Online orders are not part of this application yet, so the assistant reports none rather than inventing figures.

## What Phase 11 added

**Ready to run for real.** A production start-up check (the application refuses to start with a missing secret or unsafe
settings, naming the setting and never its value), `/health`, `/health/live`, `/health/ready`, request ids on every response
and log line, structured logs that never carry secrets, configurable rate limits (HTTP 429 with `Retry-After`), and feature
switches. See [docs/PRODUCTION.md](docs/PRODUCTION.md).

**For the shop owner.** A notification bell and inbox (in-app is real; email, SMS, WhatsApp and push say "Not set up yet"),
a dashboard and Insights page built from your own records ("Not Available" where cost is missing), plan usage, a backup
status line, calm messages when the shop account is restricted or the connection is down, "Contact support" that copies the
reference, and a warning before you close a tab with unsaved work.

**For the people who run the platform.** Administrators with roles (token based; command line to create), shop suspension
and reactivation with a reason, plans without payments, backups you can verify and rehearse, a read-only integrity check, and
an audited console at `/admin` that never shows a shop's sales or customers. See [docs/SAAS_ADMIN.md](docs/SAAS_ADMIN.md)
and [docs/BACKUP_AND_RESTORE.md](docs/BACKUP_AND_RESTORE.md).

**Not done (on purpose).** An online store and online orders, payments and billing,
cloud backups, real email/SMS/WhatsApp providers, and PostgreSQL (a checklist only).

```bash
cd backend
.venv/bin/python -m app.backup_cli create        # a consistent, verified backup
.venv/bin/python -m app.integrity_cli            # read-only data check
.venv/bin/python -m app.admin_cli create --email ops@example.com --name "Ops" --role SUPER_ADMIN
```

## What Phase 12 added

**Several people, safely.** Sign-in (Argon2id passwords, HttpOnly session cookie, CSRF protection, idle and absolute expiry, throttling and a
temporary pause after repeated wrong passwords), shop **memberships** (one person can belong to several shops and chooses one), six ready-made **roles**
(Owner, Manager, Cashier, Inventory staff, Sales staff, Accountant) and your own custom ones, ~50 granular **permissions**, and **staff invitations** by
one-time link (no email is sent: you hand the link over). The server decides everything: every route has a permission rule and a test calls every route as every role.
The AI assistant and every export follow the same permissions. See [Authentication](docs/AUTHENTICATION.md), [RBAC](docs/RBAC.md) and
[Staff management](docs/STAFF_MANAGEMENT.md).

**Ready to deploy, and to watch.** Dockerfiles, an nginx config and a production-like compose file (unverified: no Docker was available to build them), a deployment
guide with HTTPS, proxy, database and migration notes, optional Prometheus-format metrics, and a database-backed background-job foundation with a worker.
See [Production deployment](docs/PRODUCTION_DEPLOYMENT.md), [Monitoring](docs/MONITORING.md) and [Background jobs](docs/BACKGROUND_JOBS.md).

**Operators.** `python -m app.account_cli create-shop ...` (the first shop and owner, with a one-time password), `set-password`, `unlock`, `disable`;
`python -m app.worker --loop --schedule`.

**Not done (on purpose).** Password reset by email, MFA, ownership transfer, email delivery of invitations, an online store and orders, payments.

