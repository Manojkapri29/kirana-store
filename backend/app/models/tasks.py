"""Business tasks: a generic operational to-do, and its comments. Not a second notification system — a
notification says something happened; a task is something a person is expected to act on, can be assigned, and
can be marked done.

`kind` is a short free-text code (like a notification `event_type`), not a fixed enum: `LOW_STOCK_REVIEW`,
`STOCK_COUNT_VARIANCE`, `REORDER_REVIEW`, `APPROVAL_REQUEST`, or a plain manual task with no kind at all.
Adding a new originator of tasks is then a data change, not a migration."""

from datetime import date, datetime

from sqlalchemy import Date, Index, String, Text, UniqueConstraint
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
from app.models.enums import TaskPriority, TaskStatus


class BusinessTask(TimestampMixin, Base):
    __tablename__ = "business_tasks"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("assigned_to", "users"),
        tenant_fk("created_by", "users"),
        tenant_fk("completed_by", "users"),
        tenant_fk("cancelled_by", "users"),
        Index("ix_business_tasks_shop_status", "shop_id", "status"),
        Index("ix_business_tasks_shop_assigned", "shop_id", "assigned_to", "status"),
        Index("ix_business_tasks_shop_entity", "shop_id", "entity_type", "entity_id"),
        not_blank("title"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[TaskStatus] = mapped_column(
        enum_type(TaskStatus, "task_status"), default=TaskStatus.OPEN, server_default=TaskStatus.OPEN.value
    )
    priority: Mapped[TaskPriority] = mapped_column(
        enum_type(TaskPriority, "task_priority"),
        default=TaskPriority.MEDIUM,
        server_default=TaskPriority.MEDIUM.value,
    )
    assigned_to: Mapped[int | None] = mapped_column(IdType)
    due_date: Mapped[date | None] = mapped_column(Date)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(IdType)
    created_by: Mapped[int] = mapped_column(IdType)
    completed_by: Mapped[int | None] = mapped_column(IdType)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancelled_by: Mapped[int | None] = mapped_column(IdType)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class TaskComment(CreatedAtMixin, Base):
    """A note on a task. INSERT-ONLY, like the audit log: a comment is never edited or removed once
    written."""

    __tablename__ = "task_comments"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        tenant_fk("task_id", "business_tasks"),
        tenant_fk("user_id", "users"),
        Index("ix_task_comments_shop_task", "shop_id", "task_id"),
        not_blank("body"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    task_id: Mapped[int] = mapped_column(IdType)
    user_id: Mapped[int] = mapped_column(IdType)
    body: Mapped[str] = mapped_column(Text)
