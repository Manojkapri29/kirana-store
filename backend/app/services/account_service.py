"""A shop's account state and what it allows. SaaS operations, not billing.

States: ACTIVE and TRIAL work normally. SUSPENDED and DEACTIVATED restrict use according to two settings
(`suspended_policy`, `deactivated_policy`: "read_only" lets people look but not change; "blocked" allows only the account
page that explains the situation). What each state means commercially is the operator's decision and lives in configuration,
not code. Changing a state never deletes or edits business data, and it is recorded (who, why, when) in the audit log.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.types import utc_now
from app.models import Shop
from app.models.enums import AccountStatus
from app.services.audit_service import record_system_audit
from app.services.errors import AccountRestrictedError, InvalidInputError, NotFoundError

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Always reachable, so a restricted owner can see why and what to do.
ALWAYS_ALLOWED_PREFIXES = ("/api/v1/account",)

OWNER_MESSAGES = {
    AccountStatus.SUSPENDED: "This shop account is currently suspended. Please contact support to restore access.",
    AccountStatus.DEACTIVATED: "This shop account has been deactivated. Your data is safe. Please contact support.",
}


def get_status(session: Session, shop_id: int) -> tuple[AccountStatus, str | None]:
    row = session.execute(select(Shop.account_status, Shop.status_reason).where(Shop.id == shop_id)).first()
    if row is None:
        raise NotFoundError("Shop not found")
    return row[0], row[1]


def restriction(
    session: Session, shop_id: int, method: str, path: str, settings: Settings | None = None
) -> AccountRestrictedError | None:
    """The error to raise if this request is not allowed for the shop's state, else None."""
    settings = settings or get_settings()
    status, _reason = get_status(session, shop_id)
    if status in (AccountStatus.ACTIVE, AccountStatus.TRIAL):
        return None
    if path.startswith(ALWAYS_ALLOWED_PREFIXES):
        return None
    policy = settings.suspended_policy if status is AccountStatus.SUSPENDED else settings.deactivated_policy
    if policy == "read_only" and method.upper() in SAFE_METHODS:
        return None
    return AccountRestrictedError(OWNER_MESSAGES[status], state=status.value)


def owner_message(status: AccountStatus, settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    if status in (AccountStatus.ACTIVE, AccountStatus.TRIAL):
        return None
    policy = settings.suspended_policy if status is AccountStatus.SUSPENDED else settings.deactivated_policy
    extra = " You can still view your records." if policy == "read_only" else ""
    return OWNER_MESSAGES[status] + extra


def change_status(session: Session, shop_id: int, new: AccountStatus, *, reason: str, actor: str) -> Shop:
    """Move a shop to a state. Requires a reason; recorded in the shop's audit log with the actor's label."""
    if not reason.strip():
        raise InvalidInputError("Give a reason for changing the account state.", field="reason")
    shop = session.scalar(select(Shop).where(Shop.id == shop_id).with_for_update())
    if shop is None:
        raise NotFoundError("Shop not found")
    before = shop.account_status
    if before is new:
        return shop
    shop.account_status = new
    shop.status_reason = reason.strip()[:300]
    shop.status_changed_at = utc_now()
    session.flush()
    record_system_audit(
        session,
        shop_id,
        entity_type="shop",
        entity_id=shop_id,
        action="account_status",
        before={"account_status": before.value},
        after={"account_status": new.value, "reason": reason.strip()[:300], "by": actor},
    )
    return shop
