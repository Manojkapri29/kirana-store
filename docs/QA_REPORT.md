# Phase 20: full-system QA and production-readiness audit

Date: 2026-09-24. Environment: macOS, Python 3.13, Node 22, SQLite. **No Docker, no PostgreSQL, no external providers, no TLS, no physical phone.**
This report states only what was executed. Anything else is listed under "Not verified".

## Results

| Check | Result |
|---|---|
| Backend regression, all phases | **3840 passed** (0 failed) in 9m15s |
| Backend lint (`ruff check app tests migrations`) | clean |
| Frontend `tsc -b`, `oxlint`, `vitest` | clean; 178 tests passed |
| Frontend production build | succeeds; main chunk 433 kB (135 kB gzip); no secret value in the bundle |
| Migrations | 1 head; stepwise up 0001→0021 and down to base; up again; `alembic check` clean |
| Dependency audit | `pip-audit -r requirements.txt`: no known vulnerabilities; `npm audit --omit=dev`: 0 |
| Production start drill | unsafe config refused with a message naming the setting; safe config starts; `/health/live`, `/health/ready` OK; `/docs` and `/metrics` 404; no `Server` header; unauthenticated API 401; cookie `HttpOnly`(session), `Secure`, `SameSite=lax`; CSRF enforced |
| Restore drill (real CLIs) | see below |

## Business journeys (`tests/test_phase20_journeys.py`, over HTTP)

1. Shop → purchase → sale → khata → payment → reports: stock, khata (`SUM` of ledger) and P&L agree; integrity clean.
2. Quick sale: posts; carries no cost, so profit is not invented.
3. Online order: **no such module exists** (asserted: no orders/storefront routes; P&L `online_sales` is 0). Not a defect; a scope fact.
4. Customer / loyalty / CRM: points earned exactly (₹100 × 0.1 = 10), CRM profile loads.
5. Weighted-average cost: 100 kg @20 + 50 kg @30 → 23.33; 30 kg sold → COGS 699.90; a later purchase does not change the posted sale's cost.
6. Expense approval → P&L (operating expenses, net = gross − expenses) → cash summary (movement falls by the expense).
7. Offline sale sync: replaying the same operation applies once.
8. External payment webhook: signed capture applied once; redelivery ignored; khata reduced exactly once.

## Data-integrity identities (independent recomputation from raw ledger rows)

* Inventory = opening + purchases − sales + sales returns − purchase returns ± adjustments/reversals (rice: 10 + 20 − 5 + 1 − 3 = 23), equal to the API and to `SUM(qty_delta)` for every product.
* Khata balance = `SUM(amount_delta)` of the customer ledger = the expected figure computed by hand (CASH refunds do not touch khata).
* P&L: revenue = detailed + quick + online − returns; gross = revenue − COGS; net = gross − expenses; unknown cost gives `null`, never zero.
* `integrity.run()` (sale/purchase totals, offers, returns, duplicate numbers/SKUs, negative stock, orphan ledger rows, reversals, khata vs bills, foreign keys) returns no findings after each journey and after the restore.

## Restore drill (production environment, real processes)

Fresh database → `alembic upgrade head` → `account_cli create-shop` → uvicorn → over HTTP: category, supplier, product, purchase (10 @ 20), customer, sale of 3 on part credit,
photo kept → `backup_cli create` → another sale (stock 5) → app stopped → photo folder deleted → `verify`, `rehearse`, `restore` → `integrity_cli` clean,
`alembic current` 0021 → app restarted → sign-in OK, stock 7 and balance ₹100 exactly as at the backup, new sale posts (stock 6). The photo returned 404 until the saved
photo folder was copied back, after which its SHA-256 matched. See findings M1 and L1.

## Audits

