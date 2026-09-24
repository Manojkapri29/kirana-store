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
- [Roadmap](docs/ROADMAP.md): Phases 1 to 16 (build Phase 14, CRM, is the latest; backend only, see "What Phase 14 added" below)
- [Production](docs/PRODUCTION.md), [Security](docs/SECURITY.md), [Observability](docs/OBSERVABILITY.md)
- [Backup and restore](docs/BACKUP_AND_RESTORE.md), [Notifications](docs/NOTIFICATIONS.md), [SaaS administration](docs/SAAS_ADMIN.md)
- [PostgreSQL migration checklist](docs/POSTGRES_MIGRATION_CHECKLIST.md)
- [Authentication](docs/AUTHENTICATION.md), [Roles and permissions](docs/RBAC.md), [Staff management](docs/STAFF_MANAGEMENT.md)
- [Production deployment](docs/PRODUCTION_DEPLOYMENT.md), [Monitoring](docs/MONITORING.md), [Background jobs](docs/BACKGROUND_JOBS.md)
- Phase 13: [Advanced inventory](docs/ADVANCED_INVENTORY.md), [Stock counting](docs/STOCK_COUNTING.md), [Reorder planning](docs/REORDER_PLANNING.md), [Purchase planning](docs/PURCHASE_PLANNING.md)
- Phase 13: [Supplier analytics](docs/SUPPLIER_ANALYTICS.md), [Customer analytics](docs/CUSTOMER_ANALYTICS.md), [Business health](docs/BUSINESS_HEALTH.md)
- Phase 13: [Workflow automation](docs/WORKFLOW_AUTOMATION.md), [Scheduled reports](docs/SCHEDULED_REPORTS.md)
- Phase 14: [CRM](docs/CRM.md), [Loyalty](docs/LOYALTY.md), [Campaigns and reactivation](docs/CAMPAIGNS.md), [Marketing automation](docs/MARKETING_AUTOMATION.md)
- Phase 15: [Finance](docs/FINANCE.md), [Ledger](docs/FINANCE_LEDGER.md), [Expenses](docs/EXPENSES.md), [Cash](docs/CASH_MANAGEMENT.md), [Profit and loss](docs/PROFIT_AND_LOSS.md), [Cash flow](docs/CASH_FLOW.md)
- Phase 15: [Tax reporting](docs/TAX_REPORTING.md), [Period controls](docs/PERIOD_CONTROLS.md), [Reconciliation](docs/RECONCILIATION.md), [Dashboard and alerts](docs/FINANCE_DASHBOARD.md), [Finance AI tools](docs/FINANCE_AI_TOOLS.md)
- Phase 16: [Analytics and business intelligence](docs/ANALYTICS.md), [KPI formulas and sources](docs/ANALYTICS_KPIS.md), [Cohort definitions](docs/ANALYTICS_COHORTS.md), [Report builder](docs/REPORT_BUILDER.md), [Analytics AI tools](docs/ANALYTICS_AI_TOOLS.md)
- Phase 17: [Integrations and ecosystem](docs/INTEGRATIONS.md)
- Phase 18: [PWA, mobile and offline](docs/PWA_OFFLINE.md)
- Phase 14: [Retention](docs/RETENTION.md), [Referrals and consent](docs/REFERRALS.md), [CRM AI tools](docs/CRM_AI_TOOLS.md)

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

## What Phase 13 added

**Business intelligence, read-only.** Inventory health, fast/slow/dead-moving products and stock aging, all with
configurable time periods; supplier analytics and price history; customer analytics and factual segments; a
business-health comparison of the current period against the previous one, reusing the existing anomaly detector.
Money is always "Not Available," never invented, when a cost is unknown. See
[Advanced inventory](docs/ADVANCED_INVENTORY.md), [Supplier analytics](docs/SUPPLIER_ANALYTICS.md),
[Customer analytics](docs/CUSTOMER_ANALYTICS.md), [Business health](docs/BUSINESS_HEALTH.md).

**Stock counting.** A full cycle-count workflow (create, count, review differences, approve, post), with a
required creator/approver split and an optional per-shop threshold that routes a large variance to a second
approval. Posting only ever goes through the existing `inventory_service`. See
[Stock counting](docs/STOCK_COUNTING.md).

**Reorder and purchase planning.** A transparent reorder formula (never a black box), respecting pack size and
minimum order quantity, that states its assumptions (an unset supplier lead time is called out, never assumed) —
turned into a purchase **draft** by the planning workspace, never auto-posted. See
[Reorder planning](docs/REORDER_PLANNING.md), [Purchase planning](docs/PURCHASE_PLANNING.md).

**Workflow automation.** Generic business tasks (assign, comment, complete) and a generic approval queue with a
strict no-self-approval rule, reused today for the stock-count variance gate. Scheduled daily/weekly/monthly
reports re-run the existing summaries and notify when ready — no email/SMS provider is bundled. See
[Workflow automation](docs/WORKFLOW_AUTOMATION.md), [Scheduled reports](docs/SCHEDULED_REPORTS.md).

