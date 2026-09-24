"""CRM foundation (Phase 14): notes on a customer, and customer groups used to target campaigns.

Not a second customer table — everything here points back to the one `customers` table. `CustomerNote` is
INSERT-ONLY, like `task_comments` and the audit log: a note is never edited or removed once written, so a
customer's timeline stays honest. `CustomerGroup` is either MANUAL (an explicit membership list a person
maintains) or RULE_BASED (a saved filter; `crm_segment_service` recomputes `CustomerGroupMember` rows on
demand — the table is a cache of the last computed membership, not a live view).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import UTCDateTime
from app.models.base import (
    Base,
    CreatedAtMixin,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    not_blank,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import CustomerGroupKind


class CustomerNote(CreatedAtMixin, Base):
    """A note on a customer's timeline. INSERT-ONLY: never edited or removed once written."""

    __tablename__ = "customer_notes"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("customer_id", "customers"),
        tenant_fk("user_id", "users"),
        Index("ix_customer_notes_shop_customer", "shop_id", "customer_id"),
        not_blank("body"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    customer_id: Mapped[int] = mapped_column(IdType)
    user_id: Mapped[int] = mapped_column(IdType)
    body: Mapped[str] = mapped_column(Text)


class CustomerGroup(TimestampMixin, Base):
    """A named group of customers, used to target campaigns. `rule` is a filter (the same shape
    `crm_segment_service.evaluate_rule` accepts) and is only set for RULE_BASED groups; NULL for MANUAL."""

    __tablename__ = "customer_groups"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "name"),
        tenant_fk("created_by", "users"),
        Index("ix_customer_groups_shop_kind", "shop_id", "kind"),
        not_blank("name"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[CustomerGroupKind] = mapped_column(enum_type(CustomerGroupKind, "customer_group_kind"))
    rule: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_recalculated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[int] = mapped_column(IdType)


class CustomerGroupMember(TimestampMixin, Base):
    """One customer's membership in one group. For a RULE_BASED group these rows are replaced wholesale each
    time the group is recalculated; for a MANUAL group they are the membership itself."""

    __tablename__ = "customer_group_members"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "group_id", "customer_id"),
        tenant_fk("group_id", "customer_groups"),
        tenant_fk("customer_id", "customers"),
        Index("ix_customer_group_members_shop_group", "shop_id", "group_id"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    group_id: Mapped[int] = mapped_column(IdType)
    customer_id: Mapped[int] = mapped_column(IdType)
