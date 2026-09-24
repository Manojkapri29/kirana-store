# CRM (Phase 14)

Customer relationship management is built **on the existing `customers` table** — there is no second customer
table, no second sales system and no second ledger. Everything below is backend only (no frontend screens were
built this phase).

## Foundation

`customers` gained: `customer_type` (RETAIL/WHOLESALE/OTHER), `source` (WALK_IN/REFERRAL/ONLINE/CAMPAIGN/OTHER),
`tags` (list of strings), `preferred_contact_channel`, four per-channel marketing consent flags
(`marketing_opt_in_email|sms|whatsapp|push`, all default **off**) and `referred_by_customer_id`. They are changed
with `PATCH /api/v1/crm/customers/{id}/classification`, a thin wrapper over `customer_service.update_customer`, so every
change is audited with before/after exactly like any other customer edit.

## Profile and timeline

* `GET /crm/customers/{id}/profile` — one call for the profile: classification, consent flags, analytics (from
  `customer_intelligence_service`), outstanding balance (from `khata_service`), loyalty balance (from
  `loyalty_service`), referral count and code.
* `GET /crm/customers/{id}/timeline` — sales, quick sales, khata entries, loyalty entries and notes merged newest
  first. **Read-only aggregation**: nothing is copied into a timeline table.
* `GET|POST /crm/customers/{id}/notes` — notes are insert-only (a DB trigger forbids UPDATE/DELETE).

## Segments and groups

Factual, explainable segments (never scores): NEW, ACTIVE, INACTIVE, RECENTLY_INACTIVE, LONG_INACTIVE, CREDIT,
HIGH_FREQUENCY, LOW_FREQUENCY, HIGH_VALUE, ONE_TIME_BUYER, FREQUENT_BUYER, COUPON_USER, NON_COUPON_USER. Every
threshold is a parameter of the rule, never a hidden constant. `GET /crm/segments` lists customers per segment.

A **group** is either MANUAL (a hand-picked list, replaced with `PUT /crm/groups/{id}/members`) or RULE_BASED (a saved
rule such as `{"min_total_spend": "5000", "segment": "ACTIVE"}`, recomputed by `POST /crm/groups/{id}/recalculate`).
Unknown rule keys are refused at save time. Members must belong to the same shop (a customer id from another shop is
"not found"). Groups are never deleted — there is no DELETE route in the whole API.

## Dashboard

`GET /crm/dashboard?period_days=N` (plus `/retention`, `/reactivation-candidates`, `/purchase-patterns`) — every figure
comes from a backend service. Where something cannot be known the response says so ("NOT_ENOUGH_DATA", `null`) instead
of inventing a number.

## Approval and safety controls

Two **configurable** thresholds live on the shop (`GET|PUT /crm/approval-settings`; both `null` = off; there is no built-in
number):

| Setting | Effect when exceeded |
|---|---|
| `campaign_audience_threshold` | Launching a campaign whose audience is **larger** than N is held; nothing is snapshotted or sent, the campaign shows `requires_approval: true`, and an approval request is opened. |
| `loyalty_adjustment_threshold` | A manual loyalty adjustment of more than N points is **not recorded**; `POST /loyalty/customers/{id}/adjust` answers `202` with `approval_request_id`. |

Both reuse the Phase 13 approval queue (`/approvals`): the decider can never be the requester (`cannot_self_approve`), a
rejection needs a reason, and every request/decision is audited (requester, decider, threshold, observed value, note).
Approving a campaign does **not** launch it — someone with `CAMPAIGN_LAUNCH` still launches. The requester redeems an
approved loyalty adjustment by re-sending it with `approval_request_id`; an approval is single-use, tied to that customer and
that exact size.

## Permissions

`CRM_VIEW`, `CRM_MANAGE`, `CRM_SEGMENT_MANAGE`, `CRM_ANALYTICS_VIEW`, `LOYALTY_VIEW`, `LOYALTY_MANAGE`, `CAMPAIGN_VIEW`,
`CAMPAIGN_MANAGE`, `CAMPAIGN_LAUNCH`, `AUTOMATION_MANAGE`, `REFERRAL_VIEW`, `REFERRAL_MANAGE`. OWNER and MANAGER hold all;
CASHIER: CRM_VIEW, LOYALTY_VIEW; SALES_STAFF: CRM_VIEW, CRM_MANAGE, LOYALTY_VIEW; ACCOUNTANT: CRM_VIEW, LOYALTY_VIEW,
CRM_ANALYTICS_VIEW. Exports (customer group, loyalty ledger, campaign) need the `*_VIEW` permission **and** `REPORT_EXPORT`.
Changing the approval settings needs both `CAMPAIGN_MANAGE` and `LOYALTY_MANAGE`.

## Configuration

No new environment variables. Everything is per shop through the API (`/crm/approval-settings`, `/loyalty/program`,
`/referrals/program`). Migration `0017_crm_loyalty_campaigns` adds the columns and 13 tables; run `alembic upgrade head`.

## Known limitations

* No frontend screens. Dashboard filters: `period_days` only (no per-segment or per-channel filter yet).
* No group deletion (deactivate-only philosophy; no DELETE routes exist).
* Birthday/anniversary triggers are not possible: the customer record stores neither date. Abandoned-cart is n/a: there
  is no online store.
* The consolidated AI toolset does not include separate `get_customer_summary`, `get_customer_purchase_frequency` or
  `get_customer_lifetime_value` tools; that data is inside `get_customer_profile`. See [CRM_AI_TOOLS.md](CRM_AI_TOOLS.md).
