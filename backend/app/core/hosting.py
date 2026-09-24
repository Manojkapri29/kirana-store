"""Defaults for running on Vercel, applied only when Vercel says it is Vercel (the `VERCEL` variable) and only where the operator has not set the value.

A serverless function has a read-only disk, no long-running worker and a public address the platform chooses, so the safe settings differ from a server's:

* production mode, with the platform's own production address as the frontend and the allowed origin (the app and API share one address, so
  the session cookie needs no cross-origin settings) and the proxy headers trusted (Vercel sets them);
* scratch folders under /tmp (photos and backups written there do not survive: use S3 for photos and the database's own backups);
* a small database pool per instance (many instances share one Postgres server);
* a secret key derived from the database URL when none is set. The URL contains the database password, so the derived key is as secret as the
  database itself and stable across deployments. Set KIRANA_SECRET_KEY yourself to override it.
"""

import hashlib
import hmac
from collections.abc import MutableMapping


def apply_vercel_defaults(environ: MutableMapping[str, str]) -> None:
    if not environ.get("VERCEL"):
        return
    host = environ.get("VERCEL_PROJECT_PRODUCTION_URL") or environ.get("VERCEL_URL") or ""
    given = environ.setdefault
    given("KIRANA_ENVIRONMENT", "production")
    if host:
        given("KIRANA_FRONTEND_URL", f"https://{host}")
        given("KIRANA_CORS_ORIGINS", f"https://{host}")
    given("KIRANA_TRUST_PROXY_HEADERS", "true")
    given("KIRANA_IMAGE_STORAGE_DIR", "/tmp/kirana-images")  # noqa: S108  (serverless scratch space)
    given("KIRANA_BACKUP_DIR", "/tmp/kirana-backups")  # noqa: S108
    given("KIRANA_DB_POOL_SIZE", "2")
    given("KIRANA_DB_MAX_OVERFLOW", "3")
    given("KIRANA_LOG_FORMAT", "json")
    database = environ.get("KIRANA_DATABASE_URL") or environ.get("DATABASE_URL") or environ.get("POSTGRES_URL") or ""
    if database and not environ.get("KIRANA_SECRET_KEY"):
        environ["KIRANA_SECRET_KEY"] = hmac.new(b"kirana-vercel-secret-key", database.encode(), hashlib.sha256).hexdigest()  # 64 hex characters
