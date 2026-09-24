"""Staff side of the online store: settings, which products are listed, and the order workflow. There is no DELETE: an order is moved to
REJECTED or CANCELLED, never removed."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.models.enums import OnlineOrderStatus
from app.schemas.online_store import (
    AcceptIn,
    AdvanceIn,
    ListingIn,
    ListingOut,
    ListingPage,
    OrderDetailOut,
    OrderPage,
    OrderRowOut,
    OrdersSummaryOut,
    ReasonIn,
    StoreSettingsIn,
    StoreSettingsOut,
    StoreSettingsResult,
)
from app.services import authorization_service
from app.services import online_store_service as svc

router = APIRouter(tags=["online store"])
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("/store/settings", response_model=StoreSettingsResult)
def get_settings(ctx: Ctx, session: ReadSession) -> StoreSettingsResult:
    row = svc.get_store(session, ctx.shop_id)
    return StoreSettingsResult(store=None if row is None else StoreSettingsOut.from_row(row))


@router.put("/store/settings", response_model=StoreSettingsOut)
def save_settings(payload: StoreSettingsIn, ctx: Ctx) -> StoreSettingsOut:
    with write_transaction() as session:
        sent = payload.model_dump(exclude_unset=True)
        values = {k: v for k, v in sent.items() if v is not None or k in ("contact_phone", "announcement")}  # null clears only the optional texts
        return StoreSettingsOut.from_row(svc.save_store(session, ctx, values))


@router.get("/store/listings", response_model=ListingPage)
def listings(ctx: Ctx, session: ReadSession, q: Annotated[str | None, Query(max_length=60)] = None, only_visible: bool = False, limit: Annotated[int, Query(ge=1, le=200)] = 50, offset: Annotated[int, Query(ge=0)] = 0) -> ListingPage:
    rows, total = svc.list_listings(session, ctx.shop_id, q=q, only_visible=only_visible, limit=limit, offset=offset)
    return ListingPage(items=[ListingOut.from_row(r) for r in rows], total=total, limit=limit, offset=offset)


@router.put("/store/listings/{product_id}", response_model=ListingOut)
def set_listing(product_id: int, payload: ListingIn, ctx: Ctx) -> ListingOut:
    with write_transaction() as session:
        return ListingOut.from_row(svc.set_listing(session, ctx, product_id, payload.visible))


@router.get("/online-orders/summary", response_model=OrdersSummaryOut)
def orders_summary(ctx: Ctx, session: ReadSession) -> OrdersSummaryOut:
    return OrdersSummaryOut(**svc.summary(session, ctx.shop_id))


@router.get("/online-orders", response_model=OrderPage)
def list_orders(
    ctx: Ctx,
    session: ReadSession,
    status: Annotated[list[OnlineOrderStatus] | None, Query(description="Repeat to allow several")] = None,
    q: Annotated[str | None, Query(max_length=60)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrderPage:
    rows, total = svc.list_orders(session, ctx.shop_id, statuses=status, q=q, date_from=date_from, date_to=date_to, limit=limit, offset=offset)
    items = [
        OrderRowOut(
            id=r.order.id, order_no=r.order.order_no, status=r.order.status, fulfilment=r.order.fulfilment_type, payment=r.order.payment_method,
            customer_name=r.order.customer_name, customer_phone=r.order.customer_phone, total_amount=r.order.total_amount, item_count=r.item_count,
            placed_at=r.order.placed_at, sale_id=r.order.sale_id,
        )
        for r in rows
    ]  # fmt: skip
    return OrderPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/online-orders/{order_id}", response_model=OrderDetailOut)
def get_order(order_id: int, ctx: Ctx, session: ReadSession) -> OrderDetailOut:
    return OrderDetailOut.from_detail(svc.get_order(session, ctx.shop_id, order_id))


@router.post("/online-orders/{order_id}/accept", response_model=OrderDetailOut)
def accept(order_id: int, ctx: Ctx, payload: AcceptIn | None = None) -> OrderDetailOut:
    with write_transaction() as session:
        return OrderDetailOut.from_detail(svc.accept(session, ctx, order_id, payload.note if payload else None))


@router.post("/online-orders/{order_id}/reject", response_model=OrderDetailOut)
def reject(order_id: int, payload: ReasonIn, ctx: Ctx) -> OrderDetailOut:
    with write_transaction() as session:
        return OrderDetailOut.from_detail(svc.reject(session, ctx, order_id, payload.reason))


@router.post("/online-orders/{order_id}/cancel", response_model=OrderDetailOut)
def cancel(order_id: int, payload: ReasonIn, ctx: Ctx) -> OrderDetailOut:
    with write_transaction() as session:
        return OrderDetailOut.from_detail(svc.cancel(session, ctx, order_id, payload.reason))


@router.post("/online-orders/{order_id}/advance", response_model=OrderDetailOut)
def advance(order_id: int, payload: AdvanceIn, ctx: Ctx) -> OrderDetailOut:
    """Preparing, ready, out for delivery, or delivered. Delivered also creates the sale, so it needs the permission to sell."""
    if payload.status is OnlineOrderStatus.DELIVERED:
        authorization_service.require(ctx, "SALE_CREATE", "SALE_POST")
    with write_transaction() as session:
        return OrderDetailOut.from_detail(svc.advance(session, ctx, order_id, payload.status, payload.note))
