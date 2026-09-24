# Referrals and communication preferences (Phase 14)

## Referrals

`PUT /referrals/program` (one per shop): `referrer_reward_points`, `referred_reward_points`, `min_purchase_amount`,
`max_referrals_per_customer`, `expiry_days`, `is_active`. Each customer has a stable 6-character code
(`GET /referrals/customers/{id}/code`). `POST /referrals/register` links a new customer to a code.

* **Prevented**: referring yourself; a customer being referred twice (unique per referred customer); exceeding the referrer's
  maximum; an unknown code.
* **A reward is not granted at registration.** The referral stays PENDING until the referred customer's first posted sale of at
  least `min_purchase_amount` (hook in the sale/quick-sale post path, best-effort in a savepoint, so it can never block a sale).
* Rewards go **through `loyalty_service.grant_reward`** (reference type `REFERRAL`), so they are ordinary, idempotent ledger
  entries. Without an active loyalty program the referral is tracked but no reward is granted.

Known limitation: a reward is not reversed if the qualifying sale is later voided. Expiry of a pending referral is stored
(`expiry_days`) but not swept automatically.

## Communication preferences and consent

* Four per-channel marketing flags, default **off**; a preferred contact channel.
* **Marketing consent is separate from transactional messages**: `notification_service` (invoices, alerts, etc.) never reads a
  marketing flag (asserted by a test), and campaigns/automation/reactivation check the flag every time.
* Every consent change is audited (who, when, before/after) through the normal customer update audit.
* Consent is exposed on `GET /crm/customers/{id}/profile` and changed with `PATCH /crm/customers/{id}/classification`.
