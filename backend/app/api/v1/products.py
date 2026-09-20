"""Product endpoints. There is deliberately no DELETE: products are deactivated, never removed."""

from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.schemas.product import ProductCreate, ProductListOut, ProductOut, ProductSaved, ProductUpdate
from app.services import product_service

router = APIRouter(prefix="/products", tags=["products"])
ReadSession = Annotated[Session, Depends(get_session)]


class StatusFilter(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ALL = "all"


def active_flag(status: StatusFilter) -> bool | None:
    return {StatusFilter.ACTIVE: True, StatusFilter.INACTIVE: False, StatusFilter.ALL: None}[status]


@router.get("", response_model=ProductListOut)
def list_products(
    ctx: Ctx,
    session: ReadSession,
    q: Annotated[str | None, Query(description="Search SKU, name, brand or barcode")] = None,
    barcode: Annotated[str | None, Query(description="Exact barcode match")] = None,
    category_id: int | None = None,
    unit_id: int | None = None,
    status: StatusFilter = StatusFilter.ACTIVE,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductListOut:
    views, total = product_service.list_products(
        session,
        ctx.shop_id,
        q=q,
        barcode=barcode,
        category_id=category_id,
        unit_id=unit_id,
        active=active_flag(status),
        limit=limit,
        offset=offset,
    )
    return ProductListOut(
        items=[ProductOut.from_view(v) for v in views], total=total, limit=limit, offset=offset
    )


@router.post("", response_model=ProductSaved, status_code=201)
def create_product(payload: ProductCreate, ctx: Ctx) -> ProductSaved:
    with write_transaction() as session:
        result = product_service.create_product(session, ctx, payload.model_dump())
        return ProductSaved.from_result(result)


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, ctx: Ctx, session: ReadSession) -> ProductOut:
    return ProductOut.from_view(product_service.get_product_view(session, ctx.shop_id, product_id))


@router.patch("/{product_id}", response_model=ProductSaved)
def update_product(product_id: int, payload: ProductUpdate, ctx: Ctx) -> ProductSaved:
    with write_transaction() as session:
        result = product_service.update_product(
            session, ctx, product_id, payload.model_dump(exclude_unset=True)
        )
        return ProductSaved.from_result(result)


@router.post("/{product_id}/activate", response_model=ProductOut)
def activate_product(product_id: int, ctx: Ctx) -> ProductOut:
    with write_transaction() as session:
        return ProductOut.from_view(product_service.set_product_active(session, ctx, product_id, active=True))


@router.post("/{product_id}/deactivate", response_model=ProductOut)
def deactivate_product(product_id: int, ctx: Ctx) -> ProductOut:
    with write_transaction() as session:
        return ProductOut.from_view(
            product_service.set_product_active(session, ctx, product_id, active=False)
        )
