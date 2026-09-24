# Scheduled reports

`scheduled_report_service.py` / `scheduled_reports` lets a shop ask for one of the existing summaries to be worked
out again on a schedule, from the same reporting services the screens already use. Nothing is calculated specially
for a schedule — a scheduled report is the same function call a person triggers on-demand, just run automatically.

## What "runs" actually means

**No real email or SMS is sent — no delivery provider is bundled.** Each run:

1. Calls the matching existing service (see table below) and stores the small structured result (figures and
   counts only — the same shapes the API already returns; never a full document, never raw customer or sales
   rows) in `scheduled_reports.last_result` (JSON).
2. Raises an in-app `SCHEDULED_REPORT_READY` notification pointing at it.
3. The full underlying data stays one click away through the existing, already-permissioned export endpoints —
   the schedule is a pointer to "go look," not a data dump of its own.

| `report_type` | Delegates to |
| --- | --- |
| `sales_summary` | `sales_report_service.sales_summary` |
| `inventory_summary` | `inventory_intelligence_service.inventory_health` |
| `purchase_summary` | `analytics_service.purchase_totals` |
| `khata_summary` | `khata_service.list_accounts` (outstanding totals) |
| `business_health` | `business_health_service.health_report` |
| `reorder` | `ai_insights_service.reorder_recommendations` |

An unknown `report_type` is refused at creation time (`InvalidInputError`), not silently accepted.

## Schedules

`DAILY` / `WEEKLY` / `MONTHLY` map to 1/7/30-day intervals (`INTERVAL_DAYS`). Creating or updating a schedule sets
`next_run_at = now + interval`.

## Idempotency

`run_due()` (called by the background job `reports.run_scheduled`, itself registered in
`background_job_service.schedule_periodic()`) selects every **active** schedule whose `next_run_at` has passed,
across every shop, and runs each exactly once. `next_run_at` is always moved forward by the same run that used it,
in the same transaction — calling `run_due()` twice close together (e.g. a retried job, an overlapping timer) does
nothing extra the second time, because the schedule is no longer due. This is proven directly in
`tests/test_phase13_scheduled_reports.py::test_running_due_reports_twice_close_together_does_nothing_extra`.

## Shop and permission scoping

Every schedule row is `shop_id`-scoped like everything else in the schema; `run_due()` iterates schedules across
shops but each run only ever touches its own shop's data (the underlying service calls are shop-scoped, same as
any other call). Managing schedules requires `SCHEDULED_REPORT_MANAGE`.

## A run that fails

If the underlying service call raises, `_run_one()` catches it, stores `last_status="FAILED"` and
`last_error=str(exc)[:300]`, and still moves `next_run_at` forward (a permanently-broken report type does not spin
the worker in a tight retry loop). No notification is raised for a failed run.

## Known limitations

- No real delivery channel (email/SMS/WhatsApp) is wired up; "scheduled" means "computed and stored," not "sent."
  Wiring a provider is a configuration step, not a code change (see the final Phase 13 report's "Configuration
  Required" section).
- A report's `last_result` is overwritten by each run; only the most recent run's result is kept.
