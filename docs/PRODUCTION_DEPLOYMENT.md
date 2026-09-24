# Production deployment

Nothing here deploys anything for you, and nothing was deployed. The files describe a deployment you run yourself, on any cloud or a
plain server. **The Dockerfiles and compose file could not be built or run in the environment where Phase 12 was written (no Docker was
installed): they are unverified. The backend and the frontend build, and the migration, start-up check, health endpoints and worker they
rely on were run directly and are tested.** Try the images in a scratch environment before trusting them.

## Architecture

```
Internet ──HTTPS──▶  Reverse proxy / load balancer   (terminates TLS; you provide it)
                          │  plain HTTP, private network
                          ▼
                   web  (nginx, frontend/Dockerfile, :8080)   serves the built React app, forwards /api and /health
                          │
                          ▼
                   backend (uvicorn + FastAPI, backend/Dockerfile, :8000)     worker (same image: python -m app.worker --loop --schedule)
                          │                                                          │
                          └────────────────  SQLite file on the /data volume  ───────┘   (PostgreSQL later: see below)
```

One origin for the browser: the `web` container forwards `/api` to the backend, so the session cookie needs no cross-origin settings.

## What you must provide

| Requirement | Why / how |
| --- | --- |
| **HTTPS** | The session cookie is `Secure` in production and will not be sent over HTTP. Terminate TLS in a reverse proxy or load balancer (Caddy, nginx, Traefik, a cloud LB) and forward to `web:8080`. The compose file binds `web` to `127.0.0.1` so it is not exposed by accident. **Locally there is no HTTPS and none is pretended**: development uses plain HTTP and a non-Secure cookie. |
| **Proxy headers** | The proxy must set `X-Forwarded-For` and `X-Forwarded-Proto`. Set `KIRANA_TRUST_PROXY_HEADERS=true` only when it does (rate limits and logs then use the real client address). |
| **Trusted origins / CORS** | `KIRANA_FRONTEND_URL=https://shop.example.com` and `KIRANA_CORS_ORIGINS=https://shop.example.com`. Production refuses to start if the frontend URL is not https, CORS is `*`, or the frontend origin is missing. |
| **Request size limits** | nginx allows 12 MB (`client_max_body_size`, in `frontend/nginx.conf`); the backend limits photos itself (`KIRANA_IMAGE_MAX_BYTES`). Set the same or a lower limit at your proxy. |
| **Secrets** | `KIRANA_SECRET_KEY` (32+ random characters), and any provider keys (`AI_API_KEY`, ...) from the environment or your platform's secret store. **Never** in an image, in Git, or in a `VITE_*` variable. `backend.env` and `.env` are git-ignored. |
| **Persistent storage** | The `/data` volume holds the SQLite file and the backups. Back it up off the machine. |

## Configuration

Copy `backend/.env.example` to `backend.env` (compose) or set the variables in your platform. The important ones are in `PRODUCTION.md`
and `AUTHENTICATION.md`. In production the application **will not start** with a missing or weak secret, an insecure cookie setting, debug on, CORS `*`,
`KIRANA_DEV_AUTH_BYPASS`, a weak Argon2 cost, or metrics enabled without a token. The messages name the setting, never its value.

## First start

```bash
# 1. build and start (migrations run in the backend container because KIRANA_RUN_MIGRATIONS=true there)
docker compose -f docker-compose.prod.yml up -d --build
# 2. create the first shop and owner (prints a one-time password)
docker compose -f docker-compose.prod.yml exec backend python -m app.account_cli create-shop \
    --shop-name "Sharma Store" --owner-email owner@example.com --owner-name "R Sharma"
# 3. check
curl -fsS https://shop.example.com/health/ready
```

Without Docker: `pip install -r backend/requirements.txt`, `alembic upgrade head`, `uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers`,
`npm ci && npm run build` and serve `frontend/dist` with any static server plus the `/api` proxy, and run `python -m app.worker --loop --schedule` under a supervisor.

## Migration workflow

