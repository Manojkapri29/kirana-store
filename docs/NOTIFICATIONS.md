# Notifications

Provider-independent: business code emits an **event**; the notification layer decides who is told and how.

## Tables

* `notification_events` — one row per thing that happened (shop, type, category, title, message, safe entity reference,
  a `dedupe_key`, so the same event is never created twice).
* `notification_deliveries` — one row per person and channel: `PENDING`, `SENT`, `FAILED`, `RETRYING`, `CANCELLED`, with
  attempts, next attempt time and a safe error code. `(event, person, channel)` is unique.
* `notification_preferences` — per person and category, one switch per channel.

## Channels

`IN_APP` is real: an inbox with unread count, list, mark read, mark all read, timestamp, type, title, message and (where the app
can open it) a link to the product or customer. `EMAIL`, `SMS`, `WHATSAPP` and `PUSH` are **not configured** and no provider is
bundled: choosing one in preferences creates no delivery, and the preferences screen says "Not set up yet". A provider is a
small class that sends one message and reports success, a retryable failure or a permanent failure; register it in
`notification_service.provider_for` and set `KIRANA_NOTIFICATION_<CHANNEL>_PROVIDER`.

## Event types

`ONLINE_ORDER_PLACED/ACCEPTED/REJECTED/READY/OUT_FOR_DELIVERY/DELIVERED`, `LOW_STOCK`, `PAYMENT_RECEIVED`, `KHATA_REMINDER`,
`BACKUP_FAILED`, `BACKUP_COMPLETED`, `AI_USAGE_LIMIT`, `SUBSCRIPTION_LIMIT`, `BUSINESS_ALERT`.

**Emitted today:** `LOW_STOCK` (after a posted sale), `PAYMENT_RECEIVED` (khata payment; no amount or name in the text),
`AI_USAGE_LIMIT` and `SUBSCRIPTION_LIMIT` (an allowance is used up), `BACKUP_FAILED`, `BUSINESS_ALERT` (daily check).
**Defined but not emitted:** the `ONLINE_ORDER_*` types (no online store exists) and `KHATA_REMINDER` (no reminder job).

## Rules

* A notification problem **never fails a business transaction**: emitting happens in a savepoint and errors are recorded as
  platform events, not raised.
* Retries back off exponentially (`KIRANA_NOTIFICATION_BACKOFF_SECONDS`, doubling; `KIRANA_NOTIFICATION_MAX_ATTEMPTS`) and
  never send twice. `notification_service.process_due` runs due retries; call it from a scheduler.
* Everything is shop-scoped; another shop's or person's notification is a 404.
* `KIRANA_FEATURE_NOTIFICATIONS=false` switches the API off (503) without touching business features.

## Business-health alerts

`POST /api/v1/notifications/refresh-alerts` (and the dashboard) turn the shop's own records into neutral, once-a-day alerts:
low stock, customers with old outstanding, plan limits nearly used, unusual activity compared with the shop's usual pattern, and a problem with an outside service.
The wording says what the numbers show; it does not accuse or diagnose.
