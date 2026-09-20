"""Supplier endpoints. There is deliberately no DELETE: suppliers are deactivated, never removed."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.api.v1.products import StatusFilter, active_flag
from app.db.session import get_session, write_transaction
from app.schemas.supplier import (
    SupplierCreate,
    SupplierListOut,
    SupplierOptionOut,
    SupplierOut,
    SupplierSaved,
    SupplierUpdate,
)
from app.services import supplier_service

router = APIRouter(prefix="/suppliers", tags=["suppliers"])
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("", response_model=SupplierListOut)
def list_suppliers(
    ctx: Ctx,
    session: ReadSession,
    q: Annotated[str | None, Query(max_length=100, description="Search name, phone, email or GSTIN")] = None,
    status: StatusFilter = StatusFilter.ACTIVE,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SupplierListOut:
    views, total = supplier_service.list_suppliers(
        session, ctx.shop_id, q=q, active=active_flag(status), limit=limit, offset=offset
    )
    return SupplierListOut(
        items=[SupplierOut.from_view(v) for v in views], total=total, limit=limit, offset=offset
    )


@router.get("/options", response_model=list[SupplierOptionOut])
def list_supplier_options(ctx: Ctx, session: ReadSession) -> list[SupplierOptionOut]:
    """All active suppliers, id and name only, for pickers (a product's default supplier)."""
    return [
        SupplierOptionOut(id=s.id, name=s.name)
        for s in supplier_service.list_supplier_options(session, ctx.shop_id)
    ]


@router.post("", response_model=SupplierSaved, status_code=201)
def create_supplier(payload: SupplierCreate, ctx: Ctx) -> SupplierSaved:
    with write_transaction() as session:
        result = supplier_service.create_supplier(session, ctx, payload.model_dump())
        return SupplierSaved.from_result(result)


@router.get("/{supplier_id}", response_model=SupplierOut)
def get_supplier(supplier_id: int, ctx: Ctx, session: ReadSession) -> SupplierOut:
    return SupplierOut.from_view(supplier_service.get_supplier_view(session, ctx.shop_id, supplier_id))


@router.patch("/{supplier_id}", response_model=SupplierSaved)
def update_supplier(supplier_id: int, payload: SupplierUpdate, ctx: Ctx) -> SupplierSaved:
    with write_transaction() as session:
        result = supplier_service.update_supplier(
            session, ctx, supplier_id, payload.model_dump(exclude_unset=True)
        )
        return SupplierSaved.from_result(result)


@router.post("/{supplier_id}/activate", response_model=SupplierOut)
def activate_supplier(supplier_id: int, ctx: Ctx) -> SupplierOut:
    with write_transaction() as session:
        return SupplierOut.from_view(
            supplier_service.set_supplier_active(session, ctx, supplier_id, active=True)
        )


@router.post("/{supplier_id}/deactivate", response_model=SupplierOut)
def deactivate_supplier(supplier_id: int, ctx: Ctx) -> SupplierOut:
    with write_transaction() as session:
        return SupplierOut.from_view(
            supplier_service.set_supplier_active(session, ctx, supplier_id, active=False)
        )
