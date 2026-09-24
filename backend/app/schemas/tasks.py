from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import BusinessTask, TaskComment
from app.models.enums import TaskPriority, TaskStatus
from app.schemas.common import Page

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class TaskCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    description: str | None = Field(default=None, max_length=2000)
    kind: str | None = Field(default=None, max_length=40)
    priority: TaskPriority = TaskPriority.MEDIUM
    assigned_to: int | None = None
    due_date: date | None = None
    entity_type: str | None = Field(default=None, max_length=40)
    entity_id: int | None = None


class TaskUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: str | None = Field(default=None, max_length=2000)
    priority: TaskPriority | None = None
    due_date: date | None = None


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assigned_to: int | None = None


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class CommentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class TaskOut(BaseModel):
    id: int
    title: str
    description: str | None
    kind: str | None
    status: TaskStatus
    priority: TaskPriority
    assigned_to: int | None
    due_date: date | None
    entity_type: str | None
    entity_id: int | None
    created_by: int
    completed_by: int | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, t: BusinessTask) -> "TaskOut":
        return cls(
            id=t.id, title=t.title, description=t.description, kind=t.kind, status=t.status,
            priority=t.priority, assigned_to=t.assigned_to, due_date=t.due_date,
            entity_type=t.entity_type, entity_id=t.entity_id,
            created_by=t.created_by, completed_by=t.completed_by, completed_at=t.completed_at,
            cancelled_at=t.cancelled_at, created_at=t.created_at,
        )  # fmt: skip


class TaskListOut(Page):
    items: list[TaskOut]


class TaskCommentOut(BaseModel):
    id: int
    task_id: int
    user_id: int
    body: str
    created_at: datetime

    @classmethod
    def of(cls, c: TaskComment) -> "TaskCommentOut":
        return cls(id=c.id, task_id=c.task_id, user_id=c.user_id, body=c.body, created_at=c.created_at)
