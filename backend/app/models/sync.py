"""Offline synchronisation (Phase 18): one row per operation a device queued while it had no connection.

The row is the idempotency record. `(shop, client_op_id)` is unique, and for a SYNCED operation the row is written in the SAME transaction
as the business change it caused, so the two exist together or not at all: sending the same operation again (a lost response, a second
device, a retry) finds the row and returns its stored answer instead of selling twice. CONFLICT and FAILED rows keep the payload so a
person can retry or discard them; nothing is ever dropped silently.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import UTCDateTime
from app.models.base import (
    Base,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    not_blank,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import SyncStatus


class SyncOperation(TimestampMixin, Base):
    __tablename__ = "sync_operations"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "client_op_id"),
        tenant_fk("user_id", "users"),
        Index("ix_sync_operations_shop_status", "shop_id", "status"),
        not_blank("client_op_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    client_op_id: Mapped[str] = mapped_column(String(64))
    device_id: Mapped[str | None] = mapped_column(String(64))
    op_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[SyncStatus] = mapped_column(enum_type(SyncStatus, "sync_status"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(60))
    message: Mapped[str | None] = mapped_column(String(300))
    client_created_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    attempts: Mapped[int] = mapped_column(default=1, server_default="1")
    user_id: Mapped[int] = mapped_column(IdType)
