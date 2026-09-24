"""Business tasks: a generic operational to-do that other parts of the system (or a person) can raise, someone
can be assigned, and someone marks done. Not a second notification system: a notification is a one-way "this
happened"; a task is tracked until it is closed, and carries comments and an audit trail."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import BusinessTask, TaskComment, User
from app.models.enums import TaskPriority, TaskStatus
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

OPEN_STATUSES = (TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.WAITING)


@dataclass(frozen=True)
class TaskCounts:
    open: int
    overdue: int


def _get(session: Session, shop_id: int, task_id: int, *, lock: bool = False) -> BusinessTask:
    query = select(BusinessTask).where(BusinessTask.id == task_id, BusinessTask.shop_id == shop_id)
    task = session.scalar(query.with_for_update() if lock else query)
    if task is None:
        raise NotFoundError("Task not found")
    return task


def get(session: Session, shop_id: int, task_id: int) -> BusinessTask:
    return _get(session, shop_id, task_id)


def create(
    session: Session,
    ctx: RequestContext,
    *,
    title: str,
    description: str | None = None,
    kind: str | None = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
    assigned_to: int | None = None,
    due_date: date | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
) -> BusinessTask:
    if not title.strip():
        raise InvalidInputError("Give the task a title.", field="title")
    if (
        assigned_to is not None
        and session.scalar(select(User.id).where(User.id == assigned_to, User.shop_id == ctx.shop_id)) is None
    ):
        raise InvalidInputError("Choose a member of this shop to assign the task to.", field="assigned_to")
    task = BusinessTask(
        shop_id=ctx.shop_id, title=title.strip(), description=(description or "").strip() or None,
        kind=kind, priority=priority, assigned_to=assigned_to, due_date=due_date,
        entity_type=entity_type, entity_id=entity_id, created_by=ctx.user_id,
    )  # fmt: skip
    session.add(task)
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="task",
        entity_id=task.id,
        action="task_created",
        after={"title": task.title, "kind": kind, "assigned_to": assigned_to},
    )
    return task


def list_tasks(
    session: Session, shop_id: int, *, status: TaskStatus | None = None, assigned_to: int | None = None,
    entity_type: str | None = None, entity_id: int | None = None, limit: int = 50, offset: int = 0,
) -> tuple[list[BusinessTask], int]:  # fmt: skip
    conditions = [BusinessTask.shop_id == shop_id]
    if status is not None:
        conditions.append(BusinessTask.status == status)
    if assigned_to is not None:
        conditions.append(BusinessTask.assigned_to == assigned_to)
    if entity_type is not None:
        conditions.append(BusinessTask.entity_type == entity_type)
    if entity_id is not None:
        conditions.append(BusinessTask.entity_id == entity_id)
    total = session.scalar(select(func.count()).select_from(BusinessTask).where(*conditions)) or 0
    rows = list(
        session.scalars(
            select(BusinessTask)
            .where(*conditions)
            .order_by(
                BusinessTask.status,
                BusinessTask.due_date.is_(None),
                BusinessTask.due_date,
                BusinessTask.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def counts_for(session: Session, shop_id: int, user_id: int, today: date) -> TaskCounts:
    open_count = (
        session.scalar(
            select(func.count())
            .select_from(BusinessTask)
            .where(
                BusinessTask.shop_id == shop_id,
                BusinessTask.assigned_to == user_id,
                BusinessTask.status.in_(OPEN_STATUSES),
            )
        )
        or 0
    )
    overdue = (
        session.scalar(
            select(func.count())
            .select_from(BusinessTask)
            .where(
                BusinessTask.shop_id == shop_id,
                BusinessTask.assigned_to == user_id,
                BusinessTask.status.in_(OPEN_STATUSES),
                BusinessTask.due_date.isnot(None),
                BusinessTask.due_date < today,
            )
        )
        or 0
    )
    return TaskCounts(open_count, overdue)


def update(session: Session, ctx: RequestContext, task_id: int, **changes: object) -> BusinessTask:
    """Change title/description/priority/due_date/kind. Status changes go through their own dedicated
    functions, which is where the workflow rules (and the audit wording) belong."""
    task = _get(session, ctx.shop_id, task_id, lock=True)
    if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        raise ConflictError("This task is closed; reopen it first.")
    before = {
        "title": task.title,
        "priority": task.priority.value,
        "due_date": task.due_date.isoformat() if task.due_date else None,
    }
    if "title" in changes:
        title = str(changes["title"]).strip()
        if not title:
            raise InvalidInputError("Give the task a title.", field="title")
        task.title = title
    if "description" in changes:
        task.description = (str(changes["description"] or "")).strip() or None
    if "priority" in changes and changes["priority"] is not None:
        task.priority = TaskPriority(changes["priority"])
    if "due_date" in changes:
        task.due_date = changes["due_date"]  # type: ignore[assignment]
    record_audit(
        session,
        ctx,
        entity_type="task",
        entity_id=task.id,
        action="task_updated",
        before=before,
        after={"title": task.title},
    )
    return task


def assign(session: Session, ctx: RequestContext, task_id: int, assigned_to: int | None) -> BusinessTask:
    task = _get(session, ctx.shop_id, task_id, lock=True)
    if (
        assigned_to is not None
        and session.scalar(select(User.id).where(User.id == assigned_to, User.shop_id == ctx.shop_id)) is None
    ):
        raise InvalidInputError("Choose a member of this shop to assign the task to.", field="assigned_to")
    before = task.assigned_to
    task.assigned_to = assigned_to
    record_audit(
        session,
        ctx,
        entity_type="task",
        entity_id=task.id,
        action="task_assigned",
        before={"assigned_to": before},
        after={"assigned_to": assigned_to},
    )
    return task


def complete(session: Session, ctx: RequestContext, task_id: int) -> BusinessTask:
    task = _get(session, ctx.shop_id, task_id, lock=True)
    if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        raise ConflictError("This task is already closed.")
    task.status = TaskStatus.COMPLETED
    task.completed_by = ctx.user_id
    task.completed_at = utc_now()
    record_audit(session, ctx, entity_type="task", entity_id=task.id, action="task_completed")
    return task


def reopen(session: Session, ctx: RequestContext, task_id: int) -> BusinessTask:
    task = _get(session, ctx.shop_id, task_id, lock=True)
    if task.status != TaskStatus.COMPLETED:
        raise ConflictError("Only a completed task can be reopened.")
    task.status = TaskStatus.OPEN
    task.completed_by = None
    task.completed_at = None
    record_audit(session, ctx, entity_type="task", entity_id=task.id, action="task_reopened")
    return task


def cancel(session: Session, ctx: RequestContext, task_id: int, *, reason: str | None = None) -> BusinessTask:
    task = _get(session, ctx.shop_id, task_id, lock=True)
    if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        raise ConflictError("This task is already closed.")
    task.status = TaskStatus.CANCELLED
    task.cancelled_by = ctx.user_id
    task.cancelled_at = utc_now()
    record_audit(
        session, ctx, entity_type="task", entity_id=task.id, action="task_cancelled", after={"reason": reason}
    )
    return task


def set_in_progress(session: Session, ctx: RequestContext, task_id: int) -> BusinessTask:
    task = _get(session, ctx.shop_id, task_id, lock=True)
    if task.status not in (TaskStatus.OPEN, TaskStatus.WAITING):
        raise ConflictError("This task cannot move to in progress from here.")
    task.status = TaskStatus.IN_PROGRESS
    record_audit(session, ctx, entity_type="task", entity_id=task.id, action="task_in_progress")
    return task


def add_comment(session: Session, ctx: RequestContext, task_id: int, body: str) -> TaskComment:
    if not body.strip():
        raise InvalidInputError("Write something first.", field="body")
    task = _get(session, ctx.shop_id, task_id)
    comment = TaskComment(shop_id=ctx.shop_id, task_id=task.id, user_id=ctx.user_id, body=body.strip())
    session.add(comment)
    session.flush()
    record_audit(session, ctx, entity_type="task", entity_id=task.id, action="task_commented")
    return comment


def list_comments(session: Session, shop_id: int, task_id: int) -> list[TaskComment]:
    _get(session, shop_id, task_id)
    return list(
        session.scalars(
            select(TaskComment)
            .where(TaskComment.shop_id == shop_id, TaskComment.task_id == task_id)
            .order_by(TaskComment.id)
        )
    )
