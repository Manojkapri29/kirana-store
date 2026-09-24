# Financial period controls

Periods (`/finance/periods`) are opt-in date ranges: **OPEN / LOCKED / CLOSED**. A date no period covers is open.

| Status | Ordinary posting/voiding (finance entries, expenses, sales, purchases, returns, cash counts) | Authorised adjustment |
|---|---|---|
| OPEN | allowed | allowed |
| LOCKED | refused | allowed (controlled correction) |
| CLOSED | refused | refused: date it in an open period, or reopen the period |

The guard (`finance_period_service.assert_open`) is called by the finance services **and** by the post/void paths of sales, quick
sales, purchases, sales returns and purchase returns. Refusals are `409` (`period_locked` / `period_closed`).

* Lock/close need `FINANCE_PERIOD_LOCK`; unlock and reopen need `FINANCE_PERIOD_UNLOCK`. Reopening needs a reason and, when the shop sets
  `period_reopen_requires_approval`, a second person's approval (an approval counts once, for the current closing only).
* Every create/lock/close/unlock/reopen is audited (who, when, before/after).
* A refused change to a locked/closed period is recorded as a system event that **survives the rollback** (`note_after_rollback`), and
  raises the "Attempted change to a locked or closed period" alert.
