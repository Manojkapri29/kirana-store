# Marketing automation (Phase 14)

A rule = **trigger + conditions + action + cooldown**, with an active flag and a run history (`automation_rules`,
`automation_runs`; runs are insert-only). Rules run **on demand** (`POST /automation-rules/{id}/run`) — there is intentionally
no scheduler that acts as a system user (see limitations).

## Triggers

| Trigger | Conditions |
|---|---|
| `NEW_CUSTOMER` | `within_days` (default 1) |
| `INACTIVITY` | `inactive_days` (required), `requires_marketing_opt_in` (default **true**) |
| `LOYALTY_MILESTONE` | `points_threshold` (required) |
| `PURCHASE_MILESTONE` | `purchase_count_threshold` (required) |

## Actions — deliberately the safe ones only

`CREATE_CAMPAIGN_DRAFT` (a DRAFT, never launched), `CREATE_TASK` (a Phase 13 business task per matched customer), `NOTIFY`
(a staff notification). There is **no** action that sends a message, changes points, changes a balance, sets a price or applies a
discount, so an automation can never do any of those.

## Safety

* **Cooldown** per rule and customer: within `cooldown_days` of the last action the customer is skipped.
* **Idempotency**: running twice the same day actions nobody twice; a run with no one to action is `SKIPPED_CONDITION`, not an error.
* **Failures** are recorded on the run (`FAILED` with the message), not swallowed.
* Inactive rules never run. Every create/activate/deactivate/run is audited.

## Known limitations

* No background schedule: the periodic worker has no system actor to attribute audit rows and tasks to, and inventing one
  would be an autonomous actor. Trigger runs from the API (or a cron that calls it as a real user).
* No birthday/anniversary trigger (no such data), no abandoned-cart (no online store).
