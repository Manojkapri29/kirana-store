"""Authentication: sign in, sessions, sign out, changing a password.

A session is an opaque random token. The browser holds it in an HttpOnly cookie (or a client sends it as a Bearer token);
the database holds only its SHA-256 hash, so a database leak does not leak sessions. Every request looks the session up,
checks it has not been revoked, has not passed its idle or absolute limit, and that the account and the membership it is
using are still active; nothing about who someone is or what they may do is read from the client.

Sign-in is throttled three ways: a rate limit per client address, a rate limit per email, and a temporary pause of an
account after several wrong passwords in a row. A wrong email and a wrong password get the same answer in about the same time.
Services never commit: the caller owns the transaction.
"""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import metrics, observability, ratelimit
from app.core.config import Settings, get_settings
from app.core.permissions import ALL_PERMISSIONS
from app.db.types import utc_now
from app.models import Account, AuthSession, Role, Shop, User
from app.models.enums import EventSeverity, LoginStatus, MembershipStatus, UserRole
from app.services import audit_service, authorization_service, password_service, system_event_service
from app.services.errors import AuthenticationError, ForbiddenError, InvalidInputError, RateLimitedError

INVALID = "Invalid email or password."
TOO_MANY = "Too many sign-in attempts. Please wait a while and try again."
TOUCH_EVERY = timedelta(seconds=60)  # how often a busy session's last-seen time is written


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_token(token: str) -> str:
    return _sha256(token)


@dataclass(frozen=True)
class MembershipView:
    user_id: int
    shop_id: int
    shop_name: str
    role_code: str
    role_name: str
    status: MembershipStatus


@dataclass
class LoginResult:
    token: str
    csrf_token: str
    expires_at: datetime
    account: Account
    memberships: list[MembershipView]
    active_user_id: int | None


@dataclass
class Resolved:
    """Everything the request path needs, worked out from one session token."""

    account: Account
    session: AuthSession
    user: User | None  # the membership in use, if a shop has been chosen
    role: Role | None
    permissions: frozenset[str]


# --- Memberships ------------------------------------------------------------------------------------------------------


def memberships_of(session: Session, account_id: int, *, only_active: bool = True) -> list[MembershipView]:
    query = (
        select(User, Shop, Role)
        .join(Shop, Shop.id == User.shop_id)
        .join(Role, Role.id == User.role_id)
        .where(User.account_id == account_id)
        .order_by(Shop.name, User.id)
    )
    if only_active:
        query = query.where(User.status == MembershipStatus.ACTIVE)
    return [
        MembershipView(u.id, u.shop_id, shop.name, role.code, role.name, u.status)
        for u, shop, role in session.execute(query)
    ]


# --- Sign in ----------------------------------------------------------------------------------------------------------


def _pause_seconds(account: Account, now: datetime) -> int:
    return max(1, int((account.locked_until - now).total_seconds())) if account.locked_until else 0


def _failed(session: Session, account: Account | None, settings: Settings, now: datetime) -> None:
    if account is not None:
        account.failed_logins += 1
        account.last_failed_at = now
        if account.failed_logins >= settings.login_max_failures:
            account.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            account.failed_logins = 0
            system_event_service.record(
                session, category="security", severity=EventSeverity.WARNING, source="auth", code="account_paused",
                message=f"Sign-in paused after repeated wrong passwords (account {account.id}).",
            )  # fmt: skip
    system_event_service.record(
        session, category="security", severity=EventSeverity.WARNING, source="auth", code="login_failed",
        message="A sign-in attempt failed." if account is None else f"A sign-in attempt failed (account {account.id}).",
    )  # fmt: skip


