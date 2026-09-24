"""A generic approval queue. Today the only thing in it is a stock count whose variance reached the shop's
configured threshold; see `approval_service` and `stock_count_service`."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.schemas.approvals import ApprovalListOut, ApprovalRequestOut, DecideIn
from app.services import approval_service, campaign_service, stock_count_service

router = APIRouter(prefix="/approvals", tags=["approvals"])
ReadSession = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=ApprovalListOut)
def list_pending(ctx: Ctx, session: ReadSession, limit: Limit = 50) -> ApprovalListOut:
    rows = approval_service.list_pending(session, ctx.shop_id, limit=limit)
    return ApprovalListOut(
        items=[ApprovalRequestOut.of(r) for r in rows], total=len(rows), limit=limit, offset=0
    )


@router.get("/{request_id}", response_model=ApprovalRequestOut)
def get_request(request_id: int, ctx: Ctx, session: ReadSession) -> ApprovalRequestOut:
    return ApprovalRequestOut.of(approval_service.get(session, ctx.shop_id, request_id))


@router.post("/{request_id}/decide", response_model=ApprovalRequestOut)
def decide(request_id: int, payload: DecideIn, ctx: Ctx) -> ApprovalRequestOut:
    with write_transaction() as session:
        decided = approval_service.decide(
            session, ctx, request_id, approve=payload.approve, note=payload.note
        )
        # Each kind adds its own branch here. A loyalty adjustment needs none: the requester redeems the
        # approved request by id (see `loyalty_service.adjust_with_controls`).
        if decided.request.kind == stock_count_service.APPROVAL_KIND:
            stock_count_service.apply_approval_decision(
                session, ctx, decided.request.entity_id, approved=decided.approved
            )
        elif decided.request.kind == campaign_service.APPROVAL_KIND:
            campaign_service.apply_approval_decision(
                session, ctx, decided.request.entity_id, approved=decided.approved
            )
        return ApprovalRequestOut.of(decided.request)
