"""Detailed Sale endpoints (billing).

There is deliberately no DELETE: a draft is discarded and a posted sale is voided (both through `/void`), so
the record and its numbers are kept. Posting, voiding and the credit it puts on a customer's khata are single
transactions. Totals are always recalculated by the server; a client never supplies them.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.models.enums import PaymentType, SaleStatus
from app.schemas.sale import (
    CalculateIn,
    PreviewOut,
    SaleCreate,
    SaleItemsReplace,
    SaleListOut,
    SaleOut,
    SalePostIn,
    SaleSummaryOut,
    SaleUpdate,
    SaleVoidIn,
)
from app.services import sale_service

router = APIRouter(prefix="/sales", tags=["sales"])
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("", response_model=SaleListOut)
def list_sales(
    ctx: Ctx,
    session: ReadSession,
    q: Annotated[
        str | None,
        Query(max_length=100, description="Search invoice no., customer name or phone, or notes"),
    ] = None,
    customer_id: int | None = None,
    status: Annotated[list[SaleStatus] | None, Query(description="Repeat to allow several")] = None,
    payment_type: PaymentType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SaleListOut:
    rows, total = sale_service.list_sales(
        session,
        ctx.shop_id,
        q=q,
        customer_id=customer_id,
        statuses=status,
        payment_type=payment_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return SaleListOut(
        items=[SaleSummaryOut.from_row(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.post("/calculate", response_model=PreviewOut)
def calculate(payload: CalculateIn, ctx: Ctx, session: ReadSession) -> PreviewOut:
    """Price a cart without saving anything. The billing screen shows exactly these numbers."""
    items = [item.model_dump() for item in payload.items]
    return PreviewOut.from_preview(
        sale_service.calculate_preview(session, ctx.shop_id, items, payload.discount, payload.amount_paid)
    )


@router.post("", response_model=SaleOut, status_code=201)
def create_sale(payload: SaleCreate, ctx: Ctx) -> SaleOut:
    """Create a draft (a cart). Nothing is posted and no stock moves until `/post`."""
    header = payload.model_dump(exclude={"items"})
    items = [item.model_dump() for item in payload.items]
    with write_transaction() as session:
        return SaleOut.from_view(sale_service.create_sale(session, ctx, header, items))


@router.get("/{sale_id}", response_model=SaleOut)
def get_sale(sale_id: int, ctx: Ctx, session: ReadSession) -> SaleOut:
    return SaleOut.from_view(sale_service.get_sale_view(session, ctx.shop_id, sale_id))


@router.patch("/{sale_id}", response_model=SaleOut)
def update_sale(sale_id: int, payload: SaleUpdate, ctx: Ctx) -> SaleOut:
    with write_transaction() as session:
        return SaleOut.from_view(
            sale_service.update_sale(session, ctx, sale_id, payload.model_dump(exclude_unset=True))
        )


@router.put("/{sale_id}/items", response_model=SaleOut)
def replace_items(sale_id: int, payload: SaleItemsReplace, ctx: Ctx) -> SaleOut:
    """Replace every line of a draft (this is also how lines are changed or removed)."""
    with write_transaction() as session:
        return SaleOut.from_view(
            sale_service.replace_items(session, ctx, sale_id, [item.model_dump() for item in payload.items])
        )


@router.post("/{sale_id}/post", response_model=SaleOut)
def post_sale(sale_id: int, ctx: Ctx, payload: SalePostIn | None = None) -> SaleOut:
    """Post a draft: settle the payment, number it, take the stock out, record the cost, charge any credit to
    the customer's khata. All in one transaction. With no body the sale is paid in full."""
    payment = payload or SalePostIn()
    with write_transaction() as session:
        return SaleOut.from_view(
            sale_service.post_sale(
                session,
                ctx,
                sale_id,
                amount_paid=payment.amount_paid,
                payment_method=payment.payment_method,
                payment_reference=payment.payment_reference,
            )
        )


@router.post("/{sale_id}/void", response_model=SaleOut)
def void_sale(sale_id: int, payload: SaleVoidIn, ctx: Ctx) -> SaleOut:
    """Void a posted sale (stock and khata are reversed) or discard a draft. A reason is required."""
    with write_transaction() as session:
        return SaleOut.from_view(sale_service.void_sale(session, ctx, sale_id, payload.reason))


@router.post("/{sale_id}/correct", response_model=SaleOut, status_code=201)
def correct_sale(sale_id: int, ctx: Ctx) -> SaleOut:
    """Copy a voided sale into a new draft, ready to correct and post."""
    with write_transaction() as session:
        return SaleOut.from_view(sale_service.correct_sale(session, ctx, sale_id))
