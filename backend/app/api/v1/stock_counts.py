"""Stock counting. See `stock_count_service` for the workflow and every safeguard; this router only parses
requests, opens the write transaction, and shapes the response."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.models.enums import StockCountStatus
from app.schemas.stock_count import (
    ApproveIn,
    CancelIn,
    EnterCountsIn,
    StockCountCreateIn,
    StockCountListOut,
    StockCountOut,
)
from app.services import stock_count_service

router = APIRouter(prefix="/stock-counts", tags=["stock counting"])
ReadSession = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("", response_model=StockCountListOut)
def list_counts(
    ctx: Ctx,
    session: ReadSession,
    status: StockCountStatus | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> StockCountListOut:
    rows, total = stock_count_service.list_counts(
        session, ctx.shop_id, status=status, limit=limit, offset=offset
    )
    return StockCountListOut(
        items=[StockCountOut.of(c) for c in rows], total=total, limit=limit, offset=offset
    )


@router.post("", response_model=StockCountOut, status_code=201)
def create_count(payload: StockCountCreateIn, ctx: Ctx) -> StockCountOut:
    with write_transaction() as session:
        count = stock_count_service.create(
            session, ctx, title=payload.title, scope=payload.scope, category_id=payload.category_id,
            product_ids=payload.product_ids, notes=payload.notes,
        )  # fmt: skip
        return StockCountOut.of(count, items=stock_count_service.get_items(session, ctx.shop_id, count.id))


@router.get("/{count_id}", response_model=StockCountOut)
def get_count(count_id: int, ctx: Ctx, session: ReadSession) -> StockCountOut:
    count = stock_count_service.get(session, ctx.shop_id, count_id)
    return StockCountOut.of(count, items=stock_count_service.get_items(session, ctx.shop_id, count_id))


@router.post("/{count_id}/start-counting", response_model=StockCountOut)
def start_counting(count_id: int, ctx: Ctx) -> StockCountOut:
    with write_transaction() as session:
        count = stock_count_service.start_counting(session, ctx, count_id)
        return StockCountOut.of(count, items=stock_count_service.get_items(session, ctx.shop_id, count.id))


@router.put("/{count_id}/counts", response_model=StockCountOut)
def enter_counts(count_id: int, payload: EnterCountsIn, ctx: Ctx) -> StockCountOut:
    with write_transaction() as session:
        entries = [
            {"product_id": e.product_id, "counted_quantity": e.counted_quantity, "note": e.note}
            for e in payload.entries
        ]
        count = stock_count_service.enter_counts(session, ctx, count_id, entries)
        return StockCountOut.of(count, items=stock_count_service.get_items(session, ctx.shop_id, count.id))


@router.post("/{count_id}/submit-for-review", response_model=StockCountOut)
def submit_for_review(count_id: int, ctx: Ctx) -> StockCountOut:
    with write_transaction() as session:
        count = stock_count_service.submit_for_review(session, ctx, count_id)
        return StockCountOut.of(count, items=stock_count_service.get_items(session, ctx.shop_id, count.id))


@router.post("/{count_id}/approve", response_model=StockCountOut)
def approve(count_id: int, payload: ApproveIn, ctx: Ctx) -> StockCountOut:
    with write_transaction() as session:
        count = stock_count_service.approve(session, ctx, count_id, note=payload.note)
        return StockCountOut.of(count, items=stock_count_service.get_items(session, ctx.shop_id, count.id))


@router.post("/{count_id}/post", response_model=StockCountOut)
def post_count(count_id: int, ctx: Ctx) -> StockCountOut:
    with write_transaction() as session:
        count = stock_count_service.post(session, ctx, count_id)
        return StockCountOut.of(count, items=stock_count_service.get_items(session, ctx.shop_id, count.id))


@router.post("/{count_id}/cancel", response_model=StockCountOut)
def cancel(count_id: int, payload: CancelIn, ctx: Ctx) -> StockCountOut:
    with write_transaction() as session:
        count = stock_count_service.cancel(session, ctx, count_id, reason=payload.reason)
        return StockCountOut.of(count, items=stock_count_service.get_items(session, ctx.shop_id, count.id))
