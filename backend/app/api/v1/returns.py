"""Sales returns and purchase returns.

There is deliberately no DELETE and no edit: a return is posted once, and a wrong one is voided (goods and
khata credit are reversed; the return and its number stay). Posting is a single transaction, and the refund
is always worked out by the server. Creating a return accepts an `Idempotency-Key`, so a repeated request
cannot refund or send back goods twice.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.api.idempotency import IdempotencyHeader, run_idempotent
from app.db.session import get_session, write_transaction
from app.models.enums import DocumentStatus
from app.schemas.returns import (
    PurchaseReturnCalculateIn,
    PurchaseReturnCreate,
    PurchaseReturnListOut,
    PurchaseReturnOut,
    PurchaseReturnPreviewOut,
    PurchaseReturnRowOut,
    SalesReturnCalculateIn,
    SalesReturnCreate,
    SalesReturnListOut,
    SalesReturnOut,
    SalesReturnPreviewOut,
    SalesReturnRowOut,
    VoidIn,
)
from app.services import purchase_return_service, sales_return_service

sales_returns = APIRouter(prefix="/sales-returns", tags=["returns"])
purchase_returns = APIRouter(prefix="/purchase-returns", tags=["returns"])
ReadSession = Annotated[Session, Depends(get_session)]


@sales_returns.get("", response_model=SalesReturnListOut)
def list_sales_returns(
    ctx: Ctx,
    session: ReadSession,
    q: Annotated[
        str | None, Query(max_length=100, description="Search return no., invoice no. or customer")
    ] = None,
    sale_id: int | None = None,
    status: Annotated[list[DocumentStatus] | None, Query(description="Repeat to allow several")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SalesReturnListOut:
    rows, total = sales_return_service.list_returns(
        session,
        ctx.shop_id,
        q=q,
        sale_id=sale_id,
        statuses=status,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return SalesReturnListOut(
        items=[SalesReturnRowOut.from_row(r) for r in rows], total=total, limit=limit, offset=offset
    )


@sales_returns.post("/calculate", response_model=SalesReturnPreviewOut)
def calculate_sales_return(
    payload: SalesReturnCalculateIn, ctx: Ctx, session: ReadSession
) -> SalesReturnPreviewOut:
    """What a return would refund and what can still come back. Nothing is saved."""
    items = [i.model_dump() for i in payload.items]
    return SalesReturnPreviewOut.from_preview(
        sales_return_service.calculate_preview(
            session, ctx.shop_id, payload.sale_id, items, payload.refund_mode
        )
    )


@sales_returns.post("", response_model=SalesReturnOut, status_code=201)
def create_sales_return(
    payload: SalesReturnCreate, ctx: Ctx, idempotency_key: IdempotencyHeader = None
) -> SalesReturnOut:
    """Post a return: the goods go back on the shelf and the refund is settled, all in one transaction."""
    items = [i.model_dump() for i in payload.items]
    return run_idempotent(
        ctx,
        idempotency_key,
        "sales_return.create",
        payload.model_dump(mode="json"),
        lambda session: SalesReturnOut.from_view(
            sales_return_service.create_sales_return(
                session,
                ctx,
                payload.sale_id,
                items=items,
                refund_mode=payload.refund_mode,
                return_date=payload.return_date,
                reason=payload.reason,
            )
        ),
        status_code=201,
    )


@sales_returns.get("/{return_id}", response_model=SalesReturnOut)
def get_sales_return(return_id: int, ctx: Ctx, session: ReadSession) -> SalesReturnOut:
    return SalesReturnOut.from_view(sales_return_service.get_view(session, ctx.shop_id, return_id))


@sales_returns.post("/{return_id}/void", response_model=SalesReturnOut)
def void_sales_return(return_id: int, payload: VoidIn, ctx: Ctx) -> SalesReturnOut:
    """Void a return: goods leave the shelf again and any khata credit is taken back. A reason is needed."""
    with write_transaction() as session:
        return SalesReturnOut.from_view(
            sales_return_service.void_sales_return(session, ctx, return_id, payload.reason)
        )


@purchase_returns.get("", response_model=PurchaseReturnListOut)
def list_purchase_returns(
    ctx: Ctx,
    session: ReadSession,
    q: Annotated[
        str | None, Query(max_length=100, description="Search return no., purchase no. or supplier")
    ] = None,
    purchase_id: int | None = None,
    status: Annotated[list[DocumentStatus] | None, Query(description="Repeat to allow several")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PurchaseReturnListOut:
    rows, total = purchase_return_service.list_returns(
        session,
        ctx.shop_id,
        q=q,
        purchase_id=purchase_id,
        statuses=status,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return PurchaseReturnListOut(
        items=[PurchaseReturnRowOut.from_row(r) for r in rows], total=total, limit=limit, offset=offset
    )


@purchase_returns.post("/calculate", response_model=PurchaseReturnPreviewOut)
def calculate_purchase_return(
    payload: PurchaseReturnCalculateIn, ctx: Ctx, session: ReadSession
) -> PurchaseReturnPreviewOut:
    items = [i.model_dump() for i in payload.items]
    return PurchaseReturnPreviewOut.from_preview(
        purchase_return_service.calculate_preview(session, ctx.shop_id, payload.purchase_id, items)
    )


@purchase_returns.post("", response_model=PurchaseReturnOut, status_code=201)
def create_purchase_return(
    payload: PurchaseReturnCreate, ctx: Ctx, idempotency_key: IdempotencyHeader = None
) -> PurchaseReturnOut:
    """Post a return: the goods leave the shelf and go back to the supplier, all in one transaction."""
    items = [i.model_dump() for i in payload.items]
    return run_idempotent(
        ctx,
        idempotency_key,
        "purchase_return.create",
        payload.model_dump(mode="json"),
        lambda session: PurchaseReturnOut.from_view(
            purchase_return_service.create_purchase_return(
                session,
                ctx,
                payload.purchase_id,
                items=items,
                credit_mode=payload.credit_mode,
                return_date=payload.return_date,
                reason=payload.reason,
            )
        ),
        status_code=201,
    )


@purchase_returns.get("/{return_id}", response_model=PurchaseReturnOut)
def get_purchase_return(return_id: int, ctx: Ctx, session: ReadSession) -> PurchaseReturnOut:
    return PurchaseReturnOut.from_view(purchase_return_service.get_view(session, ctx.shop_id, return_id))


@purchase_returns.post("/{return_id}/void", response_model=PurchaseReturnOut)
def void_purchase_return(return_id: int, payload: VoidIn, ctx: Ctx) -> PurchaseReturnOut:
    """Void a return: the goods come back onto the shelf. A reason is required."""
    with write_transaction() as session:
        return PurchaseReturnOut.from_view(
            purchase_return_service.void_purchase_return(session, ctx, return_id, payload.reason)
        )
