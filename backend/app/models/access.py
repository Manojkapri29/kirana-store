"""Sign-in identities, roles, invitations and sessions.

The model in one picture:

    accounts (a person who can sign in: one row per email)
        |  1 --- n
    users (one per shop the person belongs to: the MEMBERSHIP; every shop table's `created_by` points here)
        |  n --- 1                       |  n --- 1
    shops                              roles (system roles: shop_id NULL; custom roles: one shop)
                                            |  1 --- n
                                        role_permissions

`users` is the membership on purpose: 30+ tables reference `(shop_id, user_id)` for who did what, and that composite key is
what keeps a row of one shop from ever pointing at a person of another. A separate memberships table would only duplicate it.
A person in two shops therefore has one `accounts` row and two `users` rows. Nothing here stores a password or token in
plain text: passwords are Argon2id hashes, session, invitation and CSRF tokens are stored as SHA-256 hashes.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import UTCDateTime
from app.models.base import (
    Base,
    CreatedAtMixin,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    non_negative,
    not_blank,
)
from app.models.enums import InvitationStatus, LoginStatus


class Account(TimestampMixin, Base):
    """A person's sign-in identity. Not tied to any shop: shops are reached through memberships (`users`)."""

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("email"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        not_blank("email"),
        not_blank("full_name"),
        non_negative("failed_logins"),
    )

    id: Mapped[int] = id_column()
    email: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(
        String(255)
    )  # Argon2id, or "!" for "cannot sign in with a password"
    status: Mapped[LoginStatus] = mapped_column(
        enum_type(LoginStatus, "login_status"), default=LoginStatus.ACTIVE
    )
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_failed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Role(TimestampMixin, Base):
    """A named set of permissions. System roles (`shop_id` NULL) are shared defaults; a custom role belongs to one shop."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("shop_id", "code"),
        Index(
            "uq_roles_system_code",
            "code",
            unique=True,
            sqlite_where=text("shop_id IS NULL"),
            postgresql_where=text("shop_id IS NULL"),
        ),
        not_blank("code"),
        not_blank("name"),
        CheckConstraint(
            "(is_system = 1 AND shop_id IS NULL) OR (is_system = 0 AND shop_id IS NOT NULL)",
            name="system_means_global",
        ),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("shops.id"))
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(300))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (not_blank("permission"),)

    role_id: Mapped[int] = mapped_column(IdType, ForeignKey("roles.id"), primary_key=True)
    permission: Mapped[str] = mapped_column(String(60), primary_key=True)


class Invitation(TimestampMixin, Base):
    """An invitation to join a shop. Only a hash of its token is stored; the token is shown once to the inviter."""

    __tablename__ = "invitations"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        UniqueConstraint("shop_id", "id"),
        ForeignKeyConstraint(["shop_id", "invited_by"], ["users.shop_id", "users.id"]),
        not_blank("email"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        Index("ix_invitations_shop_status", "shop_id", "status"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = mapped_column(IdType, ForeignKey("shops.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(IdType, ForeignKey("roles.id"))
    token_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[InvitationStatus] = mapped_column(
        enum_type(InvitationStatus, "invitation_status"), default=InvitationStatus.PENDING
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    invited_by: Mapped[int] = mapped_column(IdType)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    accepted_user_id: Mapped[int | None] = mapped_column(IdType)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AuthSession(CreatedAtMixin, Base):
    """One signed-in browser or client. The session token itself is never stored, only its hash."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        Index("ix_auth_sessions_account", "account_id", "revoked_at"),
        Index("ix_auth_sessions_expires", "expires_at"),
    )

    id: Mapped[int] = id_column()
    token_hash: Mapped[str] = mapped_column(String(64))
    csrf_hash: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[int] = mapped_column(IdType, ForeignKey("accounts.id"))
    user_id: Mapped[int | None] = mapped_column(
        IdType, ForeignKey("users.id")
    )  # the shop membership in use; NULL until a shop is chosen
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(
        UTCDateTime
    )  # the absolute end; the idle limit is applied on top
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_reason: Mapped[str | None] = mapped_column(String(40))
