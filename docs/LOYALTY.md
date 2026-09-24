# Loyalty (Phase 14)

One configurable program per shop (`PUT /loyalty/program`) and an **insert-only ledger** (`loyalty_ledger`, DB triggers
forbid UPDATE/DELETE). A customer's balance is **never stored**: it is the sum of their ledger rows. Only
`loyalty_service` may touch the ledger (enforced by `tests/test_architecture.py`); everything else uses
`get_balance_map`/`list_ledger`, mirroring `khata_service`.

## Rules (all configurable, none assumed)

`points_per_amount` (must be > 0), `min_transaction_amount`, `redemption_value` (money per point), optional
`min_redemption_points`, `max_redeem_points_per_txn`, `points_expiry_days`. A shop without an active program earns nothing;
the AI tool and dashboard then say "NOT_CONFIGURED".

## Entry types

`EARN`, `REDEEM`, `ADJUST`, `EXPIRE`, `REVERSAL`. Each row records customer, signed points, reference (type + id), note,
date and the user.

* **Earn** — inside `sale_service.post_sale` / `quick_sale_service.post_quick_sale`, in a savepoint, and never raises: a loyalty
  problem cannot block a sale. Points = floor(amount × points_per_amount).
* **Reverse** — voiding a sale inserts a REVERSAL for what that sale earned. Never a delete or an edit.
* **Redeem** — deliberate, so its rules are enforced (minimum, maximum per transaction, enough balance).
* **Adjust** — manual, always needs a reason. Above the shop's `loyalty_adjustment_threshold` it needs a second person's
  approval first (see [CRM.md](CRM.md)).
* **Expire** — `POST /loyalty/expire` (a person triggers it; nothing expires silently). Points earned more than
  `points_expiry_days` ago and not since consumed (oldest-first) expire, never more than the balance, one row per customer
  per day. No expiry configured = no-op.

## Idempotency

`uq_loyalty_ledger_no_double_entry` on (shop, customer, entry type, reference type, reference id): posting the same sale
twice, reversing twice, granting the same referral twice or expiring twice on one day simply does nothing the second time.

## Endpoints

`GET|PUT /loyalty/program`, `GET /loyalty/summary`, `GET /loyalty/customers/{id}/ledger`,
`POST /loyalty/customers/{id}/redeem`, `POST /loyalty/customers/{id}/adjust`, `POST /loyalty/expire`,
`GET /exports/loyalty-ledger/{id}`.

## Known limitations

* Referral rewards are **not** reversed if the qualifying sale is later voided (only the sale's own earning is).
* Redemption is a standalone ledger entry; it is not yet applied as a discount inside the sale checkout.
* Expiry runs on demand; there is no scheduled expiry job (see [MARKETING_AUTOMATION.md](MARKETING_AUTOMATION.md)).
