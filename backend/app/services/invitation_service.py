"""Invitations: how a person joins a shop.

An authorised member invites an email address with a role. The invitation token is random, expires, works once, and only its
hash is stored: the plain token is returned once, to the inviter, and is never logged. No email is sent (no provider is
bundled): the inviter passes the link on themselves, exactly as with a shared document link, and that is stated in the screen.

Accepting is one transaction that locks the invitation row, so two people (or one person clicking twice) cannot both use it.
A person who already has an account proves it with their password; a new person chooses one.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import observability, ratelimit
from app.core.config import Settings, get_settings
from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import Account, Invitation, Role, Shop, User
from app.models.enums import InvitationStatus, LoginStatus, MembershipStatus, UserRole
from app.services import audit_service, auth_service, authorization_service, password_service
from app.services.errors import AuthenticationError, ConflictError, InvalidInputError, NotFoundError

INVALID = "This invitation is no longer valid."  # unknown, used, revoked and expired all look the same
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


@dataclass(frozen=True)
class Preview:
    shop_name: str
    email: str
    role_name: str
    has_account: bool
    expires_at: datetime


@dataclass(frozen=True)
class Created:
    invitation: Invitation
    token: str  # shown once


def _legacy_role(role: Role) -> UserRole:
    return (
        UserRole.OWNER if role.is_system and role.code == authorization_service.OWNER_CODE else UserRole.STAFF
    )


def create(
    session: Session, ctx: RequestContext, email: str, role_id: int, settings: Settings | None = None
) -> Created:
    settings = settings or get_settings()
    email = auth_service.normalize_email(email)
    if not _EMAIL.match(email):
        raise InvalidInputError("Enter a valid email address.", field="email")
    role = authorization_service.assignable_role(session, ctx.shop_id, role_id)
    authorization_service.check_grantable(ctx, authorization_service.permissions_of_role(session, role))

    account = session.scalar(select(Account).where(Account.email == email))
    if account is not None:
        member = session.scalar(
            select(User).where(User.account_id == account.id, User.shop_id == ctx.shop_id)
        )
        if member is not None and member.status in (MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED):
            raise ConflictError("This person is already a member of the shop.", field="email")
    now = utc_now()
    for old in session.scalars(
        select(Invitation).where(
            Invitation.shop_id == ctx.shop_id,
            Invitation.email == email,
            Invitation.status == InvitationStatus.PENDING,
        )
    ):
        old.status = InvitationStatus.REVOKED  # a new invitation replaces the one still waiting
        old.revoked_at = now
        audit_service.record_access_event(
            session,
            ctx.shop_id,
            ctx.user_id,
            "invitation_revoked",
            "invitation",
            old.id,
            {"reason": "replaced"},
        )

    token = secrets.token_urlsafe(32)
    invitation = Invitation(
        shop_id=ctx.shop_id, email=email, role_id=role.id, token_hash=auth_service.hash_token(token),
        expires_at=now + timedelta(hours=settings.invitation_expiry_hours), invited_by=ctx.user_id,
    )  # fmt: skip
    session.add(invitation)
    session.flush()
    audit_service.record_access_event(
        session,
        ctx.shop_id,
        ctx.user_id,
        "invitation_created",
        "invitation",
        invitation.id,
        {"role": role.code},
    )
    observability.log_event(
        "security", "staff invitation created", shop_id=ctx.shop_id, invitation_id=invitation.id
    )
    return Created(invitation, token)


def revoke(session: Session, ctx: RequestContext, invitation_id: int) -> Invitation:
    invitation = session.scalar(
        select(Invitation)
        .where(Invitation.id == invitation_id, Invitation.shop_id == ctx.shop_id)
        .with_for_update()
    )
    if invitation is None:
        raise NotFoundError("Invitation not found")
    if invitation.status is not InvitationStatus.PENDING:
        raise ConflictError("This invitation is not waiting any more.")
    invitation.status = InvitationStatus.REVOKED
    invitation.revoked_at = utc_now()
    audit_service.record_access_event(
        session,
        ctx.shop_id,
        ctx.user_id,
        "invitation_revoked",
        "invitation",
        invitation.id,
        {"reason": "revoked"},
    )
    return invitation


def effective_status(invitation: Invitation) -> InvitationStatus:
    """PENDING becomes EXPIRED once its time has passed (nothing has to run for that to be true)."""
    if invitation.status is InvitationStatus.PENDING and invitation.expires_at <= utc_now():
        return InvitationStatus.EXPIRED
    return invitation.status


def list_invitations(session: Session, shop_id: int) -> list[tuple[Invitation, Role]]:
    rows = session.execute(
        select(Invitation, Role).join(Role, Role.id == Invitation.role_id)
        .where(Invitation.shop_id == shop_id).order_by(Invitation.id.desc()).limit(200)
    ).all()  # fmt: skip
    return [(i, r) for i, r in rows]


def _usable(session: Session, token: str, *, lock: bool) -> Invitation:
    query = select(Invitation).where(Invitation.token_hash == auth_service.hash_token(token))
    invitation = session.scalar(query.with_for_update() if lock else query)
    if invitation is None or invitation.status is not InvitationStatus.PENDING:
        raise NotFoundError(INVALID)
    if invitation.expires_at <= utc_now():
        invitation.status = InvitationStatus.EXPIRED
        raise NotFoundError(INVALID)
    return invitation


def preview(session: Session, token: str, *, client_address: str) -> Preview:
    ratelimit.enforce("auth", f"invite:{client_address}")
    failure: NotFoundError | None = None
    try:
        invitation = _usable(session, token, lock=False)
    except NotFoundError as exc:
        failure = exc
    if failure is not None:
        raise failure
    shop = session.get(Shop, invitation.shop_id)
    role = session.get(Role, invitation.role_id)
    assert shop is not None and role is not None
    has_account = session.scalar(select(Account.id).where(Account.email == invitation.email)) is not None
    return Preview(
        shop_name=shop.name,
        email=invitation.email,
        role_name=role.name,
        has_account=has_account,
        expires_at=invitation.expires_at,
    )


def accept(
    session: Session,
    token: str,
    *,
    password: str,
    full_name: str | None,
    client_address: str,
    settings: Settings | None = None,
) -> auth_service.LoginResult:
    settings = settings or get_settings()
    ratelimit.enforce("auth", f"invite:{client_address}", settings)
    invitation = _usable(session, token, lock=True)
    role = session.get(Role, invitation.role_id)
    shop = session.get(Shop, invitation.shop_id)
    assert role is not None and shop is not None
    if not role.is_active:
        raise NotFoundError(INVALID)  # the role was retired while the invitation waited
    now = utc_now()

    account = session.scalar(select(Account).where(Account.email == invitation.email))
    if account is not None:
        # Someone who already has an account proves it is theirs with their password (counted like any sign-in).
        ratelimit.enforce("auth", f"email:{auth_service._sha256(invitation.email)[:24]}", settings)  # noqa: SLF001
        if account.status is not LoginStatus.ACTIVE or not password_service.verify_password(
            account.password_hash, password, settings
        ):
            password_service.spend_the_same_time(password, settings)
            raise AuthenticationError(auth_service.INVALID, code="invalid_credentials")
    else:
        if not full_name or not full_name.strip():
            raise InvalidInputError("Enter your name.", field="full_name")
        password_service.check_new_password(password, email=invitation.email, settings=settings)
        account = Account(
            email=invitation.email, full_name=full_name.strip(), password_hash=password_service.hash_password(password, settings),
            status=LoginStatus.ACTIVE, password_changed_at=now,
        )  # fmt: skip
        session.add(account)
        session.flush()

    member = session.scalar(select(User).where(User.account_id == account.id, User.shop_id == shop.id))
    if member is None:
        member = User(
            shop_id=shop.id, email=invitation.email, full_name=account.full_name, password_hash=password_service.UNUSABLE,
            role=_legacy_role(role), account_id=account.id, role_id=role.id, status=MembershipStatus.ACTIVE, is_active=True,
            invited_by=invitation.invited_by, joined_at=now,
        )  # fmt: skip
        session.add(member)
    else:  # a person who was removed or suspended and is invited again: the same membership (and its history) comes back
        member.role_id = role.id
        member.role = _legacy_role(role)
        member.status = MembershipStatus.ACTIVE
        member.is_active = True
        member.removed_at = None
        member.invited_by = invitation.invited_by
        member.joined_at = member.joined_at or now
    session.flush()

    invitation.status = InvitationStatus.ACCEPTED
    invitation.accepted_at = now
    invitation.accepted_user_id = member.id
    account.last_login_at = now
    account.failed_logins = 0
    audit_service.record_access_event(
        session, shop.id, member.id, "invitation_accepted", "invitation", invitation.id, {"role": role.code}
    )
    memberships = auth_service.memberships_of(session, account.id)
    session_token, csrf, row = auth_service._new_session(session, account.id, member.id, settings, now)  # noqa: SLF001
    return auth_service.LoginResult(session_token, csrf, row.expires_at, account, memberships, member.id)