def login(
    session: Session, email: str, password: str, *, client_address: str, settings: Settings | None = None
) -> LoginResult:
    settings = settings or get_settings()
    now = utc_now()
    email = normalize_email(email)
    ratelimit.enforce("auth", f"ip:{client_address}", settings)
    ratelimit.enforce("auth", f"email:{_sha256(email)[:24]}", settings)

    account = session.scalar(select(Account).where(Account.email == email))
    if account is not None and account.locked_until is not None and account.locked_until > now:
        password_service.spend_the_same_time(password, settings)
        metrics.record_auth_event("login_paused")
        raise RateLimitedError(retry_after=_pause_seconds(account, now), group="auth")
    usable = account is not None and account.status is LoginStatus.ACTIVE
    if not usable or not password_service.verify_password(account.password_hash, password, settings):  # type: ignore[union-attr]
        if not usable:
            password_service.spend_the_same_time(password, settings)
        _failed(session, account, settings, now)
        metrics.record_auth_event("login_failed")
        raise AuthenticationError(INVALID, code="invalid_credentials")
    assert account is not None

    if password_service.needs_rehash(account.password_hash, settings):
        account.password_hash = password_service.hash_password(password, settings)
    account.failed_logins = 0
    account.locked_until = None
    account.last_login_at = now

    memberships = memberships_of(session, account.id)
    active = memberships[0].user_id if len(memberships) == 1 else None
    token, csrf, row = _new_session(session, account.id, active, settings, now)
    if active is not None:
        _touch_membership(session, active, now)
        _audit(session, memberships[0].shop_id, active, "login", account.id)
    metrics.record_auth_event("login_ok")
    observability.log_event("security", "sign-in succeeded", account_id=account.id)
    return LoginResult(token, csrf, row.expires_at, account, memberships, active)


def _new_session(
    session: Session, account_id: int, user_id: int | None, settings: Settings, now: datetime
) -> tuple[str, str, AuthSession]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    row = AuthSession(
        token_hash=hash_token(token), csrf_hash=_sha256(csrf), account_id=account_id, user_id=user_id,
        last_seen_at=now, expires_at=now + timedelta(hours=settings.session_absolute_hours),
    )  # fmt: skip
    session.add(row)
    session.flush()
    return token, csrf, row


def _audit(
    session: Session, shop_id: int, user_id: int, action: str, account_id: int, meta: dict | None = None
) -> None:
    audit_service.record_access_event(
        session, shop_id, user_id, action, "session", None, {"account_id": account_id, **(meta or {})}
    )


def _touch_membership(session: Session, user_id: int, now: datetime) -> None:
    session.execute(update(User).where(User.id == user_id).values(last_active_at=now))


# --- Resolving a session on every request ----------------------------------------------------------------------------------


def resolve_session(session: Session, token: str | None, settings: Settings | None = None) -> Resolved | None:
    """The account, session, membership and permissions behind a token, or None if it is unknown, revoked or expired."""
    if not token:
        return None
    settings = settings or get_settings()
    now = utc_now()
    row = session.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return None
    if row.last_seen_at + timedelta(minutes=settings.session_idle_minutes) <= now:
        return None
    return _resolved_from_row(session, row)


def resolve_row(session: Session, session_id: int) -> Resolved | None:
    """Re-read a session after it was changed (for example a shop was chosen). The caller already proved it is valid."""
    row = session.get(AuthSession, session_id)
    return None if row is None else _resolved_from_row(session, row)


def _resolved_from_row(session: Session, row: AuthSession) -> Resolved | None:
    account = session.get(Account, row.account_id)
    if account is None or account.status is not LoginStatus.ACTIVE:
        return None
    user: User | None = None
    role: Role | None = None
    permissions: frozenset[str] = frozenset()
    if row.user_id is not None:
        user = session.get(User, row.user_id)
        if (
            user is not None
            and user.account_id == account.id
            and user.status is MembershipStatus.ACTIVE
            and user.role_id
        ):
            role = session.get(Role, user.role_id)
            if role is not None:
                permissions = authorization_service.permissions_of_role(session, role)
        else:
            user = None  # suspended, removed or moved: the session is still good for choosing another shop
    return Resolved(account, row, user, role, permissions)


