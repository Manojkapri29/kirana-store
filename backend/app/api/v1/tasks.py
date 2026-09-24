"""Business tasks: a generic operational to-do. See `task_service` for the rules."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.models.enums import TaskStatus
from app.schemas.tasks import (
    AssignIn,
    CancelIn,
    CommentIn,
    TaskCommentOut,
    TaskCreateIn,
    TaskListOut,
    TaskOut,
    TaskUpdateIn,
)
from app.services import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])
ReadSession = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("", response_model=TaskListOut)
def list_tasks(
    ctx: Ctx, session: ReadSession, status: TaskStatus | None = None, assigned_to: int | None = None,
    entity_type: str | None = None, entity_id: int | None = None, limit: Limit = 50, offset: Offset = 0,
) -> TaskListOut:  # fmt: skip
    rows, total = task_service.list_tasks(
        session,
        ctx.shop_id,
        status=status,
        assigned_to=assigned_to,
        entity_type=entity_type,
        entity_id=entity_id,
        limit=limit,
        offset=offset,
    )
    return TaskListOut(items=[TaskOut.of(t) for t in rows], total=total, limit=limit, offset=offset)


@router.post("", response_model=TaskOut, status_code=201)
def create_task(payload: TaskCreateIn, ctx: Ctx) -> TaskOut:
    with write_transaction() as session:
        task = task_service.create(
            session, ctx, title=payload.title, description=payload.description, kind=payload.kind,
            priority=payload.priority, assigned_to=payload.assigned_to, due_date=payload.due_date,
            entity_type=payload.entity_type, entity_id=payload.entity_id,
        )  # fmt: skip
        return TaskOut.of(task)


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, ctx: Ctx, session: ReadSession) -> TaskOut:
    return TaskOut.of(task_service.get(session, ctx.shop_id, task_id))


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskUpdateIn, ctx: Ctx) -> TaskOut:
    with write_transaction() as session:
        changes = payload.model_dump(exclude_unset=True)
        return TaskOut.of(task_service.update(session, ctx, task_id, **changes))


@router.post("/{task_id}/assign", response_model=TaskOut)
def assign_task(task_id: int, payload: AssignIn, ctx: Ctx) -> TaskOut:
    with write_transaction() as session:
        return TaskOut.of(task_service.assign(session, ctx, task_id, payload.assigned_to))


@router.post("/{task_id}/complete", response_model=TaskOut)
def complete_task(task_id: int, ctx: Ctx) -> TaskOut:
    with write_transaction() as session:
        return TaskOut.of(task_service.complete(session, ctx, task_id))


@router.post("/{task_id}/reopen", response_model=TaskOut)
def reopen_task(task_id: int, ctx: Ctx) -> TaskOut:
    with write_transaction() as session:
        return TaskOut.of(task_service.reopen(session, ctx, task_id))


@router.post("/{task_id}/cancel", response_model=TaskOut)
def cancel_task(task_id: int, payload: CancelIn, ctx: Ctx) -> TaskOut:
    with write_transaction() as session:
        return TaskOut.of(task_service.cancel(session, ctx, task_id, reason=payload.reason))


@router.get("/{task_id}/comments", response_model=list[TaskCommentOut])
def list_comments(task_id: int, ctx: Ctx, session: ReadSession) -> list[TaskCommentOut]:
    return [TaskCommentOut.of(c) for c in task_service.list_comments(session, ctx.shop_id, task_id)]


@router.post("/{task_id}/comments", response_model=TaskCommentOut, status_code=201)
def add_comment(task_id: int, payload: CommentIn, ctx: Ctx) -> TaskCommentOut:
    with write_transaction() as session:
        return TaskCommentOut.of(task_service.add_comment(session, ctx, task_id, payload.body))
