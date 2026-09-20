"""Shop settings (read-only for now), units and categories."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.schemas.catalog import CategoryCreate, CategoryOut, CategoryUpdate, ShopOut, UnitOut
from app.services import catalog_service, shop_service

router = APIRouter(tags=["shop, units and categories"])
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("/shop", response_model=ShopOut)
def get_shop(ctx: Ctx, session: ReadSession) -> ShopOut:
    shop = shop_service.get_shop(session, ctx.shop_id)
    return ShopOut.model_validate(shop, from_attributes=True)


@router.get("/units", response_model=list[UnitOut])
def list_units(_: Ctx, session: ReadSession) -> list[UnitOut]:
    return [UnitOut.model_validate(u, from_attributes=True) for u in catalog_service.list_units(session)]


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(
    ctx: Ctx, session: ReadSession, active: Annotated[bool | None, Query()] = None
) -> list[CategoryOut]:
    categories = catalog_service.list_categories(session, ctx.shop_id, active=active)
    return [CategoryOut.model_validate(c, from_attributes=True) for c in categories]


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(payload: CategoryCreate, ctx: Ctx) -> CategoryOut:
    with write_transaction() as session:
        category = catalog_service.create_category(session, ctx, name=payload.name)
        return CategoryOut.model_validate(category, from_attributes=True)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, payload: CategoryUpdate, ctx: Ctx) -> CategoryOut:
    with write_transaction() as session:
        category = catalog_service.update_category(
            session, ctx, category_id, **payload.model_dump(exclude_unset=True)
        )
        return CategoryOut.model_validate(category, from_attributes=True)
