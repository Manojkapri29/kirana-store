# Workflow automation: business tasks and approvals

## Business tasks

`task_service.py` / `business_tasks` (+ `task_comments`, insert-only like the audit log) is a generic operational
to-do — not a second notification system. A notification is one-way ("this happened"); a task is tracked until
someone closes it, carries comments, and has an audit trail.

Statuses: `OPEN` → `IN_PROGRESS` / `WAITING` → `COMPLETED` (or `CANCELLED` from any open state). A single nullable
`assigned_to` column is used rather than a separate many-to-many assignment table — multiple simultaneous
assignees were not required by the brief, and a simpler schema was chosen deliberately over speculative
generality.

`kind` is a short free-text code (matching the pattern already used for notification `event_type`), not a fixed
enum — so a task can be raised for any purpose (`LOW_STOCK_REVIEW`, `SUPPLIER_FOLLOW_UP`, a plain ad-hoc job)
without a schema migration every time a new kind of task is needed. `entity_type`/`entity_id` optionally link a
task back to whatever it concerns (a product, a stock count, a supplier).

Permissions: `TASK_VIEW`, `TASK_CREATE`, `TASK_ASSIGN`, `TASK_COMPLETE`, `TASK_CANCEL`.

### Who can create a task

Any authenticated shop member (through the AI assistant's `TASK_DRAFT` action, or directly), a stock count on
variance, or any other part of the system — `task_service.create()` has no special-casing per caller.

## Approvals

`approval_service.py` / `approval_requests` is a **generic** queue, written so a new kind of gated action can reuse
it later by giving it a `kind` and calling `create()`/`decide()` — nothing in the service is specific to stock
counts. Today exactly one thing is wired to it: a stock count whose variance reaches the shop's configured
threshold (see `STOCK_COUNTING.md`). This was a deliberate scoping decision — building unused generic
infrastructure for hypothetical future gates was avoided per the "don't duplicate, don't over-build" instruction.

Rules, enforced in the service (not just the UI):

- **No self-approval**: `decide()` refuses if the deciding user is the same as `requested_by`.
- **Rejecting requires a note** — a blank rejection is refused.
- **A decided request cannot be decided again** — `decide()` refuses on a non-`PENDING` request.
- Every decision is written to the shop's audit log, so the queue table itself can be trimmed or rebuilt without
  losing the history of who decided what.

## Notifications, not duplicated

New event types (`STOCK_COUNT_VARIANCE`, `REORDER_RECOMMENDATION`, `PURCHASE_DRAFT_READY`, `APPROVAL_REQUESTED` →
category `OPERATIONS`; `TASK_ASSIGNED`, `TASK_DUE` → category `TASKS`; `SCHEDULED_REPORT_READY` → category
`REPORTS`) extend the existing `notification_service.EVENT_CATEGORY`/`CATEGORIES` — there is no second
notification pipeline, and `emit_safely()`'s dedupe-key behaviour applies to these events exactly as it does to
every other.

## Known limitations

- No multi-assignee tasks (see above).
- The approval queue currently has one real trigger (stock-count large variance); a second use (e.g. a large
  inventory adjustment or an unusual discount) would need its own `kind` and its own caller, following the same
  pattern.
