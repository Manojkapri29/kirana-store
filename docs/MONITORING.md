# Monitoring

Three sources, none of them needing a paid product: **health endpoints**, **structured logs**, and **metrics** in the Prometheus text format.

## Health

`/health` and `/health/live` (process is up), `/health/ready` (database reachable, migrations at the code's head, configuration safe; 503 otherwise;
check names only). Detailed diagnostics for operators: `GET /api/v1/admin/system/health`, and recent background jobs: `GET /api/v1/admin/system/jobs`
(administrator token). See `OBSERVABILITY.md` for request ids, log fields and categories.

## Metrics

Off by default. `KIRANA_METRICS_ENABLED=true` plus `KIRANA_METRICS_TOKEN` (16+ characters; production refuses to start without it) makes
`GET /metrics` answer with `Authorization: Bearer <token>` (constant-time comparison, rate limited). While disabled the route does not exist (404).

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `kirana_http_requests_total` | counter | method, route, status (`2xx`/`4xx`/`5xx`) | request count; 4xx and 5xx are the HTTP error counts |
| `kirana_http_request_duration_seconds` | histogram | method, route | request duration |
| `kirana_db_errors_total` | counter | | database errors turned into an error response |
| `kirana_external_api_errors_total` | counter | | outside-service failures (price, barcode, AI provider calls) |
| `kirana_ai_failures_total` | counter | | AI provider failures |
| `kirana_notification_failures_total` | counter | | notification deliveries that could not be sent |
| `kirana_job_runs_total` | counter | job_type, outcome | background job results (`COMPLETED`, `RETRYING`, `FAILED`) |
| `kirana_auth_events_total` | counter | event (`login_ok`, `login_failed`, `login_paused`) | sign-in outcomes |
| `kirana_system_events_total` | counter | category, severity | platform events |

**No personal data**: labels are a method, a route *template* (`/api/v1/customers/{id}`: record numbers masked), a status class and a fixed
event name. Never a shop, user, email, token or business value. Paths outside the API and any beyond 300 distinct routes share the label `other`.

The counters are **per process** and reset on restart. With several processes Prometheus scrapes each and sums them; with one process
nothing more is needed. There is **no OpenTelemetry integration** and none is required: feed your platform from the same call sites
(`app/core/metrics.py`), or from the logs.

## Prometheus example

```yaml
scrape_configs:
  - job_name: kirana
    metrics_path: /metrics
    scheme: https
    authorization: { type: Bearer, credentials_file: /etc/prometheus/kirana.token }
    static_configs: [{ targets: ["shop.example.com"] }]
```

Metrics are reached through the reverse proxy, so restrict `/metrics` there to your monitoring's address as well.

## What to alert on (suggestions, not shipped rules)

* `/health/ready` not `ok` for 2 minutes. * `rate(kirana_http_requests_total{status="5xx"}[5m])` above your baseline.
* p95 of `kirana_http_request_duration_seconds` above 2 s. * any increase in `kirana_db_errors_total`.
* `kirana_job_runs_total{outcome="FAILED"}` increasing, or jobs stuck `PENDING` (no worker running).
* a burst of `kirana_auth_events_total{event="login_failed"}` (guessing) or `login_paused`.
* the newest backup older than a day (`/api/v1/admin/system/health` reports staleness; owners see a status line).
* `kirana_ai_failures_total`, `kirana_notification_failures_total`, `kirana_external_api_errors_total` rising (a provider is down).
