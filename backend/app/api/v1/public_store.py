"""The public storefront. No sign-in: a customer opens `/store/<slug>`, sees the listed products, places an order and follows it with the
tracking token they were given. Every route here is rate limited by client address and returns only what a shopper may see."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import client_address
from app.core import ratelimit
from app.db.session import get_session, write_transaction
from app.schemas.online_store import (
    PublicOrderIn,
    PublicOrderOut,
    PublicProductOut,
    PublicProductPage,
    PublicStoreOut,
)
from app.services import online_store_service as svc

router = APIRouter(prefix="/public/stores", tags=["public storefront"])
ReadSession = Annotated[Session, Depends(get_session)]
Slug = Annotated[str, Query(max_length=40)]


def _limit(request: Request, group: str = "public") -> None:
    ratelimit.enforce(group, client_address(request))


def _store_out(row) -> PublicStoreOut:  # noqa: ANN001
    return PublicStoreOut(
        slug=row.slug, name=row.display_name, is_open=row.is_open, accepts_cod=row.accepts_cod, accepts_upi=row.accepts_upi,
        delivery_enabled=row.delivery_enabled, pickup_enabled=row.pickup_enabled, min_order_amount=row.min_order_amount,
        contact_phone=row.contact_phone, announcement=row.announcement,
    )  # fmt: skip


@router.get("/{slug}", response_model=PublicStoreOut)
def store(slug: str, request: Request, session: ReadSession) -> PublicStoreOut:
    _limit(request)
    return _store_out(svc.public_store(session, slug))


@router.get("/{slug}/products", response_model=PublicProductPage)
def products(slug: str, request: Request, session: ReadSession, q: Annotated[str | None, Query(max_length=60)] = None, category: Annotated[str | None, Query(max_length=60)] = None, limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0) -> PublicProductPage:
    _limit(request)
    rows, total = svc.public_products(session, slug, q=q, category=category, limit=limit, offset=offset)
    return PublicProductPage(items=[PublicProductOut(id=r.id, name=r.name, category=r.category, unit=r.unit, allows_decimal=r.allows_decimal, price=r.price, in_stock=r.in_stock) for r in rows], total=total, limit=limit, offset=offset)


@router.post("/{slug}/orders", response_model=PublicOrderOut, status_code=201)
def place_order(slug: str, payload: PublicOrderIn, request: Request, idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> PublicOrderOut:
    _limit(request, "store_order")
    order_in = svc.OrderIn(
        customer_name=payload.customer_name, customer_phone=payload.customer_phone, fulfilment=payload.fulfilment, payment=payload.payment,
        delivery_address=payload.delivery_address, notes=payload.notes, items=[svc.OrderLineIn(i.product_id, i.quantity) for i in payload.items],
    )  # fmt: skip
    with write_transaction() as session:
        placed = svc.place_order(session, slug, order_in, idempotency_key or "")
        view = svc.public_order_view(session, slug, svc.public_reference(placed.order.order_no), placed.tracking_token)
        return PublicOrderOut.from_view(view, token=placed.tracking_token)


@router.get("/{slug}/orders/{reference}", response_model=PublicOrderOut)
def track(slug: str, reference: str, request: Request, session: ReadSession, token: Annotated[str, Query(max_length=64)] = "") -> PublicOrderOut:
    _limit(request)
    return PublicOrderOut.from_view(svc.public_order_view(session, slug, reference, token))


@router.post("/{slug}/orders/{reference}/cancel", response_model=PublicOrderOut)
def cancel(slug: str, reference: str, request: Request, token: Annotated[str, Query(max_length=64)] = "") -> PublicOrderOut:
    _limit(request)
    with write_transaction() as session:
        return PublicOrderOut.from_view(svc.customer_cancel(session, slug, reference, token))
