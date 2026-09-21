"""System administration: who the operators are, what each role may do, and everything they did.

This is separate from a shop's own users. A system administrator is not a shop's OWNER or STAFF, has no shop, and does not
automatically see any shop's business records. What they can do is a short list of permissions per role; each request is
checked against it on the server and written to `admin_audit_logs` (allowed or denied). Looking into ONE shop beyond its
account status needs a time-limited, reasoned support grant that a SUPER_ADMIN creates; even then the support view returns
counts and health, not rows of business data.

Identity today is a per-administrator secret token (`X-Admin-Token`) that is shown once when created and stored only as a
SHA-256 hash. Shop login does not exist yet (Phase 14); when it does, administrators move to it without changing anything else
here.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.types import utc_now
from app.models import (
    AdminAuditLog,
    Product,
    Shop,
    SupportAccessGrant,
    SystemAdmin,
    User,
)
from app.models.enums import AccountStatus, AdminRole
from app.reporting import integrity
from app.services import account_service, entitlement_service, system_event_service
from app.services.audit_service import record_system_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

TOKEN_PREFIX = "kadm_"
MIN_TOKEN_CHARS = 40

PERMISSIONS: dict[AdminRole, frozenset[str]] = {
    AdminRole.SUPER_ADMIN: frozenset(
        {
            "shops.view", "shops.lifecycle", "subscriptions.manage", "system.health", "events.view", "backups.view",
            "backups.manage", "backups.restore", "integrity.run", "audit.view", "support.grant", "support.view",
        }
    ),
    AdminRole.OPERATIONS_ADMIN: frozenset(
        {"shops.view", "shops.lifecycle", "system.health", "events.view", "backups.view", "backups.manage", "integrity.run"}
    ),
    AdminRole.SUPPORT_ADMIN: frozenset({"shops.view", "events.view", "support.view"}),
}  # fmt: skip


@dataclass(frozen=True)
class AdminIdentity:
    id: int
    email: str
    display_name: str
    role: AdminRole

    def can(self, permission: str) -> bool:
        return permission in PERMISSIONS[self.role]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_admin(
    session: Session, *, email: str, display_name: str, role: AdminRole
) -> tuple[SystemAdmin, str]:
    """Create an administrator and return the token. It is shown ONCE; only its hash is kept."""
    email = email.strip().lower()
    if not email or "@" not in email:
        raise InvalidInputError("Enter a valid e-mail address.", field="email")
    if session.scalar(select(SystemAdmin).where(SystemAdmin.email == email)) is not None:
        raise ConflictError("An administrator with this e-mail already exists.", field="email")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    admin = SystemAdmin(
        email=email, display_name=display_name.strip() or email, role=role,
        token_hash=hash_token(token), token_prefix=token[:10], is_active=True,
    )  # fmt: skip
    session.add(admin)
    session.flush()
    return admin, token


def rotate_token(session: Session, email: str) -> str:
    admin = session.scalar(select(SystemAdmin).where(SystemAdmin.email == email.strip().lower()))
    if admin is None:
        raise NotFoundError("Administrator not found")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    admin.token_hash, admin.token_prefix = hash_token(token), token[:10]
    session.flush()
    return token


def deactivate(session: Session, email: str) -> None:
    admin = session.scalar(select(SystemAdmin).where(SystemAdmin.email == email.strip().lower()))
    if admin is None:
        raise NotFoundError("Administrator not found")
    admin.is_active = False
    session.flush()


def authenticate(session: Session, token: str | None) -> AdminIdentity | None:
    """The administrator a token belongs to, or None. A missing, malformed, unknown or deactivated token is None."""
    if not token or not token.startswith(TOKEN_PREFIX) or len(token) < MIN_TOKEN_CHARS:
        return None
    admin = session.scalar(
        select(SystemAdmin).where(
            SystemAdmin.token_hash == hash_token(token), SystemAdmin.is_active.is_(True)
        )
    )
    if admin is None:
        return None
    admin.last_used_at = utc_now()
    return AdminIdentity(admin.id, admin.email, admin.display_name, admin.role)


def audit(
    session: Session,
    admin: AdminIdentity | None,
    action: str,
    *,
    permission: str | None,
    outcome: str,
    shop_id: int | None = None,
    detail: dict | None = None,
    request_id: str | None = None,
) -> None:
    session.add(
        AdminAuditLog(
            admin_id=admin.id if admin else None, action=action[:80], permission=permission, outcome=outcome,
            target_shop_id=shop_id, detail=detail, request_id=request_id,
        )
    )  # fmt: skip
    session.flush()


def list_audit(session: Session, *, limit: int = 50, offset: int = 0) -> tuple[list[AdminAuditLog], int]:
    total = session.scalar(select(func.count()).select_from(AdminAuditLog)) or 0
    rows = session.scalars(
        select(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(limit).offset(offset)
    )
    return list(rows), total


# --- Shops (account level only) --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ShopSummary:
    id: int
    name: str
    account_status: AccountStatus
    status_reason: str | None
    status_changed_at: datetime | None
    plan_code: str
    plan_source: str
    subscription_status: str | None
    created_at: datetime
    products: int
    users: int


def _summary(session: Session, shop: Shop) -> ShopSummary:
    e = entitlement_service.get_entitlements(session, shop.id)
    products = (
        session.scalar(select(func.count()).select_from(Product).where(Product.shop_id == shop.id)) or 0
    )
    users = session.scalar(select(func.count()).select_from(User).where(User.shop_id == shop.id)) or 0
    return ShopSummary(
        shop.id, shop.name, shop.account_status, shop.status_reason, shop.status_changed_at,
        e.plan_code, e.source, e.status, shop.created_at, products, users,
    )  # fmt: skip


def list_shops(
    session: Session, *, status: AccountStatus | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[ShopSummary], int]:
    conditions = [] if status is None else [Shop.account_status == status]
    total = session.scalar(select(func.count()).select_from(Shop).where(*conditions)) or 0
    shops = session.scalars(select(Shop).where(*conditions).order_by(Shop.id).limit(limit).offset(offset))
    return [_summary(session, s) for s in shops], total


def get_shop_summary(session: Session, shop_id: int) -> ShopSummary:
    shop = session.get(Shop, shop_id)
    if shop is None:
        raise NotFoundError("Shop not found")
    return _summary(session, shop)


def change_shop_status(
    session: Session, shop_id: int, status: AccountStatus, *, reason: str, admin: AdminIdentity
) -> ShopSummary:
    account_service.change_status(session, shop_id, status, reason=reason, actor=admin.email)
    return get_shop_summary(session, shop_id)


def assign_subscription(
    session: Session,
    shop_id: int,
    plan_code: str,
    *,
    admin: AdminIdentity,
    trial_days: int | None = None,
    notes: str | None = None,
) -> ShopSummary:
    from app.models.enums import SubscriptionStatus

    before = entitlement_service.get_entitlements(session, shop_id)
    ends = utc_now() + timedelta(days=trial_days) if trial_days else None
    entitlement_service.assign_plan(
        session, shop_id, plan_code,
        status=SubscriptionStatus.TRIAL if trial_days else SubscriptionStatus.ACTIVE, ends_at=ends, notes=notes,
    )  # fmt: skip
    record_system_audit(
        session, shop_id, entity_type="subscription", entity_id=None, action="plan_changed",
        before={"plan": before.plan_code}, after={"plan": plan_code, "trial_days": trial_days, "by": admin.email},
    )  # fmt: skip
    return get_shop_summary(session, shop_id)


# --- Support access ----------------------------------------------------------------------------------------------------------


def grant_support_access(
    session: Session, shop_id: int, admin_email: str, *, reason: str, hours: int, granted_by: AdminIdentity
) -> SupportAccessGrant:
    if not reason.strip():
        raise InvalidInputError("Give the reason for this support access.", field="reason")
    if not 1 <= hours <= 72:
        raise InvalidInputError("Support access lasts between 1 and 72 hours.", field="hours")
    target = session.scalar(
        select(SystemAdmin).where(
            SystemAdmin.email == admin_email.strip().lower(), SystemAdmin.is_active.is_(True)
        )
    )
    if target is None:
        raise NotFoundError("Administrator not found")
    if session.get(Shop, shop_id) is None:
        raise NotFoundError("Shop not found")
    grant = SupportAccessGrant(
        shop_id=shop_id, admin_id=target.id, granted_by=granted_by.id, reason=reason.strip()[:300],
        expires_at=utc_now() + timedelta(hours=hours),
    )  # fmt: skip
    session.add(grant)
    session.flush()
    return grant


def has_active_grant(session: Session, admin: AdminIdentity, shop_id: int) -> bool:
    now = utc_now()
    return (
        session.scalar(
            select(SupportAccessGrant.id)
            .where(
                SupportAccessGrant.admin_id == admin.id,
                SupportAccessGrant.shop_id == shop_id,
                SupportAccessGrant.revoked_at.is_(None),
                SupportAccessGrant.expires_at > now,
            )
            .limit(1)
        )
        is not None
    )


def support_view(session: Session, shop_id: int) -> dict:
    """What support may see with a grant: the shop's account, plan, usage, its own recent platform events, and an integrity
    summary (counts only). No sales, customers, amounts or documents."""
    summary = get_shop_summary(session, shop_id)
    usage = entitlement_service.usage_summary(session, shop_id)
    events, _ = system_event_service.recent(session, shop_id=shop_id, limit=20)
    findings = integrity.run(session, shop_id)
    return {
        "shop": summary,
        "usage": usage,
        "events": [
            {
                "category": e.category,
                "severity": e.severity.value,
                "source": e.source,
                "code": e.code,
                "message": e.message,
                "at": e.created_at,
            }
            for e in events
        ],
        "integrity": {
            "ok": not any(f.severity == "ERROR" for f in findings),
            "findings": [{"check": f.check, "severity": f.severity, "count": f.count} for f in findings],
        },
    }


def usage_overview(session: Session) -> dict:
    by_status = {status.value: 0 for status in AccountStatus}
    for status, count in session.execute(
        select(Shop.account_status, func.count()).group_by(Shop.account_status)
    ):
        by_status[status.value] = count
    return {"shops_by_status": by_status, "events_last_24h": system_event_service.counts_since(session, 24)}