**AI integration.** The assistant gained a `TASK_DRAFT` confirmable action and two new read-only tools (dead
stock, supplier analytics) — every number still comes from the services above, never invented, and every write
still goes through the propose → confirm → authorize → execute → audit pipeline from Phase 10.

**Not done (on purpose, this phase).** No frontend screens were built for Phase 13 — everything above is a backend
API and service layer only (see each linked doc's "Known limitations" and the Phase 13 audit for the full list).
No real email/SMS delivery for scheduled reports. No online-order or payment features (unchanged from Phase 12).

## What Phase 14 added

**CRM on the existing customer record.** Classification, tags, per-channel marketing consent (default off), a
profile and a read-only timeline, notes, factual segments and manual/rule-based groups. There is no second
customer table. See [CRM](docs/CRM.md).

**Loyalty.** A configurable program and an insert-only points ledger (balance = sum of the ledger, reversal on
sale void, idempotent, on-demand expiry). Only `loyalty_service` touches it. See [Loyalty](docs/LOYALTY.md).

**Campaigns, reactivation and automation.** Draft → schedule → launch lifecycle with audience snapshots and an
honest per-customer outcome — **no email/SMS/WhatsApp/push provider exists**, so every send is recorded as
`NOT_CONFIGURED` (or `SKIPPED_NO_CONSENT`), never faked. Reactivation previews who and why, then creates a draft only.
Automation rules only ever create a draft, a task or a staff notification. See
[Campaigns](docs/CAMPAIGNS.md), [Marketing automation](docs/MARKETING_AUTOMATION.md), [Retention](docs/RETENTION.md).

**Referrals and consent.** Self-/duplicate-referral prevention, rewards only after a qualifying purchase and only
through the loyalty ledger; marketing consent is separate from transactional messages and every change is audited.
See [Referrals](docs/REFERRALS.md).

**Safety and AI.** Large campaign audiences and large loyalty adjustments (thresholds are per-shop settings, off by
default) need a second person's approval via the Phase 13 queue. The assistant gained nine read-only CRM tools and a
`CAMPAIGN_DRAFT` action that can only create a draft. See [CRM AI tools](docs/CRM_AI_TOOLS.md).

**Screens.** A "Growth" section (`/crm`): overview dashboard, campaigns (create draft, launch with confirmation, pause/cancel,
per-customer outcomes), win-back preview and draft, loyalty program, automation rules, approval limits and the referral
program, plus a per-customer growth profile (consent, loyalty adjustment, notes, timeline) linked from the customer page.
English and Hindi. Not built: a screen for customer groups (campaigns can still target a saved group) and for approving a
request (the existing Approvals API is used).

**Not done (on purpose, this phase).** No real messaging provider; no scheduler for automation
rules or campaign start times; referral rewards are not reversed on a sale void; no birthday/anniversary triggers
(no such data) and no abandoned-cart (no online store).

## What Phase 15 added

**Finance on top of what exists, with no second accounting system.** An expense workflow (draft, submit, approve, post, void) with
configurable categories and a per-shop approval limit; a financial ledger that is one traceable view over sales, purchases, returns,
khata payments and the few events finance owns (insert-only, corrected by reversal or explicit adjustment); daily cash with physical
counts (opening cash is never guessed); payables and receivables (khata stays the source of truth) with labelled ageing; profit and
loss that says **Profit Not Available** rather than inventing a cost; cash flow; a configurable tax *reporting foundation*; financial
periods (open, locked, closed) enforced on finance and on sale/purchase/return posting and voiding; reconciliation review (no bank
integration exists and none is faked); a finance dashboard, neutral alerts, ten read-only AI tools, exports and an
English/Hindi "Finance" section. See [Finance](docs/FINANCE.md).

**Not done (on purpose).** No bank, payment or tax-filing integration; no online orders exist to include; ageing is by transaction date
because no due dates or payment terms are stored; split payments across methods are not recorded per method.

## What Phase 16 added

**Advanced reporting and business intelligence on top of what exists, with no second data source.** Common, server-resolved filters and
periods (presets, comparison, both ends inclusive); a KPI framework of 31 KPIs each with a formula, source and limitations (and honest
*Not Available* / *Insufficient Data* / *Insufficient comparison data*); an executive dashboard; sales, inventory, customer, supplier and
finance analytics; cohort retention with written definitions; factual cross-module observations (never causes); drill-down from a total to
the record; a custom report builder over an allowlist (never SQL; saved, editable, archived, never deleted); scheduled advanced reports
(idempotent, "Delivery Channel Not Configured" when no provider exists); CSV, XLSX and PDF exports with title, shop, period and generated
time; ten read-only AI tools; six analytics permissions; an English/Hindi **Analytics** section. See [Analytics](docs/ANALYTICS.md).

**Measured, not assumed.** Timings on 25,000 sales are in [docs/ANALYTICS.md](docs/ANALYTICS.md); no index or cache was added because nothing
measured needed one, and the one cost driver found (a daily finance trend over years) is bounded.

**Not done (on purpose).** No online orders exist, so online figures are Not Available; no email or SMS provider, so scheduled reports are kept
in the app; no supplier quality, reliability or delivery scores (no such data is recorded); PDF uses standard Latin-1 fonts.

