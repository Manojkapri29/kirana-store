"""The CRM dashboard and retention analytics. See `crm_dashboard_service`/`retention_service`; every number
comes from a backend service, never calculated in the frontend."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.models.enums import NotificationChannel
from app.schemas.campaigns import CampaignOut
from app.schemas.crm_analytics import (
    CrmDashboardOut,
    PurchasePatternOut,
    ReactivationDraftIn,
    ReactivationPreviewOut,
    RetentionSummaryOut,
)
from app.services import crm_dashboard_service, reactivation_service, retention_service
from app.services.shop_service import get_shop, shop_today

router = APIRouter(prefix="/crm", tags=["crm analytics"])
ReadSession = Annotated[Session, Depends(get_session)]
PeriodDays = Annotated[int, Query(ge=1, le=730)]


@router.get("/dashboard", response_model=CrmDashboardOut)
def dashboard(ctx: Ctx, session: ReadSession, period_days: PeriodDays = 90) -> CrmDashboardOut:
    today = shop_today(get_shop(session, ctx.shop_id))
    return CrmDashboardOut.of(
        crm_dashboard_service.build(session, ctx.shop_id, today, period_days=period_days)
    )


@router.get("/retention", response_model=RetentionSummaryOut)
def retention(ctx: Ctx, session: ReadSession, period_days: PeriodDays = 90) -> RetentionSummaryOut:
    today = shop_today(get_shop(session, ctx.shop_id))
    return RetentionSummaryOut.of(
        retention_service.retention_summary(session, ctx.shop_id, today, period_days=period_days)
    )


@router.get("/reactivation-candidates", response_model=list[int])
def reactivation_candidates(
    ctx: Ctx, session: ReadSession, inactive_days: Annotated[int, Query(ge=1, le=730)] = 60
) -> list[int]:
    today = shop_today(get_shop(session, ctx.shop_id))
    return retention_service.reactivation_candidates(session, ctx.shop_id, today, inactive_days=inactive_days)


@router.get("/purchase-patterns", response_model=list[PurchasePatternOut])
def purchase_patterns(ctx: Ctx, session: ReadSession) -> list[PurchasePatternOut]:
    today = shop_today(get_shop(session, ctx.shop_id))
    return [
        PurchasePatternOut.of(p) for p in retention_service.purchase_patterns(session, ctx.shop_id, today)
    ]


@router.get("/reactivation/preview", response_model=ReactivationPreviewOut)
def reactivation_preview(
    ctx: Ctx,
    session: ReadSession,
    inactive_days: Annotated[int, Query(ge=1, le=730)] = 60,
    cooldown_days: Annotated[int, Query(ge=0, le=365)] = 30,
    channel: NotificationChannel = NotificationChannel.IN_APP,
) -> ReactivationPreviewOut:
    """Who would be contacted, who is excluded and why. Sends nothing."""
    today = shop_today(get_shop(session, ctx.shop_id))
    return ReactivationPreviewOut.of(
        reactivation_service.preview(
            session,
            ctx.shop_id,
            today,
            inactive_days=inactive_days,
            cooldown_days=cooldown_days,
            channel=channel,
        )
    )


@router.post("/reactivation/draft", response_model=CampaignOut, status_code=201)
def reactivation_draft(payload: ReactivationDraftIn, ctx: Ctx) -> CampaignOut:
    """Turns today's eligible customers into a DRAFT campaign. Launching stays a separate, approved step."""
    with write_transaction() as session:
        today = shop_today(get_shop(session, ctx.shop_id))
        return CampaignOut.of(
            reactivation_service.create_draft(
                session,
                ctx,
                today,
                name=payload.name,
                message_template=payload.message_template,
                inactive_days=payload.inactive_days,
                cooldown_days=payload.cooldown_days,
                channel=payload.channel,
            )
        )
