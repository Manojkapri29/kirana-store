"""The shop, its business type, units and categories."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx, OwnerCtx
from app.db.session import get_session, write_transaction
from app.schemas.catalog import (
    BusinessTypeOut,
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    ShopOut,
    ShopTemplateOut,
    ShopUpdate,
    UnitOut,
)
from app.services import business_type_service, catalog_service, shop_service

router = APIRouter(tags=["shop, units and categories"])
ReadSession = Annotated[Session, Depends(get_session)]


def _shop_out(session: Session, shop_id: int) -> ShopOut:
    shop = shop_service.get_shop(session, shop_id)
    business_type = business_type_service.get_business_type(session, shop.business_type)
    return ShopOut(
        id=shop.id,
        name=shop.name,
        business_type=shop.business_type,
        business_type_name=business_type.name if business_type else shop.business_type,
        timezone=shop.timezone,
        language=shop.language,
        allow_negative_stock=shop.allow_negative_stock,
        mrp_validation_mode=shop.mrp_validation_mode,
    )


@router.get("/shop", response_model=ShopOut)
def get_shop(ctx: Ctx, session: ReadSession) -> ShopOut:
    return _shop_out(session, ctx.shop_id)


@router.patch("/shop", response_model=ShopOut)
def update_shop(payload: ShopUpdate, ctx: OwnerCtx) -> ShopOut:
    """Change shop settings (only the business type for now). Owner only."""
    with write_transaction() as session:
        if payload.business_type is not None:
            business_type_service.set_shop_business_type(session, ctx, payload.business_type)
        return _shop_out(session, ctx.shop_id)


@router.get("/shop/template", response_model=ShopTemplateOut)
def get_shop_template(ctx: Ctx, session: ReadSession) -> ShopTemplateOut:
    """Categories and units suggested for this shop's kind of business. Suggestions only."""
    view = business_type_service.shop_template(session, ctx.shop_id)
    return ShopTemplateOut(
        business_type=view.business_type,
        business_type_name=view.business_type_name,
        categories=[{"name": c.name, "exists": c.exists} for c in view.categories],
        unit_codes=list(view.unit_codes),
    )


@router.get("/business-types", response_model=list[BusinessTypeOut])
def list_business_types(_: Ctx, session: ReadSession) -> list[BusinessTypeOut]:
    return [
        BusinessTypeOut(code=b.code, name=b.name) for b in business_type_service.list_business_types(session)
    ]


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
