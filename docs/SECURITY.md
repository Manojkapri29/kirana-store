# Security

What is enforced, where, and what is not done yet. "Enforced" means the backend does it and a test proves it.

## Trust boundaries

* The **frontend is not trusted**. Every rule (tenant, role, plan, limits, prices, stock, balances) is enforced by the backend.
* **Shop isolation** is server-side: every shop-owned table has a `shop_id`, every service query filters by the caller's shop, and
  composite foreign keys `(shop_id, id)` make a cross-shop reference impossible even for a buggy query. Another shop's record
  answers **404**, not 403 (existence is not leaked).
* **The AI never writes.** It reads through the same services and proposes structured actions that a person confirms (Phase 10).
* **Administrators** are separate from shop users and do **not** see a shop's business data (see `SAAS_ADMIN.md`).

## Checklist

| Area | Status | Notes |
| --- | --- | --- |
| Tenant isolation / IDOR | Enforced, tested | cross-shop reads, exports, ledgers, notifications, usage |
| Payload manipulation | Enforced, tested | request bodies reject unknown fields (422); price, cost, discount, stock effect, balance and `shop_id` are never taken from a request |
| SQL injection | Enforced, tested | SQLAlchemy parameters only; no string-built SQL |
| Search abuse | Enforced, tested | `%`, `_` and `\` are literal (`autoescape`); queries over 100 characters are refused; pages are bounded (`limit` ≤ 100) |
| CORS | Enforced, tested | only the configured origins; methods GET/POST/PUT/PATCH/OPTIONS; no `DELETE`; explicit headers |
| CSRF | Enforced, tested | per-session CSRF token (hashed) echoed in `X-CSRF-Token` on every change made with the cookie; SameSite=Lax as a second layer; Bearer calls need none |
| XSS | React escapes output; API sends `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a strict `Referrer-Policy`; `Cache-Control: no-store` on `/api/` |
| CSV formula injection | Enforced, tested | cells starting `= + - @ tab CR` are neutralised in CSV and XLSX; real date cells in XLSX; UTF-8 BOM in CSV |
| Uploads | Enforced, tested (Phase 10) | MIME, extension and file signature must agree; size and pixel limits; decoded and re-encoded; generated storage names; no path from user input |
| Path traversal | Enforced, tested | backup storage refuses names with `/`, `..` or a leading dot |
| Rate limiting | Enforced, tested | AI, image analysis, external lookups, coupon/price calculation, exports, admin; login/reset: not applicable yet |
| Secrets | `.env` git-ignored; `SecretStr`; never printed, logged, returned or put in OpenAPI; production refuses to start without them |
| Error leakage | Enforced, tested | responses carry a plain message and an `ERR-…` reference; no stack, SQL, path, key or token |
| Logging | Enforced, tested | fields named password/token/secret/key/authorization are dropped; the access log has no query string or body |
| Financial records | Enforced by DB triggers and services | ledgers are insert-only; posted documents are voided or reversed, never deleted; there is no `DELETE` route |
| Admin access | Enforced, tested | hashed tokens, role permissions, denials audited, support access by time-limited grant |
| Backups | Enforced, tested | checksum, integrity check, refusal to restore an invalid or incompatible file, confirmation phrase, safety copy |
| Public storefront | Not built | there is no public API to leak from; `online_visible` remains a data flag only |
| Authentication | Enforced, tested (Phase 12) | Argon2id, HttpOnly cookie sessions stored as hashes, idle + absolute expiry, throttling, account pause, `AUTHENTICATION.md` |
| Authorization | Enforced, tested (Phase 12) | one permission table for every route, every role tested against every route, no escalation, `RBAC.md` |
| Invitations | Enforced, tested | random, expiring, single-use, hash-stored, fragment link, never logged |
| Dependency scanning | Not done | run `pip-audit` / `npm audit` in CI |

## Reporting a problem

Keep the `ERR-…` reference shown to the user: it finds the exact log line. Do not paste secrets into tickets.
