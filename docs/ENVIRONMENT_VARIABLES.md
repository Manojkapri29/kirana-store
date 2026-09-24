# Environment variables

Generated from `backend/app/core/config.py` (`Settings`) and kept honest by `tests/test_phase20_release.py`, which fails if a setting
is missing from `backend/.env.example`. Every backend variable is read from the environment (or `backend/.env`). **Nothing here is a secret default:**
secrets are unset until you provide them, and only their *names* are ever stored in the database (integrations).

Frontend: only `VITE_*` variables exist, and they are public (built into the JavaScript). Never put a secret in one.

In production the application refuses to start unless `KIRANA_SECRET_KEY` (32+ chars), `KIRANA_FRONTEND_URL` (https), `KIRANA_CORS_ORIGINS` (includes the frontend, not `*`), debug off, rate limiting on, Argon2 cost at or above the minimum, dev auth bypass off, secure cookies on and (if metrics are enabled) a metrics token are all set. See `Settings.production_problems`.

For PostgreSQL set `KIRANA_DATABASE_URL=postgresql+psycopg://user:password@host:5432/dbname` (the password lives only in the environment) and see `POSTGRES_MIGRATION_CHECKLIST.md`.

| Variable | Type | Default | Notes |
|---|---|---|---|
| `KIRANA_APP_NAME` | str | `Shop Manager API` |  |
| `KIRANA_APP_VERSION` | str | `0.1.0` |  |
| `KIRANA_ENVIRONMENT` | text choice | `development` |  |
| `KIRANA_DEBUG` | bool | `False` |  |
| `KIRANA_LOG_LEVEL` | text choice | `INFO` |  |
| `KIRANA_LOG_FORMAT` | text choice | `text` |  |
| `KIRANA_FRONTEND_URL` | str or none | `(unset)` |  |
| `KIRANA_SECRET_KEY` | secret (text) | `(unset)` | secret: set from your secret store, never commit |
| `KIRANA_RATE_LIMIT_ENABLED` | bool | `True` |  |
| `KIRANA_RATE_LIMIT_WINDOW_SECONDS` | int | `60` |  |
| `KIRANA_RATE_LIMIT_AI` | int | `30` |  |
| `KIRANA_RATE_LIMIT_IMAGE` | int | `20` |  |
| `KIRANA_RATE_LIMIT_EXTERNAL` | int | `40` |  |
| `KIRANA_RATE_LIMIT_COUPON` | int | `600` |  |
| `KIRANA_RATE_LIMIT_EXPORT` | int | `20` |  |
| `KIRANA_RATE_LIMIT_WEBHOOK` | int | `120` |  |
| `KIRANA_PASSWORD_HASH_CONCURRENCY` | int | `4` |  |
| `KIRANA_RATE_LIMIT_STORE_ORDER` | int | `10` |  |
| `KIRANA_RATE_LIMIT_SYNC` | int | `600` |  |
| `KIRANA_RATE_LIMIT_MESSAGE` | int | `60` |  |
| `KIRANA_RATE_LIMIT_PAYMENT` | int | `120` |  |
| `KIRANA_RATE_LIMIT_ADMIN` | int | `60` |  |
| `KIRANA_RATE_LIMIT_ADMIN_AUTH_FAILURES` | int | `10` |  |
| `KIRANA_RATE_LIMIT_AUTH` | int | `10` |  |
| `KIRANA_RATE_LIMIT_PUBLIC` | int | `60` |  |
| `KIRANA_SUSPENDED_POLICY` | text choice | `read_only` |  |
| `KIRANA_DEACTIVATED_POLICY` | text choice | `blocked` |  |
| `KIRANA_FEATURE_AI` | bool | `True` |  |
| `KIRANA_FEATURE_EXPORTS` | bool | `True` |  |
| `KIRANA_FEATURE_NOTIFICATIONS` | bool | `True` |  |
| `KIRANA_FEATURE_PRICE_LOOKUPS` | bool | `True` |  |
| `KIRANA_BACKUP_DIR` | str | `./data/backups` |  |
| `KIRANA_BACKUP_STORAGE_PROVIDER` | str | `local` |  |
| `KIRANA_BACKUP_KEEP_DAILY_DAYS` | int | `14` |  |
| `KIRANA_BACKUP_KEEP_WEEKLY_WEEKS` | int | `8` |  |
| `KIRANA_BACKUP_MIN_FREE_MB` | int | `200` |  |
| `KIRANA_BACKUP_STALE_AFTER_HOURS` | int | `36` |  |
| `KIRANA_RESTORE_VIA_API_ENABLED` | bool | `False` |  |
| `KIRANA_NOTIFICATION_EMAIL_PROVIDER` | str or none | `(unset)` |  |
| `KIRANA_NOTIFICATION_SMS_PROVIDER` | str or none | `(unset)` |  |
| `KIRANA_NOTIFICATION_WHATSAPP_PROVIDER` | str or none | `(unset)` |  |
| `KIRANA_NOTIFICATION_PUSH_PROVIDER` | str or none | `(unset)` |  |
| `KIRANA_STORAGE_PROVIDER` | text choice | `local` |  |
| `KIRANA_STORAGE_S3_ENDPOINT` | str or none | `(unset)` |  |
| `KIRANA_STORAGE_S3_BUCKET` | str or none | `(unset)` |  |
| `KIRANA_STORAGE_S3_REGION` | str | `us-east-1` |  |
| `KIRANA_STORAGE_S3_PATH_STYLE` | bool | `True` |  |
| `KIRANA_STORAGE_CREDENTIALS_REF` | str | `KIRANA_INTEGRATION_STORAGE_CREDENTIALS` |  |
| `KIRANA_NOTIFICATION_MAX_ATTEMPTS` | int | `5` |  |
| `KIRANA_NOTIFICATION_BACKOFF_SECONDS` | int | `60` |  |
| `KIRANA_ALLOWED_IMAGE_TYPES` | list[str] | `['image/jpeg', 'image/png', 'image/webp']` |  |
| `KIRANA_DATABASE_URL` | str | `sqlite:///./data/kirana.db` |  |
| `KIRANA_DB_BUSY_TIMEOUT_MS` | int | `5000` |  |
| `KIRANA_DB_POOL_SIZE` | int | `10` |  |
| `KIRANA_DB_MAX_OVERFLOW` | int | `10` |  |
| `KIRANA_DB_POOL_RECYCLE_SECONDS` | int | `1800` |  |
| `KIRANA_DB_POOL_TIMEOUT_SECONDS` | int | `30` |  |
| `KIRANA_CORS_ORIGINS` | list[str] | `['http://localhost:5173']` |  |
| `KIRANA_DIAGNOSTICS_LOG_FILE` | str or none | `(unset)` |  |
| `KIRANA_IMAGE_MAX_BYTES` | int | `5000000` |  |
| `KIRANA_IMAGE_MAX_SIDE` | int | `8000` |  |
| `KIRANA_IMAGE_STORAGE_DIR` | str | `./data/images` |  |
| `KIRANA_IMAGE_MAX_PER_SHOP` | int | `500` |  |
| `KIRANA_IMAGE_ANALYSIS_PROVIDER` | str or none | `(unset)` |  |
| `IMAGE_ANALYSIS_API_KEY` | secret (text) | `(unset)` | secret: set from your secret store, never commit |
| `KIRANA_AI_PROVIDER` | str or none | `(unset)` |  |
| `AI_API_KEY` | secret (text) | `(unset)` | secret: set from your secret store, never commit |
| `KIRANA_AI_MODEL` | str or none | `(unset)` |  |
| `KIRANA_AI_TIMEOUT_SECONDS` | float | `20.0` |  |
| `KIRANA_AI_MAX_OUTPUT_TOKENS` | int | `1024` |  |
| `KIRANA_EXTERNAL_LOOKUPS_ENABLED` | bool | `True` |  |
| `KIRANA_EXTERNAL_TIMEOUT_SECONDS` | float | `4.0` |  |
| `KIRANA_PRICE_CACHE_TTL_HOURS` | int | `24` |  |
| `KIRANA_OFF_USER_AGENT` | str | `ShopManager/0.1 (contact: not-set)` |  |
| `UPCITEMDB_API_KEY` | secret (text) | `(unset)` | secret: set from your secret store, never commit |
| `KIRANA_SESSION_IDLE_MINUTES` | int | `60` |  |
| `KIRANA_SESSION_ABSOLUTE_HOURS` | int | `12` |  |
| `KIRANA_SESSION_COOKIE_NAME` | str | `kirana_session` |  |
| `KIRANA_CSRF_COOKIE_NAME` | str | `kirana_csrf` |  |
| `KIRANA_SESSION_COOKIE_SECURE` | bool or none | `(unset)` |  |
| `KIRANA_SESSION_COOKIE_SAMESITE` | text choice | `lax` |  |
| `KIRANA_LOGIN_MAX_FAILURES` | int | `5` |  |
| `KIRANA_LOGIN_LOCKOUT_MINUTES` | int | `15` |  |
| `KIRANA_PASSWORD_MIN_LENGTH` | int | `10` |  |
| `KIRANA_PASSWORD_HASH_TIME_COST` | int | `3` |  |
| `KIRANA_PASSWORD_HASH_MEMORY_KIB` | int | `65536` |  |
| `KIRANA_INVITATION_EXPIRY_HOURS` | int | `72` |  |
| `KIRANA_TRUST_PROXY_HEADERS` | bool | `False` |  |
| `KIRANA_DEV_AUTH_BYPASS` | bool | `False` |  |
| `KIRANA_METRICS_ENABLED` | bool | `False` |  |
| `KIRANA_METRICS_TOKEN` | secret (text) | `(unset)` | secret: set from your secret store, never commit |
| `KIRANA_JOB_MAX_ATTEMPTS` | int | `3` |  |
| `KIRANA_JOB_BACKOFF_SECONDS` | int | `30` |  |