1. **Back up** (`python -m app.backup_cli create`). 2. Read the new migration. 3. Apply it: `alembic upgrade head` (in compose, only the `backend`
service migrates; the `worker` never does). 4. `python -m app.integrity_cli` (read-only) and `/health/ready` (it fails while the schema is behind
the code). 5. Roll back with a **restore** of the backup (`BACKUP_AND_RESTORE.md`): a database downgrade is a last resort, and migration
`0015` refuses to downgrade while one email belongs to two shops. New releases: build the new image, stop the old, start the new; the migration runs once.

## The database

**SQLite (the MVP deployment).** One file, one machine. The application already configures: WAL journal mode (readers do not block the writer),
`busy_timeout` (a waiting writer retries instead of failing), foreign keys **on** for every connection, and `BEGIN IMMEDIATE` for writers (so
write conflicts are queued, not deadlocked). Concurrency is bounded on purpose: **run one backend process (or a few workers on the same
machine) and never put the file on a network share**. Back up with `backup_cli` (the online backup API, not `cp`). Restore only with the application stopped.

**PostgreSQL (recommended for more than one instance).** Verified on a local PostgreSQL 16 server (`POSTGRES_MIGRATION_CHECKLIST.md`); a remote/managed service was not. For
when it is: use a connection pooler (PgBouncer, or SQLAlchemy's pool sized to the workers), run migrations from one place as a release step,
back up with `pg_dump`/base backups or your provider's snapshots and **test restores**, require SSL (`sslmode=verify-full`), keep the credentials in the
secret store, and give the application a role that owns only its own schema (no superuser, no `CREATE DATABASE`; the migration role may differ from the runtime role).
Rate limiting and the in-memory metrics are per process; several instances need a shared store (Redis) for the limiter.

## Containers

* `backend/Dockerfile`: `python:3.13-slim`, a **non-root** user (uid 10001, no shell, no write access to the code), a `/data` volume, a health check on
  `/health/live`, `--proxy-headers`, no server header. `.dockerignore` keeps `.env`, databases and tests out of the build.
* `frontend/Dockerfile`: a Node build stage, then `nginx-unprivileged` (non-root, port 8080), a strict Content-Security-Policy and security headers,
  SPA fallback, long caching for fingerprinted assets only, `/nginx-health`.
* `docker-compose.prod.yml`: `backend`, `worker`, `web`; read-only root filesystems, `cap_drop: ALL`, `no-new-privileges`, health checks, restart policy.
  **No `.env` is copied into any image.** `VITE_*` build arguments are public by design.

## Reverse proxy example (Caddy)

```
shop.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8080
    # Caddy sets X-Forwarded-For/-Proto and gets the certificate automatically
}
```

## Checklist before real users

- [ ] HTTPS works and `Secure` cookies are set (check `Set-Cookie` in the browser)
- [ ] `KIRANA_ENVIRONMENT=production`; the app started (it refuses unsafe settings)
- [ ] `/health/ready` is `ok`; `/docs` and `/openapi.json` are 404
- [ ] First owner created, password changed after first sign-in; no development account exists
- [ ] Daily backup runs (the worker's `--schedule`, or cron) **and** copies leave the machine; a restore was rehearsed (`backup_cli rehearse`)
- [ ] Metrics and logs are collected (`MONITORING.md`); an alert exists for `/health/ready` failing
- [ ] The proxy limits request size and sets forwarded headers


## Security headers at the web container (updated)

nginx does not inherit `add_header` from the server block into a `location` that sets its own, so the Content-Security-Policy, `X-Frame-Options` and the other headers
were missing on the app page (`/`) and the built files. They now live in `frontend/security-headers.conf` (copied to `/etc/nginx/snippets/` by the Dockerfile) and every
location that sets a header includes it; a test enforces this and the config was checked with a real nginx (`nginx -t` and `curl` on `/`, `/assets/*`, `/sw.js`, `/health/live`).
`Strict-Transport-Security` is sent too; browsers ignore it over plain HTTP, so it takes effect once your HTTPS proxy serves the response.
