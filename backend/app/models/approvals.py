"""A generic approval request for a sensitive operational action that a shop has chosen to gate behind a
second look.

Not tied to one feature: `kind` names what is being approved (today only `STOCK_COUNT_LARGE_VARIANCE`,
when a shop has set `shops.stock_count_variance_threshold` and a count's variance value reaches it),
`entity_type`/`entity_id` point at the record. Every decision is also written to the shop's audit log
(`audit_service`); this table is the queue of what is still waiting, not the only record that it happened."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import Money, UTCDateTime
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
from app.models.enums import ApprovalStatus


class ApprovalRequest(TimestampMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("requested_by", "users"),
        tenant_fk("decided_by", "users"),
        Index("ix_approval_requests_shop_status", "shop_id", "status"),
        Index("ix_approval_requests_shop_entity", "shop_id", "entity_type", "entity_id"),
        not_blank("kind"),
        not_blank("entity_type"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    kind: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int] = mapped_column(IdType)
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus, "approval_status"),
        default=ApprovalStatus.PENDING,
        server_default=ApprovalStatus.PENDING.value,
    )
    reason: Mapped[str] = mapped_column(Text)
    threshold_value: Mapped[Decimal | None] = mapped_column(
        Money
    )  # the configured threshold at the time, if money-based
    observed_value: Mapped[Decimal | None] = mapped_column(
        Money
    )  # what was actually seen, for the same reason
    requested_by: Mapped[int] = mapped_column(IdType)
    decided_by: Mapped[int | None] = mapped_column(IdType)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decision_note: Mapped[str | None] = mapped_column(Text)
