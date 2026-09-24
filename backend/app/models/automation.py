"""Marketing automation (Phase 14): event/rule-based triggers with an explicit condition, action, cooldown and
execution history. Nothing here sends a financial reward or a message on its own — `CREATE_CAMPAIGN_DRAFT`
creates a DRAFT campaign for a person to review and launch, `CREATE_TASK` creates an ordinary `BusinessTask`,
and `NOTIFY` raises an ordinary in-app notification. Idempotency is enforced by checking `AutomationRun` for a
recent run before acting, not by a unique constraint (a rule may legitimately fire again after its cooldown).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Index, Integer, String, UniqueConstraint
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
from app.models.enums import AutomationAction, AutomationRunStatus, AutomationTrigger


class AutomationRule(TimestampMixin, Base):
    __tablename__ = "automation_rules"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("created_by", "users"),
        Index("ix_automation_rules_shop_active", "shop_id", "is_active"),
        not_blank("name"),
        non_negative("cooldown_days"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    name: Mapped[str] = mapped_column(String(150))
    trigger_type: Mapped[AutomationTrigger] = mapped_column(
        enum_type(AutomationTrigger, "automation_trigger")
    )
    conditions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")
    action_type: Mapped[AutomationAction] = mapped_column(enum_type(AutomationAction, "automation_action"))
    action_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")
    cooldown_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_by: Mapped[int] = mapped_column(IdType)


class AutomationRun(CreatedAtMixin, Base):
    """One execution attempt. INSERT-ONLY execution history, so a rule's audit trail can never be edited."""

    __tablename__ = "automation_runs"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("rule_id", "automation_rules"),
        tenant_fk("customer_id", "customers"),
        Index("ix_automation_runs_shop_rule_customer", "shop_id", "rule_id", "customer_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    rule_id: Mapped[int] = mapped_column(IdType)
    customer_id: Mapped[int | None] = mapped_column(IdType)  # NULL for a shop-wide trigger, not one customer
    status: Mapped[AutomationRunStatus] = mapped_column(
        enum_type(AutomationRunStatus, "automation_run_status")
    )
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")
    ran_at: Mapped[datetime] = mapped_column(UTCDateTime)
