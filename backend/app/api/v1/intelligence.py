"""Read-only intelligence: inventory health, reorder recommendations, purchase planning, supplier and customer
analytics, business health, and the advanced dashboard. Every number comes from the existing report,
inventory, analytics and khata services (see each service's own module docstring); nothing here calculates a
business number a second way, and nothing here writes anything except `POST
/intelligence/purchase-suggestions/draft`, which creates an ordinary purchase DRAFT through the unchanged
`purchase_service` — posting it is a separate, already-existing step."""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.schemas.intelligence import (
    AgingOut,
    BusinessHealthOut,
    CustomerAnalyticsOut,
    DashboardOut,
    InventoryHealthOut,
    MoverOut,
    ProductPriceHistoryOut,
    PurchaseDraftFromSuggestionsIn,
    ReorderRecommendationOut,
    SupplierAnalyticsOut,
    SupplierGroupOut,
)
from app.schemas.purchase import PurchaseOut
from app.services import ai_insights_service as insights
from app.services import (
    business_health_service,
    customer_intelligence_service,
    inventory_intelligence_service,
    khata_service,
    purchase_planning_service,
    supplier_intelligence_service,
)
from app.services.shop_service import get_shop, shop_today

router = APIRouter(prefix="/intelligence", tags=["business intelligence"])
ReadSession = Annotated[Session, Depends(get_session)]
Days = Annotated[int, Query(ge=1, le=365)]
Limit = Annotated[int, Query(ge=1, le=200)]


@router.get("/inventory-health", response_model=InventoryHealthOut)
def inventory_health(ctx: Ctx, session: ReadSession, days: Days = 30) -> InventoryHealthOut:
    today = shop_today(get_shop(session, ctx.shop_id))
    return InventoryHealthOut.of(
        inventory_intelligence_service.inventory_health(session, ctx.shop_id, today, days)
    )


@router.get("/fast-moving", response_model=list[MoverOut])
def fast_moving(ctx: Ctx, session: ReadSession, days: Days = 30, limit: Limit = 20) -> list[MoverOut]:
    today = shop_today(get_shop(session, ctx.shop_id))
    return [
        MoverOut.of(m)
        for m in inventory_intelligence_service.fast_moving(session, ctx.shop_id, today, days, limit)
    ]


@router.get("/slow-moving", response_model=list[MoverOut])
def slow_moving(ctx: Ctx, session: ReadSession, days: Days = 60) -> list[MoverOut]:
    today = shop_today(get_shop(session, ctx.shop_id))
    return [
        MoverOut.of(m) for m in inventory_intelligence_service.slow_moving(session, ctx.shop_id, today, days)
    ]


@router.get("/dead-stock", response_model=list[MoverOut])
def dead_stock(ctx: Ctx, session: ReadSession, days: Days = 90) -> list[MoverOut]:
    today = shop_today(get_shop(session, ctx.shop_id))
    return [
        MoverOut.of(m) for m in inventory_intelligence_service.dead_stock(session, ctx.shop_id, today, days)
    ]


@router.get("/stock-aging", response_model=list[AgingOut])
def stock_aging(ctx: Ctx, session: ReadSession) -> list[AgingOut]:
    today = shop_today(get_shop(session, ctx.shop_id))
    return [AgingOut.of(a) for a in inventory_intelligence_service.stock_aging(session, ctx.shop_id, today)]


@router.get("/reorder-recommendations", response_model=list[ReorderRecommendationOut])
def reorder_recommendations(
    ctx: Ctx,
    session: ReadSession,
    window_days: Days = insights.WINDOW_DAYS,
    cover_days: Days = insights.COVER_DAYS,
) -> list[ReorderRecommendationOut]:
    today = shop_today(get_shop(session, ctx.shop_id))
    return [
        ReorderRecommendationOut.of(r)
        for r in insights.reorder_recommendations(
            session, ctx.shop_id, today, window_days=window_days, cover_days=cover_days
        )
    ]


