"""Quick/Daily Sale endpoints: money-only entries. There is deliberately no DELETE and no product line.

Posting and voiding, and the credit they put on a customer's khata, are single transactions. A quick sale
never touches stock, and its profit is always "Not Available".
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.models.enums import PaymentType, SaleStatus
from app.schemas.quick_sale import (
    QuickSaleCreate,
    QuickSaleListOut,
    QuickSaleOut,
    QuickSaleSummaryOut,
    QuickSaleUpdate,
)
from app.schemas.sale import SalePostIn, SaleVoidIn
from app.services import quick_sale_service

router = APIRouter(prefix="/quick-sales", tags=["quick-sales"])
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("", response_model=QuickSaleListOut)
def list_quick_sales(
    ctx: Ctx,
    session: ReadSession,
    q: Annotated[
        str | None, Query(max_length=100, description="Search number, customer name or phone, or note")
    ] = None,
    customer_id: int | None = None,
    status: Annotated[list[SaleStatus] | None, Query(description="Repeat to allow several")] = None,
    payment_type: PaymentType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QuickSaleListOut:
    rows, total = quick_sale_service.list_quick_sales(
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
    return QuickSaleListOut(
        items=[QuickSaleSummaryOut.from_row(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.post("", response_model=QuickSaleOut, status_code=201)
def create_quick_sale(payload: QuickSaleCreate, ctx: Ctx) -> QuickSaleOut:
    """Start an entry (a draft). Nothing is posted until `/post`."""
    with write_transaction() as session:
        return QuickSaleOut.from_view(
            quick_sale_service.create_quick_sale(session, ctx, payload.model_dump())
        )


@router.get("/{quick_sale_id}", response_model=QuickSaleOut)
def get_quick_sale(quick_sale_id: int, ctx: Ctx, session: ReadSession) -> QuickSaleOut:
    return QuickSaleOut.from_view(quick_sale_service.get_quick_sale_view(session, ctx.shop_id, quick_sale_id))


@router.patch("/{quick_sale_id}", response_model=QuickSaleOut)
def update_quick_sale(quick_sale_id: int, payload: QuickSaleUpdate, ctx: Ctx) -> QuickSaleOut:
    with write_transaction() as session:
        return QuickSaleOut.from_view(
            quick_sale_service.update_quick_sale(
                session, ctx, quick_sale_id, payload.model_dump(exclude_unset=True)
            )
        )


@router.post("/{quick_sale_id}/post", response_model=QuickSaleOut)
def post_quick_sale(quick_sale_id: int, ctx: Ctx, payload: SalePostIn | None = None) -> QuickSaleOut:
    """Post a draft: settle the payment, number it, charge any unpaid part to the customer's khata.
    With no body the entry is paid in full."""
    payment = payload or SalePostIn()
    with write_transaction() as session:
        return QuickSaleOut.from_view(
            quick_sale_service.post_quick_sale(
                session,
                ctx,
                quick_sale_id,
                amount_paid=payment.amount_paid,
                payment_method=payment.payment_method,
                payment_reference=payment.payment_reference,
            )
        )


@router.post("/{quick_sale_id}/void", response_model=QuickSaleOut)
def void_quick_sale(quick_sale_id: int, payload: SaleVoidIn, ctx: Ctx) -> QuickSaleOut:
    """Void a posted entry (its khata charge is reversed) or discard a draft. A reason is required."""
    with write_transaction() as session:
        return QuickSaleOut.from_view(
            quick_sale_service.void_quick_sale(session, ctx, quick_sale_id, payload.reason)
        )
