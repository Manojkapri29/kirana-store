"""AI assistant tables: usage (cost control, plan limits) and actions (proposed by AI, confirmed by a person).

Neither table stores a question, a prompt, an answer or a document's text: only structured facts (which
feature, which
provider, how many tokens, what was proposed). That keeps sensitive words out of the database while still
making every
AI-assisted change auditable.

* `ai_usage`    one row per request that reached the AI layer, with the provider's token counts when it gave
them.
* `ai_actions`  a business change the AI *prepared*. Nothing has happened until a person confirms it;
confirming runs
                an existing service (purchase, inventory or promotion), never SQL from the AI.
                `proposal` is what was
                first proposed and never changes; `current` is what would be done now (a
                person may have edited it).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, CheckConstraint, Index, Integer, String, UniqueConstraint
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
    shop_id_column,
    tenant_fk,
)
from app.models.enums import AiActionKind, AiActionStatus


class AiUsage(CreatedAtMixin, Base):
    __tablename__ = "ai_usage"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        Index("ix_ai_usage_shop_created", "shop_id", "created_at"),
        tenant_fk("user_id", "users"),
        not_blank("feature"),
        non_negative("input_tokens"),
        non_negative("output_tokens"),
        non_negative("estimated_cost_micros"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    user_id: Mapped[int] = mapped_column(IdType)
    feature: Mapped[str] = mapped_column(String(40))  # ask, insights, reorder, invoice_photo, ...
    provider: Mapped[str | None] = mapped_column(String(30))  # None: answered without any provider
    model: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))  # OK, FAILED, UNSUPPORTED
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    # Only when the provider reported a cost (millionths of `cost_currency`); never estimated by us.
    estimated_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    cost_currency: Mapped[str | None] = mapped_column(String(3))


class AiAction(TimestampMixin, Base):
    __tablename__ = "ai_actions"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        Index("ix_ai_actions_shop_status", "shop_id", "status"),
        tenant_fk("created_by", "users"),
        not_blank("feature"),
        non_negative("attempts"),
        # A result exists only for an executed action.
        CheckConstraint("status = 'EXECUTED' OR result_type IS NULL", name="result_only_when_executed"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    created_by: Mapped[int] = mapped_column(IdType)
    kind: Mapped[AiActionKind] = mapped_column(enum_type(AiActionKind, "kind"))
    status: Mapped[AiActionStatus] = mapped_column(
        enum_type(AiActionStatus, "status"), default=AiActionStatus.PROPOSED
    )
    feature: Mapped[str] = mapped_column(String(40))  # which AI feature proposed it
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON)  # as first proposed: never edited
    current: Mapped[dict[str, Any]] = mapped_column(JSON)  # what a confirmation would do now
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result_type: Mapped[str | None] = mapped_column(String(30))  # purchase, promotion, inventory_adjustment
    result_ids: Mapped[list[int] | None] = mapped_column(JSON)
    failure_message: Mapped[str | None] = mapped_column(String(300))
    reference_id: Mapped[str | None] = mapped_column(String(30))  # the error reference of a failed attempt
    decided_by: Mapped[int | None] = mapped_column(IdType)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
