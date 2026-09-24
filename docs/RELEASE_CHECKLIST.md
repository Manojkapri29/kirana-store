# Release checklist

Status is as of the Phase 20 audit. "Done" means it was executed in this repository's environment; see QA_REPORT.md for evidence.

| # | Item | Status |
|---|---|---|
| 1 | Backend tests pass (`pytest`) | Done |
| 2 | Frontend type-check, lint and tests pass | Done |
| 3 | Frontend production build succeeds; no secret in the bundle | Done |
| 4 | Migrations: one head, stepwise up and down, `alembic check` clean | Done (SQLite only) |
| 5 | Integrity check (`integrity_cli`) clean on a drilled database | Done |
| 6 | Backup created, verified, rehearsed and actually restored | Done (database and photos; SQLite and PostgreSQL) |
| 7 | Production start refuses unsafe configuration; starts with a safe one | Done |
| 8 | `/health/live` and `/health/ready` OK; `/docs`, `/metrics` closed | Done |
| 9 | Security headers, CSRF, HttpOnly/Secure/SameSite cookie, rate limits | Done (backend); HSTS is the proxy's job |
| 10 | RBAC: every route has exactly one rule, deny by default, no HTTP DELETE | Done (tests) |
| 11 | Tenant isolation (SQL scan of every GET route; id sweeps) | Done |
| 12 | Dependency audit (`pip-audit`, `npm audit --omit=dev`) | Done: no known vulnerabilities at the time |
| 13 | `.env.example` complete and secret-free (test-enforced) | Done |
| 14 | Documentation complete and consistent | Done |
| 15 | Performance baseline recorded (SQLite) | Done |
| 16 | Load test on production-like hardware and database | **Not done** |
| 17 | Docker images built and run | Done locally (compose stack, end to end); TLS/proxy and orchestrators untested |
| 18 | PostgreSQL migrations and test suite run on a real server | Done on a local PostgreSQL 16; **not** on a remote/managed service |
| 19 | TLS, HSTS and proxy headers configured at the edge | **Operator** |
| 20 | Each external integration configured and tested by the shop (SMTP, SMS/WhatsApp, payments, S3) | **Operator**; none is claimed working |
| 21 | Off-machine backup copy of database and photos, restore practised | **Operator** |
| 22 | Exactly one worker process supervised; monitoring/alerting wired | **Operator** |
