"""Campaign management. See `campaign_service` for every rule (audience calculation, the honest per-channel
send outcome); this router only parses requests, opens the write transaction, and shapes the response."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.models.enums import CampaignStatus
from app.schemas.campaigns import (
    AudiencePreviewOut,
    CampaignCreateIn,
    CampaignListOut,
    CampaignOut,
    CampaignUpdateIn,
    CancelIn,
    ScheduleIn,
    SendOutcomeOut,
    SendOutcomesOut,
)
from app.services import campaign_service
from app.services.shop_service import get_shop, shop_today

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
ReadSession = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("", response_model=CampaignListOut)
def list_campaigns(
    ctx: Ctx,
    session: ReadSession,
    status: CampaignStatus | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> CampaignListOut:
    rows, total = campaign_service.list_campaigns(
        session, ctx.shop_id, status=status, limit=limit, offset=offset
    )
    return CampaignListOut(items=[CampaignOut.of(c) for c in rows], total=total, limit=limit, offset=offset)


@router.post("", response_model=CampaignOut, status_code=201)
def create_campaign(payload: CampaignCreateIn, ctx: Ctx) -> CampaignOut:
    with write_transaction() as session:
        campaign = campaign_service.create(
            session, ctx, name=payload.name, description=payload.description, channel=payload.channel,
            target_group_id=payload.target_group_id, target_segment=payload.target_segment,
            promotion_id=payload.promotion_id, message_template=payload.message_template,
            start_at=payload.start_at, end_at=payload.end_at,
        )  # fmt: skip
        return CampaignOut.of(campaign)


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: int, ctx: Ctx, session: ReadSession) -> CampaignOut:
    return CampaignOut.of(campaign_service.get(session, ctx.shop_id, campaign_id))


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: int, payload: CampaignUpdateIn, ctx: Ctx) -> CampaignOut:
    with write_transaction() as session:
        changes = payload.model_dump(exclude_unset=True)
        return CampaignOut.of(campaign_service.update(session, ctx, campaign_id, changes))


@router.get("/{campaign_id}/audience", response_model=AudiencePreviewOut)
def preview_audience(campaign_id: int, ctx: Ctx, session: ReadSession) -> AudiencePreviewOut:
    today = shop_today(get_shop(session, ctx.shop_id))
    ids = campaign_service.preview_audience(session, ctx.shop_id, campaign_id, today)
    return AudiencePreviewOut(customer_ids=ids, audience_size=len(ids))


@router.post("/{campaign_id}/schedule", response_model=CampaignOut)
def schedule_campaign(campaign_id: int, payload: ScheduleIn, ctx: Ctx) -> CampaignOut:
    with write_transaction() as session:
        return CampaignOut.of(campaign_service.schedule(session, ctx, campaign_id, payload.start_at))


@router.post("/{campaign_id}/launch", response_model=CampaignOut)
def launch_campaign(campaign_id: int, ctx: Ctx) -> CampaignOut:
    with write_transaction() as session:
        today = shop_today(get_shop(session, ctx.shop_id))
        return CampaignOut.of(campaign_service.launch(session, ctx, campaign_id, today))


@router.post("/{campaign_id}/pause", response_model=CampaignOut)
def pause_campaign(campaign_id: int, ctx: Ctx) -> CampaignOut:
    with write_transaction() as session:
        return CampaignOut.of(campaign_service.pause(session, ctx, campaign_id))


@router.post("/{campaign_id}/resume", response_model=CampaignOut)
def resume_campaign(campaign_id: int, ctx: Ctx) -> CampaignOut:
    with write_transaction() as session:
        return CampaignOut.of(campaign_service.resume(session, ctx, campaign_id))


@router.post("/{campaign_id}/cancel", response_model=CampaignOut)
def cancel_campaign(campaign_id: int, payload: CancelIn, ctx: Ctx) -> CampaignOut:
    with write_transaction() as session:
        return CampaignOut.of(campaign_service.cancel(session, ctx, campaign_id, payload.reason))


@router.get("/{campaign_id}/sends", response_model=SendOutcomesOut)
def send_outcomes(campaign_id: int, ctx: Ctx, session: ReadSession) -> SendOutcomesOut:
    return SendOutcomesOut(
        items=[
            SendOutcomeOut.of(s) for s in campaign_service.send_outcomes(session, ctx.shop_id, campaign_id)
        ]
    )
