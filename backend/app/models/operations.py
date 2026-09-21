"""SaaS operations tables: system administrators and their audit trail, support access, system events, backups.

These are not shop data. A system administrator is a different kind of person from a shop's owner or staff: they have
their own identity, their own permissions and their own audit log, and nothing here gives them a way into a shop's
business records. `system_events` holds safe, short facts about how the platform is behaving (an outside service failing,
a notification not delivered, a backup that failed): never a customer, an amount, a document or a secret.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
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
from app.models.enums import AdminRole, BackupKind, BackupStatus, EventSeverity, RestoreMode


class SystemAdmin(TimestampMixin, Base):
    __tablename__ = "system_admins"
    __table_args__ = (
        UniqueConstraint("email"),
        UniqueConstraint("token_hash"),
        not_blank("email"),
        not_blank("display_name"),
    )

    id: Mapped[int] = id_column()
    email: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[AdminRole] = mapped_column(enum_type(AdminRole, "admin_role"))
    # Only the SHA-256 of the token is kept; the token itself is shown once, when it is created.
    token_hash: Mapped[str] = mapped_column(String(64))
    token_prefix: Mapped[str] = mapped_column(String(12))  # to recognise a token without revealing it
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AdminAuditLog(CreatedAtMixin, Base):
    """Every sensitive admin action, allowed or denied. INSERT-ONLY (a database trigger enforces it)."""

    __tablename__ = "admin_audit_logs"
    __table_args__ = (
        Index("ix_admin_audit_created", "created_at"),
        Index("ix_admin_audit_admin", "admin_id", "created_at"),
        not_blank("action"),
    )

    id: Mapped[int] = id_column()
    admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_admins.id")
    )  # NULL: an unknown token was refused
    action: Mapped[str] = mapped_column(String(80))
    permission: Mapped[str | None] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(10))  # ALLOWED or DENIED
    # No foreign key on purpose: the log records what an administrator asked for, even a shop id that does not exist.
    target_shop_id: Mapped[int | None] = mapped_column(IdType)
    detail: Mapped[Any | None] = mapped_column(JSON)  # safe metadata only: never a secret or business data
    request_id: Mapped[str | None] = mapped_column(String(64))


class SupportAccessGrant(CreatedAtMixin, Base):
    """A time-limited, reasoned permission for one admin to look at one shop's support view. Nothing else opens it."""

    __tablename__ = "support_access_grants"
    __table_args__ = (Index("ix_support_grants_shop_admin", "shop_id", "admin_id"), not_blank("reason"))

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    admin_id: Mapped[int] = mapped_column(ForeignKey("system_admins.id"))
    granted_by: Mapped[int] = mapped_column(ForeignKey("system_admins.id"))
    reason: Mapped[str] = mapped_column(String(300))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class SystemEvent(CreatedAtMixin, Base):
    __tablename__ = "system_events"
    __table_args__ = (
        Index("ix_system_events_category_created", "category", "created_at"),
        Index("ix_system_events_shop_created", "shop_id", "created_at"),
        not_blank("category"),
        not_blank("code"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int | None] = mapped_column(ForeignKey("shops.id"))
    category: Mapped[str] = mapped_column(
        String(30)
    )  # integration, ai, notification, backup, security, application
    severity: Mapped[EventSeverity] = mapped_column(enum_type(EventSeverity, "event_severity"))
    source: Mapped[str] = mapped_column(String(60))  # which part reported it, e.g. "price:open_prices"
    code: Mapped[str] = mapped_column(String(60))  # a short fixed word, e.g. "unavailable"
    message: Mapped[str] = mapped_column(String(300))  # a safe sentence
    request_id: Mapped[str | None] = mapped_column(String(64))


class BackupRecord(TimestampMixin, Base):
    """The index of backups. The file's own manifest (a small JSON next to it) is the authoritative copy of this."""

    __tablename__ = "backup_records"
    __table_args__ = (UniqueConstraint("backup_key"), non_negative("size_bytes"), not_blank("backup_key"))

    id: Mapped[int] = id_column()
    backup_key: Mapped[str] = mapped_column(String(60))
    kind: Mapped[BackupKind] = mapped_column(enum_type(BackupKind, "backup_kind"))
    status: Mapped[BackupStatus] = mapped_column(enum_type(BackupStatus, "backup_status"))
    storage_provider: Mapped[str] = mapped_column(String(30))
    filename: Mapped[str | None] = mapped_column(
        String(120)
    )  # relative to the storage; never an absolute path
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    schema_revision: Mapped[str | None] = mapped_column(String(30))
    initiated_by: Mapped[str] = mapped_column(String(120))
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(String(300))
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class RestoreRecord(CreatedAtMixin, Base):
    """A validation, rehearsal or restore attempt: who, which backup, when, the result and why it failed."""

    __tablename__ = "restore_records"

    id: Mapped[int] = id_column()
    backup_key: Mapped[str] = mapped_column(String(60))
    mode: Mapped[RestoreMode] = mapped_column(enum_type(RestoreMode, "restore_mode"))
    status: Mapped[str] = mapped_column(String(20))  # OK, FAILED, REFUSED
    initiated_by: Mapped[str] = mapped_column(String(120))
    detail: Mapped[str | None] = mapped_column(String(300))
    attempts: Mapped[int] = mapped_column(Integer, default=1)