def csrf_matches(row: AuthSession, presented: str | None) -> bool:
    return bool(presented) and hmac.compare_digest(row.csrf_hash, _sha256(presented or ""))


def touch(session: Session, row_id: int, user_id: int | None) -> None:
    """Record activity (called at most about once a minute per session, from the request path)."""
    now = utc_now()
    session.execute(update(AuthSession).where(AuthSession.id == row_id).values(last_seen_at=now))
    if user_id is not None:
        _touch_membership(session, user_id, now)


def needs_touch(row: AuthSession) -> bool:
    return utc_now() - row.last_seen_at >= TOUCH_EVERY


# --- Choosing a shop, signing out, changing the password ----------------------------------------------------------------------


def select_shop(session: Session, resolved: Resolved, user_id: int) -> MembershipView:
    """Use one of the account's own active memberships for this session. Anyone else's is "not allowed"."""
    chosen = next((m for m in memberships_of(session, resolved.account.id) if m.user_id == user_id), None)
    if chosen is None:
        raise ForbiddenError("You do not have access to that shop.", code="not_a_member")
    row = session.get(AuthSession, resolved.session.id)
    assert row is not None
    row.user_id = chosen.user_id
    _touch_membership(session, chosen.user_id, utc_now())
    _audit(session, chosen.shop_id, chosen.user_id, "select_shop", resolved.account.id)
    return chosen


def revoke(session: Session, resolved: Resolved, reason: str = "logout") -> None:
    row = session.get(AuthSession, resolved.session.id)
    assert row is not None
    row.revoked_at = utc_now()
    row.revoked_reason = reason
    if resolved.user is not None:
        _audit(session, resolved.user.shop_id, resolved.user.id, "logout", resolved.account.id)


def revoke_all_for_account(
    session: Session, account_id: int, *, except_session_id: int | None, reason: str
) -> int:
    now = utc_now()
    condition = [AuthSession.account_id == account_id, AuthSession.revoked_at.is_(None)]
    if except_session_id is not None:
        condition.append(AuthSession.id != except_session_id)
    result = session.execute(
        update(AuthSession).where(*condition).values(revoked_at=now, revoked_reason=reason)
    )
    return result.rowcount or 0


def change_password(
    session: Session, resolved: Resolved, current: str, new: str, *, settings: Settings | None = None
) -> int:
    """Change the signed-in person's password. Their other sessions end; this one continues. Returns how many ended."""
    settings = settings or get_settings()
    account = session.get(Account, resolved.account.id)
    assert account is not None
    ratelimit.enforce("auth", f"pw:{account.id}", settings)
    if not password_service.verify_password(account.password_hash, current, settings):
        raise InvalidInputError("The current password is not right.", field="current_password")
    password_service.check_new_password(new, email=account.email, settings=settings)
    if password_service.verify_password(account.password_hash, new, settings):
        raise InvalidInputError("Choose a password you have not used just now.", field="password")
    account.password_hash = password_service.hash_password(new, settings)
    account.password_changed_at = utc_now()
    ended = revoke_all_for_account(
        session, account.id, except_session_id=resolved.session.id, reason="password_changed"
    )
    if resolved.user is not None:
        _audit(
            session,
            resolved.user.shop_id,
            resolved.user.id,
            "password_changed",
            account.id,
            {"sessions_ended": ended},
        )
    observability.log_event("security", "password changed", level=logging.INFO, account_id=account.id)
    return ended


def purge_expired(session: Session, *, older_than_days: int = 30) -> int:
    """Delete long-dead session rows (a maintenance job). Sessions carry no business history."""
    cutoff = utc_now() - timedelta(days=older_than_days)
    dead = session.scalars(
        select(AuthSession).where((AuthSession.expires_at < cutoff) | (AuthSession.revoked_at < cutoff))
    ).all()
    for row in dead:
        session.delete(row)
    return len(dead)


__all__ = ["ALL_PERMISSIONS", "UserRole"]
