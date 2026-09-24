"""A generic approval queue shared by stock counts, campaign launches, loyalty adjustments and finance
(expenses, adjustments, period reopening). See `approval_service`.

Who may see or decide a request depends on its KIND: the permission that governs the thing being approved.
The route itself only needs a signed-in member: one static permission could not fit every kind (a stock
counter must not approve an expense, an accountant must not approve a stock count)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.schemas.approvals import ApprovalListOut, ApprovalRequestOut, DecideIn
from app.services import (
    approval_service,
    authorization_service,
    campaign_service,
    expense_service,
    finance_ledger_service,
    finance_period_service,
    loyalty_service,
    stock_count_service,
)

router = APIRouter(prefix="/approvals", tags=["approvals"])
ReadSession = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=100)]


# The permission that decides each kind of request. An unknown kind falls back to the original generic one.
KIND_PERMISSION = {
    stock_count_service.APPROVAL_KIND: "STOCK_COUNT_APPROVE",
    campaign_service.APPROVAL_KIND: "CAMPAIGN_LAUNCH",
    loyalty_service.APPROVAL_KIND: "LOYALTY_MANAGE",
    expense_service.APPROVAL_KIND: "FINANCE_EXPENSE_APPROVE",
    finance_ledger_service.ADJUSTMENT_APPROVAL_KIND: "FINANCE_ADJUSTMENT_MANAGE",
    finance_period_service.REOPEN_APPROVAL_KIND: "FINANCE_PERIOD_UNLOCK",
}
FALLBACK_PERMISSION = "STOCK_COUNT_APPROVE"


def _needed(kind: str) -> str:
    return KIND_PERMISSION.get(kind, FALLBACK_PERMISSION)


@router.get("", response_model=ApprovalListOut)
def list_pending(ctx: Ctx, session: ReadSession, limit: Limit = 50) -> ApprovalListOut:
    rows = [
        r for r in approval_service.list_pending(session, ctx.shop_id, limit=500) if ctx.has(_needed(r.kind))
    ][:limit]
    return ApprovalListOut(
        items=[ApprovalRequestOut.of(r) for r in rows], total=len(rows), limit=limit, offset=0
    )


@router.get("/{request_id}", response_model=ApprovalRequestOut)
def get_request(request_id: int, ctx: Ctx, session: ReadSession) -> ApprovalRequestOut:
    request = approval_service.get(session, ctx.shop_id, request_id)
    if not ctx.has(_needed(request.kind)):
        authorization_service.require(ctx, _needed(request.kind))
    return ApprovalRequestOut.of(request)


@router.post("/{request_id}/decide", response_model=ApprovalRequestOut)
def decide(request_id: int, payload: DecideIn, ctx: Ctx) -> ApprovalRequestOut:
    with write_transaction() as session:
        kind = approval_service.get(session, ctx.shop_id, request_id).kind
        authorization_service.require(ctx, _needed(kind))
        decided = approval_service.decide(
            session, ctx, request_id, approve=payload.approve, note=payload.note
        )
        # Each kind adds its own branch here. A loyalty adjustment needs none: the requester redeems the
        # approved request by id (see `loyalty_service.adjust_with_controls`).
        if decided.request.kind == stock_count_service.APPROVAL_KIND:
            stock_count_service.apply_approval_decision(
                session, ctx, decided.request.entity_id, approved=decided.approved
            )
        elif decided.request.kind == expense_service.APPROVAL_KIND:
            expense_service.apply_approval_decision(
                session, ctx, decided.request.entity_id, approved=decided.approved, note=payload.note
            )
        elif decided.request.kind == campaign_service.APPROVAL_KIND:
            campaign_service.apply_approval_decision(
                session, ctx, decided.request.entity_id, approved=decided.approved
            )
        return ApprovalRequestOut.of(decided.request)
