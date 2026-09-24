# Roles and permissions (RBAC)

The backend is authoritative. The frontend hides buttons for a nicer screen; **a call without the permission is refused by the
server** whatever the screen shows, and tests prove it.

## How a request is decided

1. The session names a **membership** (`users` row); the membership names the **shop** and the **role**; the role names the
   **permissions**. Nothing the client sends (a `shop_id` in a body, a query string or a header) takes part.
2. `api/deps.py` finds the permission(s) the route needs in **one table**, `ROUTE_RULES` in `app/core/permissions.py`, and checks them
   through `authorization_service.require`. Routes carry no scattered checks.
3. A route with **no rule is refused** (fail closed). `tests/test_rbac.py` lists every route of the running application and fails if
   a route has no rule or a rule has no route, and it calls **every route as every system role** and checks the outcome matches the
   role's permissions.
4. Services check a permission again only where an action depends on data the route cannot know (the AI actions, below).

## Permissions

Codes and meanings are in `PERMISSIONS` (`GET /api/v1/roles/permissions`). Groups: Products, Inventory, Suppliers, Purchases, Sales,
Returns, Customers, Khata, Offers, Online orders, Reports, AI, Staff, Account, Settings. Beyond the list in the brief, `ROLE_MANAGE`
(custom roles) was added. `ONLINE_ORDER_*` and `STORE_SETTINGS_MANAGE` exist as codes but **no route uses them yet**: there is no
online store.

**Owner-only** (`OWNER_ONLY_PERMISSIONS`): `ROLE_MANAGE`, `BACKUP_CREATE`. Only the OWNER role holds them; a custom role can never be
given one.

## Default roles

Stored in `roles` / `role_permissions` (seeded by migration `0015`; system roles are shared rows with no shop). They are defaults,
readable and changeable as data. **OWNER** always holds every permission in the catalogue, including any added later.

| Role | Holds |
| --- | --- |
| **OWNER** | everything |
| **MANAGER** | everything except the owner-only permissions (including reports, exports, staff invite/edit/suspend, AI and AI confirmation) |
| **CASHIER** | products, inventory (view); sales view/create/post; quick sales; customers view/create; khata view and payments; offers view |
| **INVENTORY_STAFF** | products view/create/edit; inventory view/adjust/export; suppliers view. No purchases, sales, customers, reports |
| **SALES_STAFF** | cashier's set plus customer edit and the online-order codes (accept/reject/status) |
| **ACCOUNTANT** | purchases view/create/post; sales, customers and products view; khata view/payments/adjust; suppliers view; reports view and export; audit log. No stock adjustment, no product edits, no selling |

Not in the default sets on purpose: cashiers cannot post purchases, adjust stock, see costs, reports, staff or the plan; inventory staff
cannot see money reports; nobody but OWNER/MANAGER can use the AI by default.

## Rules that protect against escalation

* **You can only give what you hold**: assigning a role, inviting with a role, or making a custom role fails (403) if it contains a
  permission the actor lacks. A manager therefore cannot make an owner.
* **You cannot manage someone who holds more than you**, and **nobody changes their own access** (role, suspension, removal).
* A custom role cannot hold an owner-only permission; you cannot edit the permissions of the role you yourself hold.
* A role cannot be retired while members use it; a system role or another shop's role cannot be edited or even seen.
* A shop always keeps at least one active owner.
* Legacy: a request context built without a membership (only tests and the development shortcut) treats OWNER as everything and STAFF
  as `LEGACY_STAFF_PERMISSIONS`. Real sessions never use this.

## Exports

Each export needs the permission to see that data **and** the permission to download it: inventory files `INVENTORY_VIEW` +
`INVENTORY_EXPORT`; every other file its `*_VIEW` + `REPORT_EXPORT`. The shop is always the caller's; a `shop_id` in the query is
ignored. Every export is written to the audit log (who, which file) and counts toward the plan's export limit.

## AI

