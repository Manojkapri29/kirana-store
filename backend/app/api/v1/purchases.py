"""Purchase endpoints.

There is deliberately no DELETE: a draft is discarded and a posted purchase is voided (both through
`/void`), so the record and its numbers are kept. Lines of a draft are changed with the item endpoints.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.models.enums import PurchaseStatus
from app.schemas.purchase import (
    PurchaseCreate,
    PurchaseItemIn,
    PurchaseItemsReplace,
    PurchaseItemUpdate,
    PurchaseListOut,
    PurchaseOut,
    PurchaseSummaryOut,
    PurchaseUpdate,
    PurchaseVoidIn,
    SupplierPurchaseTotalsOut,
)
from app.services import purchase_service

router = APIRouter(prefix="/purchases", tags=["purchases"])
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("", response_model=PurchaseListOut)
def list_purchases(
    ctx: Ctx,
    session: ReadSession,
    q: Annotated[
        str | None,
        Query(max_length=100, description="Search purchase no., supplier invoice no., supplier or notes"),
    ] = None,
    supplier_id: int | None = None,
    status: Annotated[list[PurchaseStatus] | None, Query(description="Repeat to allow several")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PurchaseListOut:
    rows, total = purchase_service.list_purchases(
        session,
        ctx.shop_id,
        q=q,
        supplier_id=supplier_id,
        statuses=status,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return PurchaseListOut(
        items=[PurchaseSummaryOut.from_row(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/supplier-totals/{supplier_id}", response_model=SupplierPurchaseTotalsOut)
def get_supplier_totals(supplier_id: int, ctx: Ctx, session: ReadSession) -> SupplierPurchaseTotalsOut:
    """How many posted purchases a supplier has and what they add up to."""
    totals = purchase_service.supplier_totals(session, ctx.shop_id, supplier_id)
    return SupplierPurchaseTotalsOut(posted_count=totals.posted_count, posted_total=totals.posted_total)


@router.post("", response_model=PurchaseOut, status_code=201)
def create_purchase(payload: PurchaseCreate, ctx: Ctx) -> PurchaseOut:
    """Create a draft purchase. Nothing is posted and no stock moves until `/post`."""
    header = payload.model_dump(exclude={"items"})
    items = [item.model_dump() for item in payload.items]
    with write_transaction() as session:
        return PurchaseOut.from_view(purchase_service.create_purchase(session, ctx, header, items))


@router.get("/{purchase_id}", response_model=PurchaseOut)
def get_purchase(purchase_id: int, ctx: Ctx, session: ReadSession) -> PurchaseOut:
    return PurchaseOut.from_view(purchase_service.get_purchase_view(session, ctx.shop_id, purchase_id))


@router.patch("/{purchase_id}", response_model=PurchaseOut)
def update_purchase(purchase_id: int, payload: PurchaseUpdate, ctx: Ctx) -> PurchaseOut:
    with write_transaction() as session:
        return PurchaseOut.from_view(
            purchase_service.update_purchase(
                session, ctx, purchase_id, payload.model_dump(exclude_unset=True)
            )
        )


@router.put("/{purchase_id}/items", response_model=PurchaseOut)
def replace_items(purchase_id: int, payload: PurchaseItemsReplace, ctx: Ctx) -> PurchaseOut:
    """Replace every line of a draft (this is also how lines are removed)."""
    with write_transaction() as session:
        return PurchaseOut.from_view(
            purchase_service.replace_items(
                session, ctx, purchase_id, [item.model_dump() for item in payload.items]
            )
        )


@router.post("/{purchase_id}/items", response_model=PurchaseOut, status_code=201)
def add_item(purchase_id: int, payload: PurchaseItemIn, ctx: Ctx) -> PurchaseOut:
    with write_transaction() as session:
        return PurchaseOut.from_view(
            purchase_service.add_item(session, ctx, purchase_id, payload.model_dump())
        )


@router.patch("/{purchase_id}/items/{item_id}", response_model=PurchaseOut)
def update_item(purchase_id: int, item_id: int, payload: PurchaseItemUpdate, ctx: Ctx) -> PurchaseOut:
    with write_transaction() as session:
        return PurchaseOut.from_view(
            purchase_service.update_item(
                session, ctx, purchase_id, item_id, payload.model_dump(exclude_unset=True)
            )
        )


@router.post("/{purchase_id}/post", response_model=PurchaseOut)
def post_purchase(purchase_id: int, ctx: Ctx) -> PurchaseOut:
    """Post a draft: number it, add the stock and update the average costs, all in one transaction."""
    with write_transaction() as session:
        return PurchaseOut.from_view(purchase_service.post_purchase(session, ctx, purchase_id))


@router.post("/{purchase_id}/void", response_model=PurchaseOut)
def void_purchase(purchase_id: int, payload: PurchaseVoidIn, ctx: Ctx) -> PurchaseOut:
    """Void a posted purchase (its stock is reversed) or discard a draft. A reason is required."""
    with write_transaction() as session:
        return PurchaseOut.from_view(
            purchase_service.void_purchase(session, ctx, purchase_id, payload.reason)
        )


@router.post("/{purchase_id}/correct", response_model=PurchaseOut, status_code=201)
def correct_purchase(purchase_id: int, ctx: Ctx) -> PurchaseOut:
    """Copy a voided purchase into a new draft, ready to correct and post."""
    with write_transaction() as session:
        return PurchaseOut.from_view(purchase_service.correct_purchase(session, ctx, purchase_id))
