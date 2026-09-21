# SaaS administration

The internal operators' side of the platform. It is deliberately separate from the shop owner's experience and it has no
automatic view of any shop's business records.

## Administrators, roles and permissions

`system_admins` holds people who run the platform. They are **not** shop users. There is no admin password and no login page
yet: each administrator has a long random **token** (shown once when created, stored only as a hash) sent as `X-Admin-Token`.
The browser console at `/admin` keeps it in `sessionStorage` (this tab only). Failed attempts are rate limited (per client address) and audited.

Create and manage administrators from the command line, where you already have server access:

```bash
python -m app.admin_cli create --email ops@example.com --name "Ops" --role OPERATIONS_ADMIN
python -m app.admin_cli rotate --email ops@example.com
python -m app.admin_cli deactivate --email ops@example.com
python -m app.admin_cli list
```

| Permission | SUPER_ADMIN | OPERATIONS_ADMIN | SUPPORT_ADMIN |
| --- | :-: | :-: | :-: |
| `shops.view` (account state, plan, counts) | yes | yes | yes |
| `shops.lifecycle` (suspend / deactivate / reactivate) | yes | yes | |
| `subscriptions.manage` (put a shop on a plan) | yes | | |
| `system.health`, `integrity.run` | yes | yes | |
| `events.view` | yes | yes | yes |
| `backups.view`, `backups.manage` | yes | yes | |
| `backups.restore` (API restore, off by default) | yes | | |
| `audit.view` | yes | | |
| `support.grant`, `support.view` | grant: yes | | view: yes |

The backend checks the permission on every request. Hiding a button in the console is only a convenience.

## What an administrator can and cannot see

They see accounts, plans, counts, platform events and health. They **never** get a shop's sales, customers, ledgers,
prices, costs or documents from any admin endpoint.

**Support view** (`GET /api/v1/admin/shops/{id}/support-view`) needs an active **grant**: a SUPER_ADMIN records a reason and a
time limit (1–72 hours) for one named administrator and one shop. Even with a grant the view returns counts, statuses, recent
platform events and integrity findings, not business records. A denied attempt is audited too.

## Audit

`admin_audit_logs` is insert-only (database triggers refuse UPDATE and DELETE). Every admin request that reaches a permission
check writes a row: who, what (`METHOD /path` with ids masked), the permission, `OK` or `DENIED`, the target shop, safe
details (never a token) and the request id. `GET /api/v1/admin/audit` (SUPER_ADMIN) reads it. A state change is also written
to the shop's own audit log with the administrator's label, so the owner's history shows it.

## Shop account lifecycle

`ACTIVE`, `TRIAL`, `SUSPENDED`, `DEACTIVATED` (column `shops.account_status`, with `status_reason` and `status_changed_at`).
Every existing shop becomes `ACTIVE` when the migration runs. Changing the state needs a reason.

What each restricted state allows is configuration, not code:

* `KIRANA_SUSPENDED_POLICY` (default `read_only`): the owner can still read records (GET requests, including exports), but every
  change is refused.
* `KIRANA_DEACTIVATED_POLICY` (default `blocked`): only the account page works.

`/api/v1/account*` always works, so the owner can read why. The refusal is HTTP 403, category `account_restricted`, with a
sentence written for the owner; the frontend shows a banner. **No data is ever deleted or changed** by a state change.
The application does not decide *when* to suspend: there is no billing, no payment and no automatic suspension. Those are commercial
decisions this repository does not invent.

## Plans and entitlements

`entitlement_service` is the single place that answers "may this shop use X?" and "is it within its limit?": AI features, the
online store flag, price intelligence, exports, advanced reports, and limits for products, users, monthly invoices, price
lookups, AI requests, exports and photo analyses. Features and limits are **data** in `plan_features`; a plan with no limit for a key
is unlimited. Refusals are HTTP 403 `plan_limit` with a clear sentence; nothing is silently deleted. Usage is counted in
`subscription_usage` per shop, month and metric, inside the transaction of the action it counts, and idempotent replays do not
count twice. Owners see it at `/api/v1/account/usage` and on the dashboard. Assigning a plan is an admin action with no payment.
The Phase 11 migration adds an `exports` feature (on) and two limits that are unlimited on every plan: no commercial policy is invented.
