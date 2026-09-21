"""Promotion endpoints (offers, discounts, coupons).

Reading is open to any signed-in user of the shop; creating, changing and switching promotions on or off is
the owner's, because it changes what customers pay. There is deliberately no DELETE: an offer is paused or
expired, so every sale that used it keeps its history. An offer is applied by the billing endpoints
(`/sales/calculate` and `/sales/{id}/post`), never from here.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx, OwnerCtx
from app.api.idempotency import IdempotencyHeader, run_idempotent
from app.db.session import get_session, write_transaction
from app.models.enums import PromotionStatus, PromotionType
from app.schemas.promotion import (
    PromotionCreate,
    PromotionListOut,
    PromotionOut,
    PromotionUpdate,
    UsageListOut,
    UsageOut,
)
from app.services import promotion_service

router = APIRouter(prefix="/promotions", tags=["promotions"])
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("", response_model=PromotionListOut)
def list_promotions(
    ctx: Ctx,
    session: ReadSession,
    q: Annotated[str | None, Query(max_length=100, description="Search the name or coupon code")] = None,
    status: Annotated[list[PromotionStatus] | None, Query(description="Repeat to allow several")] = None,
    promo_type: PromotionType | None = None,
    coupon_only: Annotated[
        bool | None, Query(description="true: coupons only; false: automatic only")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PromotionListOut:
    views, total = promotion_service.list_promotions(
        session,
        ctx.shop_id,
        q=q,
        statuses=status,
        promo_type=promo_type,
        coupon_only=coupon_only,
        limit=limit,
        offset=offset,
    )
    return PromotionListOut(
        items=[PromotionOut.from_view(v) for v in views], total=total, limit=limit, offset=offset
    )


@router.post("", response_model=PromotionOut, status_code=201)
def create_promotion(
    payload: PromotionCreate, ctx: OwnerCtx, idempotency_key: IdempotencyHeader = None
) -> PromotionOut:
    """Create an offer as a draft. It applies to nothing until it is activated."""
    values = payload.model_dump(exclude_none=True)
    return run_idempotent(
        ctx,
        idempotency_key,
        "promotion.create",
        payload.model_dump(mode="json"),
        lambda session: PromotionOut.from_view(promotion_service.create_promotion(session, ctx, values)),
        status_code=201,
    )


@router.get("/usage", response_model=UsageListOut)
def usage(
    ctx: Ctx,
    session: ReadSession,
    promotion_id: int | None = None,
    coupon_only: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
) -> UsageListOut:
    """Every use of an offer on a sale (from the frozen snapshots), newest first."""
    rows = promotion_service.list_usage(
        session,
        ctx.shop_id,
        promotion_id=promotion_id,
        coupon_only=coupon_only,
        date_from=date_from,
        date_to=date_to,
    )
    return UsageListOut(items=[UsageOut.from_row(r) for r in rows])


@router.get("/{promotion_id}", response_model=PromotionOut)
def get_promotion(promotion_id: int, ctx: Ctx, session: ReadSession) -> PromotionOut:
    return PromotionOut.from_view(promotion_service.get_view(session, ctx.shop_id, promotion_id))


@router.patch("/{promotion_id}", response_model=PromotionOut)
def update_promotion(promotion_id: int, payload: PromotionUpdate, ctx: OwnerCtx) -> PromotionOut:
    """Change an offer. Its kind and scope are fixed. Sales that already used it are not affected."""
    with write_transaction() as session:
        changes = payload.model_dump(exclude_unset=True)
        return PromotionOut.from_view(promotion_service.update_promotion(session, ctx, promotion_id, changes))


@router.post("/{promotion_id}/activate", response_model=PromotionOut)
def activate(promotion_id: int, ctx: OwnerCtx) -> PromotionOut:
    with write_transaction() as session:
        return PromotionOut.from_view(
            promotion_service.set_status(session, ctx, promotion_id, PromotionStatus.ACTIVE)
        )


@router.post("/{promotion_id}/pause", response_model=PromotionOut)
def pause(promotion_id: int, ctx: OwnerCtx) -> PromotionOut:
    with write_transaction() as session:
        return PromotionOut.from_view(
            promotion_service.set_status(session, ctx, promotion_id, PromotionStatus.PAUSED)
        )


@router.post("/{promotion_id}/expire", response_model=PromotionOut)
def expire(promotion_id: int, ctx: OwnerCtx) -> PromotionOut:
    """End an offer for good. It cannot be reactivated; make a new one."""
    with write_transaction() as session:
        return PromotionOut.from_view(
            promotion_service.set_status(session, ctx, promotion_id, PromotionStatus.EXPIRED)
        )
