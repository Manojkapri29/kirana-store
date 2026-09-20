"""Inventory endpoints.

Every write goes through `inventory_service`; this module never touches the ledger itself.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.api.v1.products import StatusFilter, active_flag
from app.db.session import get_session, write_transaction
from app.models.enums import InventoryTxnType
from app.schemas.inventory import (
    InventoryItemOut,
    InventoryListOut,
    OpeningStockIn,
    OpeningStockOut,
    StockOut,
    TransactionListOut,
    TransactionOut,
)
from app.services import inventory_service, product_service
from app.services.inventory_service import StockStatus

router = APIRouter(prefix="/inventory", tags=["inventory"])
ReadSession = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.get("", response_model=InventoryListOut)
def list_inventory(
    ctx: Ctx,
    session: ReadSession,
    q: Annotated[str | None, Query(description="Search SKU, name, brand or barcode")] = None,
    category_id: int | None = None,
    status: StatusFilter = StatusFilter.ACTIVE,
    stock_status: StockStatus | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> InventoryListOut:
    rows, total = inventory_service.list_inventory(
        session,
        ctx.shop_id,
        q=q,
        category_id=category_id,
        active=active_flag(status),
        status=stock_status,
        limit=limit,
        offset=offset,
    )
    return InventoryListOut(
        items=[InventoryItemOut.from_row(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/transactions", response_model=TransactionListOut)
def list_all_transactions(
    ctx: Ctx,
    session: ReadSession,
    product_id: int | None = None,
    txn_type: InventoryTxnType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> TransactionListOut:
    rows, total = inventory_service.list_transactions(
        session,
        ctx.shop_id,
        product_id=product_id,
        txn_type=txn_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return TransactionListOut(
        items=[TransactionOut.from_row(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/products/{product_id}", response_model=StockOut)
def get_product_stock(product_id: int, ctx: Ctx, session: ReadSession) -> StockOut:
    return StockOut.from_view(product_service.get_product_view(session, ctx.shop_id, product_id))


@router.get("/products/{product_id}/transactions", response_model=TransactionListOut)
def get_product_history(
    product_id: int, ctx: Ctx, session: ReadSession, limit: Limit = 50, offset: Offset = 0
) -> TransactionListOut:
    product_service.get_product_view(session, ctx.shop_id, product_id)  # 404 for another shop's product
    rows, total = inventory_service.list_transactions(
        session, ctx.shop_id, product_id=product_id, limit=limit, offset=offset
    )
    return TransactionListOut(
        items=[TransactionOut.from_row(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.post("/opening-stock", response_model=OpeningStockOut, status_code=201)
def create_opening_stock(payload: OpeningStockIn, ctx: Ctx) -> OpeningStockOut:
    with write_transaction() as session:
        row = inventory_service.record_opening_stock(
            session,
            ctx,
            product_id=payload.product_id,
            quantity=payload.quantity,
            unit_cost=payload.unit_cost,
            txn_date=payload.txn_date,
            note=payload.note,
        )
        history, _ = inventory_service.list_transactions(
            session, ctx.shop_id, product_id=row.product_id, limit=1
        )
        view = product_service.get_product_view(session, ctx.shop_id, row.product_id)
        return OpeningStockOut(
            transaction=TransactionOut.from_row(history[0]), stock=StockOut.from_view(view)
        )
