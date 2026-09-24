"""System administration: an internal API for the people who operate the platform, not for shop owners.

Every route requires a system administrator's token (`X-Admin-Token`) and a role that carries the route's permission; the check
and an audit row happen on the server for every call, allowed or denied. These routes deal with accounts, platform health,
backups and support. They cannot read a shop's business records, and there is no route here that can: looking into one shop
beyond its account needs a time-limited support grant, and even then only counts and health are returned.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.admin_deps import admin_permission
from app.core.config import get_settings
from app.db.session import get_session, write_transaction
from app.models.enums import AccountStatus, BackupKind
from app.reporting import integrity
from app.schemas.admin import (
    AdminAuditListOut,
    AdminAuditOut,
    AdminMeOut,
    BackupOut,
    EventListOut,
    EventOut,
    GrantIn,
    GrantOut,
    IntegrityOut,
    RestoreIn,
    RestoreOut,
    RetentionIn,
    RetentionOut,
    ShopListOut,
    ShopOut,
    StatusIn,
    SubscriptionIn,
    SupportViewOut,
    VerificationOut,
)
from app.services import (
    admin_service,
    background_job_service,
    backup_service,
    restore_service,
    system_event_service,
    system_health_service,
)
from app.services.admin_service import PERMISSIONS, AdminIdentity
from app.services.errors import NotFoundError

router = APIRouter(prefix="/admin", tags=["admin (internal)"])
ReadSession = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def _shop(summary) -> ShopOut:  # noqa: ANN001
    return ShopOut(**summary.__dict__)


def _backup(record) -> BackupOut:  # noqa: ANN001
    return BackupOut(
        backup_key=record.backup_key, kind=record.kind.value, status=record.status.value,
        storage_provider=record.storage_provider, size_bytes=record.size_bytes, sha256=record.sha256,
        schema_revision=record.schema_revision, initiated_by=record.initiated_by, error_code=record.error_code,
        error_message=record.error_message, created_at=record.created_at, verified_at=record.verified_at, deleted_at=record.deleted_at,
    )  # fmt: skip


def _verification(v) -> VerificationOut | None:  # noqa: ANN001
    return (
        None
        if v is None
        else VerificationOut(
            ok=v.ok, checks=v.checks, schema_revision=v.schema_revision, compatibility=v.compatibility
        )
    )


def _restore_out(result) -> RestoreOut:  # noqa: ANN001
    return RestoreOut(
        ok=result.ok, mode=result.mode.value, backup_key=result.backup_key, detail=result.detail,
        pre_restore_key=result.pre_restore_key, report=result.report, verification=_verification(result.verification),
        confirmation_phrase=restore_service.confirmation_phrase(result.backup_key),
    )  # fmt: skip


@router.get("/me", response_model=AdminMeOut, summary="Who am I, and what may I do?")
def me(admin: Annotated[AdminIdentity, Depends(admin_permission("shops.view"))]) -> AdminMeOut:
    return AdminMeOut(
        email=admin.email,
        display_name=admin.display_name,
        role=admin.role,
        permissions=sorted(PERMISSIONS[admin.role]),
    )


# --- Shops ---------------------------------------------------------------------------------------------------------------


@router.get("/shops", response_model=ShopListOut, summary="Shops and their account state")
def shops(
    _: Annotated[AdminIdentity, Depends(admin_permission("shops.view"))],
    session: ReadSession,
    status: AccountStatus | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> ShopListOut:
    items, total = admin_service.list_shops(session, status=status, limit=limit, offset=offset)
    return ShopListOut(items=[_shop(s) for s in items], total=total, limit=limit, offset=offset)


@router.get("/shops/{shop_id}", response_model=ShopOut, summary="One shop's account state (no business data)")
def shop(
    shop_id: int, _: Annotated[AdminIdentity, Depends(admin_permission("shops.view"))], session: ReadSession
) -> ShopOut:
    return _shop(admin_service.get_shop_summary(session, shop_id))


@router.post(
    "/shops/{shop_id}/status", response_model=ShopOut, summary="Suspend, deactivate or reactivate a shop"
)
def set_status(
    shop_id: int,
    payload: StatusIn,
    admin: Annotated[AdminIdentity, Depends(admin_permission("shops.lifecycle"))],
) -> ShopOut:
    """Changes only what the shop may do. No business data is deleted or altered. A reason is required and recorded."""
    with write_transaction() as session:
        summary = admin_service.change_shop_status(
            session, shop_id, payload.status, reason=payload.reason, admin=admin
        )
        admin_service.audit(session, admin, "shops.status_change", permission="shops.lifecycle", outcome="ALLOWED", shop_id=shop_id,
                            detail={"status": payload.status.value, "reason": payload.reason})  # fmt: skip
        return _shop(summary)


@router.post(
    "/shops/{shop_id}/subscription",
    response_model=ShopOut,
    summary="Put a shop on a plan (no payment involved)",
)
def set_subscription(
    shop_id: int,
    payload: SubscriptionIn,
    admin: Annotated[AdminIdentity, Depends(admin_permission("subscriptions.manage"))],
) -> ShopOut:
    with write_transaction() as session:
        summary = admin_service.assign_subscription(
            session,
            shop_id,
            payload.plan_code,
            admin=admin,
            trial_days=payload.trial_days,
            notes=payload.notes,
        )
        admin_service.audit(session, admin, "subscriptions.assign", permission="subscriptions.manage", outcome="ALLOWED", shop_id=shop_id,
                            detail={"plan": payload.plan_code, "trial_days": payload.trial_days})  # fmt: skip
        return _shop(summary)


@router.post(
    "/shops/{shop_id}/support-grants",
    response_model=GrantOut,
    status_code=201,
    summary="Allow an administrator to open one shop's support view",
)
def grant_access(
    shop_id: int,
    payload: GrantIn,
    admin: Annotated[AdminIdentity, Depends(admin_permission("support.grant"))],
) -> GrantOut:
    with write_transaction() as session:
        grant = admin_service.grant_support_access(
            session,
            shop_id,
            payload.admin_email,
            reason=payload.reason,
            hours=payload.hours,
            granted_by=admin,
        )
        admin_service.audit(session, admin, "support.grant", permission="support.grant", outcome="ALLOWED", shop_id=shop_id,
                            detail={"to": payload.admin_email, "hours": payload.hours, "reason": payload.reason})  # fmt: skip
        return GrantOut(
            id=grant.id,
            shop_id=shop_id,
            admin_email=payload.admin_email.strip().lower(),
            reason=grant.reason,
            expires_at=grant.expires_at,
        )


@router.get(
    "/shops/{shop_id}/support-view",
    response_model=SupportViewOut,
    summary="A shop's health, with an active support grant (counts only)",
)
def support_view(
    shop_id: int,
    admin: Annotated[AdminIdentity, Depends(admin_permission("support.view"))],
    session: ReadSession,
) -> SupportViewOut:
    if not admin_service.has_active_grant(session, admin, shop_id):
        raise HTTPException(
            status_code=403,
            detail="There is no active support access for this shop. A super administrator can grant it.",
        )
    data = admin_service.support_view(session, shop_id)
    return SupportViewOut(
        shop=_shop(data["shop"]), usage=data["usage"], events=data["events"], integrity=data["integrity"]
    )


# --- System ----------------------------------------------------------------------------------------------------------------


@router.get("/system/health", summary="Detailed system health (operators only)")
def system_health(
    _: Annotated[AdminIdentity, Depends(admin_permission("system.health"))], session: ReadSession
) -> dict:
    return system_health_service.detail(session)


@router.get("/system/jobs", summary="Recent background jobs (a worker must be running for them to be done)")
def jobs(
    _: Annotated[AdminIdentity, Depends(admin_permission("system.health"))],
    session: ReadSession,
    limit: Limit = 30,
) -> list[dict]:
    return [
        {
            "id": j.id, "type": j.job_type, "status": j.status.value, "attempts": j.attempts, "max_attempts": j.max_attempts,
            "created_at": j.created_at, "started_at": j.started_at, "completed_at": j.completed_at,
            "error_code": j.error_code, "error_message": j.error_message,
        }
        for j in background_job_service.list_jobs(session, limit=limit)
    ]  # fmt: skip


@router.get("/system/overview", summary="Shops by state and recent platform events")
def overview(
    _: Annotated[AdminIdentity, Depends(admin_permission("system.health"))], session: ReadSession
) -> dict:
    return admin_service.usage_overview(session)


@router.get(
    "/events",
    response_model=EventListOut,
    summary="Integration, AI, notification, backup and security events",
)
def events(
    _: Annotated[AdminIdentity, Depends(admin_permission("events.view"))],
    session: ReadSession,
    category: str | None = Query(default=None, max_length=30),
    shop_id: int | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> EventListOut:
    rows, total = system_event_service.recent(
        session, category=category, shop_id=shop_id, limit=limit, offset=offset
    )
    return EventListOut(items=[EventOut(id=e.id, shop_id=e.shop_id, category=e.category, severity=e.severity.value, source=e.source,
                                        code=e.code, message=e.message, request_id=e.request_id, created_at=e.created_at) for e in rows],
                        total=total, limit=limit, offset=offset)  # fmt: skip


@router.get("/audit", response_model=AdminAuditListOut, summary="What administrators did, allowed or denied")
def audit_log(
    _: Annotated[AdminIdentity, Depends(admin_permission("audit.view"))],
    session: ReadSession,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AdminAuditListOut:
    rows, total = admin_service.list_audit(session, limit=limit, offset=offset)
    return AdminAuditListOut(items=[AdminAuditOut(id=r.id, admin_id=r.admin_id, action=r.action, permission=r.permission, outcome=r.outcome,
                                                  target_shop_id=r.target_shop_id, detail=r.detail, request_id=r.request_id, created_at=r.created_at) for r in rows],
                             total=total, limit=limit, offset=offset)  # fmt: skip


@router.get(
    "/integrity",
    response_model=IntegrityOut,
    summary="Read-only data integrity check (reports, never repairs)",
)
def run_integrity(
    _: Annotated[AdminIdentity, Depends(admin_permission("integrity.run"))],
    session: ReadSession,
    shop_id: int | None = None,
) -> IntegrityOut:
    return IntegrityOut(**integrity.summary(integrity.run(session, shop_id)))


# --- Backups and restore -----------------------------------------------------------------------------------------------------


@router.get("/backups", response_model=list[BackupOut], summary="Backups and their state")
def backups(
    _: Annotated[AdminIdentity, Depends(admin_permission("backups.view"))],
    session: ReadSession,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[BackupOut]:
    return [_backup(r) for r in backup_service.list_records(session, limit=limit, offset=offset)]


@router.post(
    "/backups", response_model=BackupOut, status_code=201, summary="Take a consistent, verified backup now"
)
def create_backup(admin: Annotated[AdminIdentity, Depends(admin_permission("backups.manage"))]) -> BackupOut:
    """The database is copied with SQLite's online backup, verified, checksummed and described. A disk or database problem
    is returned as a FAILED backup (and noted for the operators), never as a crash."""
    settings = get_settings()
    outcome = backup_service.perform_backup(BackupKind.MANUAL, admin.email, settings)
    with write_transaction() as session:
        return _backup(backup_service.record_outcome(session, outcome, settings.backup_storage_provider))


def _record(session: Session, key: str):  # noqa: ANN202
    record = backup_service.get_record(session, key)
    if record is None:
        raise NotFoundError("Backup not found")
    return record


@router.post(
    "/backups/{backup_key}/verify",
    response_model=VerificationOut,
    summary="Re-check a backup's checksum and integrity",
)
def verify_backup(
    backup_key: str, admin: Annotated[AdminIdentity, Depends(admin_permission("backups.manage"))]
) -> VerificationOut:
    with write_transaction() as session:
        result = backup_service.verify_record(session, _record(session, backup_key))
        return _verification(result)  # type: ignore[return-value]


@router.post(
    "/backups/{backup_key}/rehearse-restore",
    response_model=RestoreOut,
    summary="Try a restore on a temporary copy (the live database is untouched)",
)
def rehearse(
    backup_key: str, admin: Annotated[AdminIdentity, Depends(admin_permission("backups.manage"))]
) -> RestoreOut:
    with write_transaction() as session:
        record = _record(session, backup_key)
        result = restore_service.rehearse(record, admin.email)
        restore_service.record_attempt(session, result, admin.email)
        return _restore_out(result)


@router.post(
    "/backups/retention",
    response_model=RetentionOut,
    summary="Apply the retention policy (a dry run unless apply is true)",
)
def retention(
    payload: RetentionIn, admin: Annotated[AdminIdentity, Depends(admin_permission("backups.manage"))]
) -> RetentionOut:
    with write_transaction() as session:
        plan = backup_service.apply_retention(session, actor=admin.email, dry_run=not payload.apply)
        return RetentionOut(dry_run=not payload.apply, **plan)


@router.post(
    "/backups/{backup_key}/restore",
    response_model=RestoreOut,
    summary="Restore over the live database (off unless enabled; needs the confirmation phrase)",
)
def restore(
    backup_key: str,
    payload: RestoreIn,
    admin: Annotated[AdminIdentity, Depends(admin_permission("backups.restore"))],
) -> RestoreOut:
    settings = get_settings()
    with write_transaction() as session:
        record = _record(session, backup_key)
    safety_outcomes: list = []
    result = restore_service.restore(record, confirmation=payload.confirmation, actor=admin.email, via_api=True, settings=settings,
                                     pre_restore_recorder=safety_outcomes.append)  # fmt: skip
    if result.ok:
        restore_service.record_restore(result, admin.email, settings)
    return _restore_out(result)
