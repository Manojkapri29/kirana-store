"""The detailed health picture for authorised operators. The public readiness endpoint says only ok or not; this says why.

Contains migration revisions, disk space for backups, feature flags, the NAMES of configuration problems (never values), the
rate limiter's size, and counts of recent platform events. It contains no secret, no connection string and no path.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import ratelimit, schema_state
from app.core.config import Settings, get_settings
from app.db.types import utc_now
from app.models.enums import BackupStatus
from app.services import backup_service, notification_service, system_event_service
from app.services.ai_provider import provider_status


def detail(session: Session, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    database: dict[str, Any] = {"connected": True}
    try:
        session.execute(text("SELECT 1"))
        database["revision"] = schema_state.database_revision(session.connection())
        database["expected_revision"] = schema_state.code_head()
        database["up_to_date"] = database["revision"] == database["expected_revision"]
    except Exception:  # noqa: BLE001
        database = {"connected": False}
    backups: dict[str, Any] = {"provider": settings.backup_storage_provider}
    try:
        storage = backup_service.get_storage(settings)
        backups["free_mb"] = storage.free_bytes() // (1024 * 1024)
        latest = backup_service.latest_verified(session)
        backups["latest_verified_at"] = latest.created_at.isoformat() if latest else None
        backups["latest_age_hours"] = (
            round((utc_now() - latest.created_at).total_seconds() / 3600, 1) if latest else None
        )
        backups["stale"] = (
            latest is None
            or (utc_now() - latest.created_at).total_seconds() > settings.backup_stale_after_hours * 3600
        )
        backups["failed_recent"] = any(
            r.status is BackupStatus.FAILED for r in backup_service.list_records(session, limit=5)
        )
    except backup_service.BackupNotConfigured:
        backups["configured"] = False
    ai = provider_status(settings)
    return {
        "status": "ok"
        if database.get("connected") and database.get("up_to_date") and not settings.production_problems()
        else "degraded",
        "environment": settings.environment,
        "version": settings.app_version,
        "database": database,
        "configuration_problems": settings.production_problems(),
        "feature_flags": {
            "ai": settings.feature_ai,
            "exports": settings.feature_exports,
            "notifications": settings.feature_notifications,
            "price_lookups": settings.feature_price_lookups,
        },
        "rate_limiting": {"enabled": settings.rate_limit_enabled, "tracked_keys": ratelimit.limiter.keys()},
        "backups": backups,
        "ai_provider_configured": ai["configured"],
        "notification_channels": notification_service.channel_status(settings),
        "events_last_24h": system_event_service.counts_since(session, 24),
    }