* **Security:** authentication/session/CSRF/cookies; RBAC (each route exactly one rule, deny by default, no HTTP DELETE); tenant isolation (SQL of every GET route names `shop_id`; id sweeps 404);
  >4,000-request injection sweep; upload limits and type checks; rate limits; SSRF guard on outbound integration calls; webhooks HMAC-signed with replay window; no secrets in responses, logs or the
  frontend bundle (`.env.example` guarded by a test). Only 3 places use `float` (none for money); f-string SQL is limited to fixed PRAGMA/table names.
* **Financial:** integer paise, posted history never deleted (only two `DELETE` statements exist: CRM group membership recompute and role-permission replacement, both configuration).
* **Inventory / AI safety / integration:** covered by the existing phase suites (concurrency, idempotency, read-only typed AI tools with three person-confirmed draft actions, unconfigured providers report NOT_CONFIGURED).
* **Mobile / PWA:** installable manifest, hand-written service worker (shell only, never `/api`), offline shell verified in a real browser in Phase 18. **Not tested on a physical device.**
* **Performance:** see [PERFORMANCE.md](PERFORMANCE.md) (SQLite, synthetic 25k sales): list pages 3–16 ms, dashboards ~0.2–1 s, largest export 0.4 s.

## Findings

**CRITICAL: none found.** **HIGH**

* H1. **PostgreSQL has never been executed.** Only DDL rendering and pool wiring are tested. Do not deploy on PostgreSQL before running the migrations and the full suite against a real server. SQLite single-machine deployment is the supported, tested target.
* H2. **No external integration has been exercised against a real provider** (SMTP, SMS/WhatsApp, payments, S3, accounting). All are tested with fakes and report NOT_CONFIGURED until configured and tested by the shop. Nothing is claimed working.

**MEDIUM**

* M1. Database backups **exclude uploaded photos**. Back up the image folder / bucket separately (documented in BACKUP_AND_RESTORE.md, PRODUCTION_RUNBOOK.md).
* M2. Docker images and compose file are **unverified** (no Docker available). Backend and frontend were built and run directly.
* M3. Rate limiting and metrics are **per process**; several instances need a shared store. Run periodic jobs in exactly one worker.
* M4. SQLite allows one writer at a time: fine for a shop, a ceiling for a large multi-shop SaaS (see PERFORMANCE.md).
* M5. No load test on production-like hardware/database; no monitoring or alerting is wired to any external system.

**LOW**

* L1. The backup registry is stored in the database: restoring an older backup hides newer backups from `backup_cli list` (files remain; a pre-restore backup is always written). Workaround in the runbook.
* L2. `nginx.conf` sets no HSTS (TLS terminates at the operator's proxy; set it there).
* L3. `subscription_admin` refuses to run under `KIRANA_ENVIRONMENT=production` (a safety choice); operators must override the variable for that one command.
* L4. Finance dashboard needs ~372 queries (~0.9 s on SQLite at 25k sales). Acceptable; cache only if it becomes a problem.
* L5. A new shop has no categories or plan; the owner must create a category before adding products.

**INFO**

* I1. No online store / order module (journey 3 in the spec cannot exist); reports say "Not Available".
* I2. Offline conflicts are surfaced for a person to Retry/Discard; nothing is auto-adjusted by design.
* I3. AI has only been tested against a fake provider.

**Fixed during Phase 20:** README status text was stale ("Phase 7 of 16"); `.env.example` lacked the Phase 19 settings (pool, hash concurrency, rate limits, storage credentials reference), and a new test now fails when any setting is undocumented; missing docs (setup, environment variables, migrations, runbook, release checklist, troubleshooting, QA report, domain overviews) were written.

## Final production-readiness status

**Conditionally ready for a single-machine SQLite deployment behind an operator-provided HTTPS proxy, for a shop willing to complete the operator items in
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) (items 19–22) and to configure and test each integration it wants.** **Not** verified for PostgreSQL, container deployment, high load,
or any real external provider. No critical blocker was found; the HIGH items are unverified targets, not known defects.