@router.get("/purchase-suggestions", response_model=list[SupplierGroupOut])
def purchase_suggestions(
    ctx: Ctx,
    session: ReadSession,
    window_days: Days = insights.WINDOW_DAYS,
    cover_days: Days = insights.COVER_DAYS,
) -> list[SupplierGroupOut]:
    today = shop_today(get_shop(session, ctx.shop_id))
    return [
        SupplierGroupOut.of(g)
        for g in purchase_planning_service.suggestions(
            session, ctx.shop_id, today, window_days=window_days, cover_days=cover_days
        )
    ]


@router.post("/purchase-suggestions/draft", response_model=PurchaseOut, status_code=201)
def draft_from_suggestions(payload: PurchaseDraftFromSuggestionsIn, ctx: Ctx) -> PurchaseOut:
    with write_transaction() as session:
        lines = [
            {
                "product_id": ln.product_id,
                "quantity": ln.quantity,
                "unit_cost": ln.unit_cost,
                "discount": ln.discount,
            }
            for ln in payload.lines
        ]
        view = purchase_planning_service.build_draft(
            session,
            ctx,
            supplier_id=payload.supplier_id,
            lines=lines,
            purchase_date=payload.purchase_date,
            notes=payload.notes,
        )
        return PurchaseOut.from_view(view)


@router.get("/suppliers/{supplier_id}", response_model=SupplierAnalyticsOut)
def supplier_analytics(supplier_id: int, ctx: Ctx, session: ReadSession) -> SupplierAnalyticsOut:
    return SupplierAnalyticsOut.of(
        supplier_intelligence_service.analytics_for(session, ctx.shop_id, supplier_id)
    )


@router.get("/suppliers/{supplier_id}/price-history", response_model=ProductPriceHistoryOut)
def supplier_price_history(
    supplier_id: int, product_id: int, ctx: Ctx, session: ReadSession
) -> ProductPriceHistoryOut:
    return ProductPriceHistoryOut.of(
        supplier_intelligence_service.price_history(session, ctx.shop_id, product_id, supplier_id)
    )


@router.get("/customers", response_model=list[CustomerAnalyticsOut])
def customer_analytics(
    ctx: Ctx,
    session: ReadSession,
    segment: str | None = None,
    new_days: Days = 30,
    active_days: Days = 90,
    limit: Limit = 100,
) -> list[CustomerAnalyticsOut]:
    from app.services.customer_intelligence_service import CustomerSegment

    today = shop_today(get_shop(session, ctx.shop_id))
    chosen = CustomerSegment(segment) if segment else None
    rows = customer_intelligence_service.list_analytics(
        session, ctx.shop_id, today, segment=chosen, new_days=new_days, active_days=active_days, limit=limit
    )
    return [CustomerAnalyticsOut.of(r) for r in rows]


@router.get("/customers/{customer_id}", response_model=CustomerAnalyticsOut)
def customer_analytics_for(customer_id: int, ctx: Ctx, session: ReadSession) -> CustomerAnalyticsOut:
    today = shop_today(get_shop(session, ctx.shop_id))
    return CustomerAnalyticsOut.of(
        customer_intelligence_service.analytics_for(session, ctx.shop_id, customer_id, today)
    )


@router.get("/business-health", response_model=BusinessHealthOut)
def business_health(ctx: Ctx, session: ReadSession, days: Days = 7) -> BusinessHealthOut:
    today = shop_today(get_shop(session, ctx.shop_id))
    return BusinessHealthOut.of(business_health_service.health_report(session, ctx.shop_id, today, days=days))


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(ctx: Ctx, session: ReadSession, days: Days = 30) -> DashboardOut:
    today = shop_today(get_shop(session, ctx.shop_id))
    inv_health = inventory_intelligence_service.inventory_health(session, ctx.shop_id, today, days)
    health_report = business_health_service.health_report(session, ctx.shop_id, today)
    recs = insights.reorder_recommendations(session, ctx.shop_id, today)
    accounts, owing = khata_service.list_accounts(
        session, ctx.shop_id, balance=khata_service.BalanceStatus.OUTSTANDING, limit=None
    )
    return DashboardOut(
        inventory_health=InventoryHealthOut.of(inv_health),
        business_health=BusinessHealthOut.of(health_report),
        reorder_count=len(recs),
        outstanding_total=sum((a.outstanding for a in accounts), Decimal("0")),
        customers_owing=owing,
    )
