"""Price intelligence endpoints: outside prices as information.

A check asks outside providers, so it never runs inside a database transaction: the read, the internet calls
and the write are three separate steps. It cannot affect billing (it is a different endpoint that nothing in
billing calls), and it never changes a price or a cost. A provider that is missing a key, switched off or down
is reported in the answer, not as an error.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import Ctx, feature_flag, rate_limited
from app.db.session import read_session, write_transaction
from app.schemas.price import (
    HistoryOut,
    ObservationOut,
    PriceCheckIn,
    PriceResultOut,
    ProviderListOut,
    ProviderStatusOut,
)
from app.services import price_comparison_service as svc

router = APIRouter(
    prefix="/price-intelligence",
    tags=["price-intelligence"],
    dependencies=[Depends(feature_flag("price_lookups")), Depends(rate_limited("external"))],
)


@router.get("/providers", response_model=ProviderListOut)
def providers(ctx: Ctx) -> ProviderListOut:
    """Which outside providers this server can use. Says configured true or false, never the key."""
    return ProviderListOut(items=[ProviderStatusOut.from_status(s) for s in svc.provider_statuses()])


@router.post("/check", response_model=PriceResultOut)
def check(payload: PriceCheckIn, ctx: Ctx) -> PriceResultOut:
    """Look up outside prices for a product or barcode, optionally near a city, state or market."""
    hint = svc.LocationHint(payload.city or None, payload.state or None, payload.market or None)
    with read_session() as session:
        prepared = svc.prepare(
            session, ctx.shop_id, product_id=payload.product_id, barcode=payload.barcode, location=hint
        )
    fetched = svc.fetch_live(prepared)  # the internet: no transaction is open here
    with write_transaction() as session:
        return PriceResultOut.from_result(svc.finish(session, ctx, prepared, fetched))


@router.get("/history", response_model=HistoryOut)
def history(
    ctx: Ctx,
    barcode: Annotated[str | None, Query(max_length=50)] = None,
    provider: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HistoryOut:
    """The prices this shop has saved from earlier checks, newest first."""
    with read_session() as session:
        rows, total = svc.list_history(
            session, ctx.shop_id, barcode=barcode, provider=provider, limit=limit, offset=offset
        )
        items = [
            ObservationOut(
                id=r.id,
                barcode=r.barcode,
                provider=r.provider,
                product_name=r.product_name,
                brand=r.brand,
                price=r.price,
                currency=r.currency,
                location=r.location_text,
                source_url=r.source_url,
                observed_on=r.observed_on,
                checked_at=r.checked_at,
            )
            for r in rows
        ]
    return HistoryOut(items=items, total=total, limit=limit, offset=offset)
