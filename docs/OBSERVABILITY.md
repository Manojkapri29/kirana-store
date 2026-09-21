# Observability

## Request IDs

Every request gets an id like `req_9f3a1c2b4d5e6f70`. A client may send `X-Request-ID`; it is reused only if it is well formed
(8 to 64 letters, digits, `.`, `_` or `-`), otherwise replaced. The id is returned in the `X-Request-ID` response
header, written on every log line for that request, stored on audit rows (`audit_log.request_id`) and on platform events.
Unexpected failures also get an `ERR-YYYYMMDD-XXXXX` reference in the response body; the log line for that failure carries both.

## Logs

`KIRANA_LOG_FORMAT=json` writes one JSON object per line; `text` is for a terminal. `KIRANA_LOG_LEVEL` sets the level.

Fields: `timestamp`, `level`, `request_id`, `endpoint` (record numbers masked: `/api/v1/sales/{id}`), `method`, `status`,
`duration_ms`, `shop_id`, `user_id`, `category`, `message`.

Categories (one logger each): `application`, `security`, `audit`, `integration`, `ai`, `backup`, `notification`, and `access`
for the request line. Health checks are logged at DEBUG.

Never logged: passwords, tokens, API keys, authorization headers, payment secrets, raw documents or images, query strings,
request bodies. Fields with those names are dropped from structured events.

## Platform events

`system_events` keeps operational facts (provider failures, backup failures, notification delivery failures, rate limiting)
with a category, severity, a stable code and a safe message. Administrators read them at `GET /api/v1/admin/events`.

## Metrics

There is **no metrics endpoint** (Prometheus or similar) and no external monitoring integration. The administrator overview
(`/api/v1/admin/system/overview`) reports shops by state and event counts for the last 24 hours, calculated on request.
Collect logs with your platform's log shipper and alert on `ERROR` lines and on `/health/ready` returning 503.
