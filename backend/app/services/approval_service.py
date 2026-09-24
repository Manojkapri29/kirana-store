"""A generic queue of pending approvals for a sensitive operational action a shop has chosen to gate behind a
second look. Today the only thing that lands here is a stock count whose variance reaches the shop's
configured threshold (`stock_count_service`); the table and this service are written so another kind of action
could reuse the same queue later, by giving it a `kind` and calling `create`/`decide` — nothing here is
specific to stock counts.

Every decision is also written to the shop's audit log (`audit_service`), so the queue itself can be trimmed
or rebuilt without losing the history of who decided what."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import ApprovalRequest
from app.models.enums import ApprovalStatus
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError


@dataclass(frozen=True)
class Decided:
    request: ApprovalRequest
    approved: bool


def create(
    session: Session,
    ctx: RequestContext,
    *,
    kind: str,
    entity_type: str,
    entity_id: int,
    reason: str,
    threshold_value: Decimal | None = None,
    observed_value: Decimal | None = None,
) -> ApprovalRequest:
    request = ApprovalRequest(
        shop_id=ctx.shop_id, kind=kind, entity_type=entity_type, entity_id=entity_id, reason=reason,
        threshold_value=threshold_value, observed_value=observed_value, requested_by=ctx.user_id,
    )  # fmt: skip
    session.add(request)
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="approval_request",
        entity_id=request.id,
        action="approval_requested",
        after={"kind": kind, "reason": reason},
    )
    return request


def get(session: Session, shop_id: int, request_id: int) -> ApprovalRequest:
    request = session.scalar(
        select(ApprovalRequest).where(ApprovalRequest.id == request_id, ApprovalRequest.shop_id == shop_id)
    )
    if request is None:
        raise NotFoundError("Approval request not found")
    return request


def list_pending(session: Session, shop_id: int, *, limit: int = 100) -> list[ApprovalRequest]:
    return list(
        session.scalars(
            select(ApprovalRequest)
            .where(ApprovalRequest.shop_id == shop_id, ApprovalRequest.status == ApprovalStatus.PENDING)
            .order_by(ApprovalRequest.created_at)
            .limit(limit)
        )
    )


def decide(
    session: Session, ctx: RequestContext, request_id: int, *, approve: bool, note: str | None = None
) -> Decided:
    """Approve or reject. The decider may never be the person who asked for it (no self-approval)."""
    request = session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == request_id, ApprovalRequest.shop_id == ctx.shop_id)
        .with_for_update()
    )
    if request is None:
        raise NotFoundError("Approval request not found")
    if request.status is not ApprovalStatus.PENDING:
        raise ConflictError("This request has already been decided.")
    if request.requested_by == ctx.user_id:
        raise ForbiddenError("You cannot decide a request you made yourself.", code="cannot_self_approve")
    if not approve and not (note and note.strip()):
        raise InvalidInputError("Give a reason for rejecting this.", field="note")
    request.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    request.decided_by = ctx.user_id
    request.decided_at = utc_now()
    request.decision_note = note.strip() if note else None
    record_audit(
        session, ctx, entity_type="approval_request", entity_id=request.id,
        action="approval_decided", after={"status": request.status.value, "note": request.decision_note},
    )  # fmt: skip
    return Decided(request, approve)