`AI_USE` is needed for every AI route; **confirming** a proposed action needs `AI_ACTION_CONFIRM` *and* the permission of what the
action does (`PURCHASE_CREATE`, `INVENTORY_ADJUST`, `PROMOTION_CREATE`), which is also required to *propose* it. Every assistant
tool has a permission (`TOOL_PERMISSION`, `REPORT_VIEW`, `INVENTORY_VIEW`, `KHATA_VIEW`, ...); a question that needs a tool the person
cannot use is declined in words ("You don't have permission to see that information.") without running it, and a direct tool call is
403. Reading a document needs what it leads to (invoice: `PURCHASE_CREATE`; stock list: `INVENTORY_ADJUST`). The assistant cannot see
beyond its user, cannot change a role, and still only proposes: a person confirms.

## CRM permissions (Phase 14)

`CRM_VIEW`, `CRM_MANAGE`, `CRM_SEGMENT_MANAGE`, `CRM_ANALYTICS_VIEW`, `LOYALTY_VIEW`, `LOYALTY_MANAGE`, `CAMPAIGN_VIEW`,
`CAMPAIGN_MANAGE`, `CAMPAIGN_LAUNCH`, `AUTOMATION_MANAGE`, `REFERRAL_VIEW`, `REFERRAL_MANAGE` (details in [CRM.md](CRM.md)).
Launching a campaign is a separate permission from managing one. The CRM AI tools are each gated by the matching `*_VIEW` code and
`CAMPAIGN_DRAFT` needs `CAMPAIGN_MANAGE`.

## Finance permissions (Phase 15)

`FINANCE_VIEW`, `FINANCE_MANAGE`, `FINANCE_EXPENSE_VIEW`, `FINANCE_EXPENSE_MANAGE`, `FINANCE_EXPENSE_APPROVE`, `FINANCE_CASH_VIEW`,
`FINANCE_CASH_MANAGE`, `FINANCE_RECONCILIATION_MANAGE`, `FINANCE_PERIOD_LOCK`, `FINANCE_PERIOD_UNLOCK`, `FINANCE_ADJUSTMENT_MANAGE`,
`FINANCE_EXPORT`. Owner and manager hold all; the accountant has view, expense manage, cash, reconciliation, period lock and export
(not approve, unlock or adjustments); the cashier only cash view/manage. Finance exports need the view permission **and**
`FINANCE_EXPORT`. The shared `/approvals` endpoints authorise **by request kind**: stock count -> `STOCK_COUNT_APPROVE`, campaign ->
`CAMPAIGN_LAUNCH`, loyalty adjustment -> `LOYALTY_MANAGE`, expense -> `FINANCE_EXPENSE_APPROVE`, finance adjustment ->
`FINANCE_ADJUSTMENT_MANAGE`, period reopen -> `FINANCE_PERIOD_UNLOCK`.

## Phase 16: analytics permissions

`ANALYTICS_VIEW`, `ANALYTICS_ADVANCED`, `ANALYTICS_EXECUTIVE`, `ANALYTICS_CUSTOM_REPORT`, `ANALYTICS_EXPORT`, `ANALYTICS_SCHEDULE`. Owner and manager hold all
six; the accountant has view, advanced and export. Every analytics route needs an analytics permission **and** the permission of the data
it shows (finance analytics need `FINANCE_VIEW`; customer analytics `CRM_ANALYTICS_VIEW`; and so on: see [ANALYTICS.md](ANALYTICS.md)). A
custom report is checked against its dataset's permissions each time it is saved or run; a KPI the role may not see is hidden, not
zeroed. The AI tools declare a primary permission plus extras (`TOOL_EXTRA_PERMISSIONS`) mirroring the route rules.

## Adding a route or a permission

1. Add the route. 2. Add its `_r(...)` line to `ROUTE_RULES`. 3. Run `pytest tests/test_rbac.py`: it names any route without a rule.
For a new permission add it to `PERMISSIONS`, decide which default roles hold it, and add a migration that inserts it into
`role_permissions` for those system roles (OWNER needs nothing).

## What this does not do

No per-record permissions (a cashier who can view sales sees all the shop's sales), no time-limited grants, no approval workflows, and
no separate "ownership transfer" (deferred). Frontend hiding covers the menu, the main create/post/void buttons, exports and the
dashboard; other screens rely on the server's clear refusal ("You don't have permission to perform this action.").
