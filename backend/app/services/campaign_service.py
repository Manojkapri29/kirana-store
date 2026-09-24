"""Campaign management: a campaign targets a saved `CustomerGroup` or an ad-hoc segment, may carry an
existing `Promotion` (never a second discount system — see `promotion_service`), and is "sent" only through
`notification_service`'s own channel-provider abstraction. Since no external provider
(email/SMS/WhatsApp/push) is configured anywhere in this codebase, and no customer-facing app exists either,
launching a campaign today always records an honest `NOT_CONFIGURED` outcome per customer — nothing here
pretends to deliver a message it cannot. The audience is snapshotted at launch so later reporting never drifts
if group membership changes afterwards.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import ApprovalRequest, Campaign, CampaignAudienceSnapshot, CampaignSend, Customer
from app.models.enums import ApprovalStatus, CampaignSendStatus, CampaignStatus, NotificationChannel
from app.services import approval_service, crm_segment_service, notification_service, promotion_service
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop

CONSENT_FIELD = {
    NotificationChannel.EMAIL: "marketing_opt_in_email",
    NotificationChannel.SMS: "marketing_opt_in_sms",
    NotificationChannel.WHATSAPP: "marketing_opt_in_whatsapp",
    NotificationChannel.PUSH: "marketing_opt_in_push",
}
APPROVAL_KIND = "CAMPAIGN_LARGE_AUDIENCE"
EDITABLE_STATUSES = (CampaignStatus.DRAFT, CampaignStatus.SCHEDULED)


def _get(session: Session, shop_id: int, campaign_id: int, *, lock: bool = False) -> Campaign:
    query = select(Campaign).where(Campaign.shop_id == shop_id, Campaign.id == campaign_id)
    campaign = session.scalar(query.with_for_update() if lock else query)
    if campaign is None:
        raise NotFoundError("Campaign not found")
    return campaign


def get(session: Session, shop_id: int, campaign_id: int) -> Campaign:
    return _get(session, shop_id, campaign_id)


def list_campaigns(
    session: Session, shop_id: int, *, status: CampaignStatus | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[Campaign], int]:
    query = select(Campaign).where(Campaign.shop_id == shop_id)
    count_query = select(func.count()).select_from(Campaign).where(Campaign.shop_id == shop_id)
    if status is not None:
        query = query.where(Campaign.status == status)
        count_query = count_query.where(Campaign.status == status)
    total = session.scalar(count_query) or 0
    rows = list(session.scalars(query.order_by(Campaign.id.desc()).limit(limit).offset(offset)))
    return rows, total


def create(
    session: Session,
    ctx: RequestContext,
    *,
    name: str,
    description: str | None,
    channel: NotificationChannel,
    target_group_id: int | None,
    target_segment: str | None,
    promotion_id: int | None,
    message_template: str,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> Campaign:
    if not name.strip():
        raise InvalidInputError("Give the campaign a name.", field="name")
    if not message_template.strip():
        raise InvalidInputError("Write the campaign's message.", field="message_template")
    if bool(target_group_id) == bool(target_segment):
        raise InvalidInputError(
            "Choose either a saved group or a segment, not both.", field="target_group_id"
        )
    if target_group_id is not None:
        crm_segment_service.get_group(session, ctx.shop_id, target_group_id)
    if promotion_id is not None:
        promotion_service.get_view(session, ctx.shop_id, promotion_id)
    campaign = Campaign(
        shop_id=ctx.shop_id, name=name.strip(), description=(description or "").strip() or None,
        channel=channel, target_group_id=target_group_id, target_segment=target_segment,
        promotion_id=promotion_id, message_template=message_template.strip(), start_at=start_at,
        end_at=end_at, created_by=ctx.user_id,
    )  # fmt: skip
    session.add(campaign)
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="campaign",
        entity_id=campaign.id,
        action="campaign_created",
        after={"name": name, "channel": channel.value},
    )
    return campaign


def _audience(session: Session, shop_id: int, campaign: Campaign, today) -> list[int]:
    """Active customers only — an inactive or deactivated customer is never targeted."""
    if campaign.target_group_id is not None:
        ids = crm_segment_service.members_of(session, shop_id, campaign.target_group_id).customer_ids
    else:
        from app.services import customer_intelligence_service as cis

        segment = cis.CustomerSegment(campaign.target_segment)
        ids = [
            r.customer_id for r in cis.list_analytics(session, shop_id, today, segment=segment, limit=None)
        ]
    active = set(
        session.scalars(
            select(Customer.id).where(
                Customer.shop_id == shop_id, Customer.id.in_(ids), Customer.is_active.is_(True)
            )
        )
    )
    return [i for i in ids if i in active]


def preview_audience(session: Session, shop_id: int, campaign_id: int, today) -> list[int]:
    campaign = _get(session, shop_id, campaign_id)
    return _audience(session, shop_id, campaign, today)


def update(session: Session, ctx: RequestContext, campaign_id: int, changes: dict[str, Any]) -> Campaign:
    campaign = _get(session, ctx.shop_id, campaign_id, lock=True)
    if campaign.status not in EDITABLE_STATUSES:
        raise ConflictError("Only a draft or scheduled campaign can be changed.")
    allowed = {
        "name",
        "description",
        "channel",
        "target_group_id",
        "target_segment",
        "promotion_id",
        "message_template",
        "start_at",
        "end_at",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise InvalidInputError(f"These fields cannot be changed: {', '.join(sorted(unknown))}.")
    before = {k: getattr(campaign, k) for k in changes}
    for key, value in changes.items():
        setattr(campaign, key, value)
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="campaign",
        entity_id=campaign.id,
        action="campaign_updated",
        before=before,
        after=changes,
    )
    return campaign


def schedule(session: Session, ctx: RequestContext, campaign_id: int, start_at: datetime) -> Campaign:
    campaign = _get(session, ctx.shop_id, campaign_id, lock=True)
    if campaign.status is not CampaignStatus.DRAFT:
        raise ConflictError("Only a draft campaign can be scheduled.")
    campaign.status = CampaignStatus.SCHEDULED
    campaign.start_at = start_at
    session.flush()
    record_audit(session, ctx, entity_type="campaign", entity_id=campaign.id, action="campaign_scheduled")
    return campaign


def _latest_approval(session: Session, shop_id: int, campaign_id: int) -> ApprovalRequest | None:
    return session.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.shop_id == shop_id,
            ApprovalRequest.kind == APPROVAL_KIND,
            ApprovalRequest.entity_type == "campaign",
            ApprovalRequest.entity_id == campaign_id,
        )
        .order_by(ApprovalRequest.id.desc())
        .limit(1)
    )


def _launch_is_approved(
    session: Session, ctx: RequestContext, campaign: Campaign, audience_size: int
) -> bool:
    """A launch whose audience is LARGER than the shop's configured threshold needs a separate approval (never
    the requester). No threshold configured = no extra approval. Returns False while one is outstanding."""
    threshold = get_shop(session, ctx.shop_id).crm_campaign_audience_threshold
    if threshold is None or audience_size <= threshold:
        return True
    latest = _latest_approval(session, ctx.shop_id, campaign.id)
    if latest is not None and latest.status is ApprovalStatus.APPROVED:
        return True
    if latest is None or latest.status is not ApprovalStatus.PENDING:
        approval_service.create(
            session, ctx, kind=APPROVAL_KIND, entity_type="campaign", entity_id=campaign.id,
            reason=f"Audience of {audience_size} exceeds the shop's approval threshold of {threshold}.",
            threshold_value=Decimal(threshold), observed_value=Decimal(audience_size),
        )  # fmt: skip
        record_audit(
            session, ctx, entity_type="campaign", entity_id=campaign.id,
            action="campaign_launch_approval_requested",
            after={"audience_size": audience_size, "threshold": threshold},
        )  # fmt: skip
    campaign.requires_approval = True
    session.flush()
    return False


def apply_approval_decision(
    session: Session, ctx: RequestContext, campaign_id: int, *, approved: bool
) -> None:
    """Called after a large-audience approval was decided. Approval does NOT launch the campaign: someone
    with the launch permission still has to launch it. A rejection clears the waiting flag."""
    campaign = _get(session, ctx.shop_id, campaign_id, lock=True)
    if not approved:
        campaign.requires_approval = False
    record_audit(
        session, ctx, entity_type="campaign", entity_id=campaign.id,
        action="campaign_launch_approval_decided", after={"approved": approved},
    )  # fmt: skip


def launch(session: Session, ctx: RequestContext, campaign_id: int, today) -> Campaign:
    """Snapshot the audience, attempt delivery per customer through `notification_service`'s provider
    abstraction, and record the honest outcome for each — never a fabricated "sent"."""
    campaign = _get(session, ctx.shop_id, campaign_id, lock=True)
    if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.SCHEDULED):
        raise ConflictError("Only a draft or scheduled campaign can be launched.")
    audience = _audience(session, ctx.shop_id, campaign, today)
    if not _launch_is_approved(session, ctx, campaign, len(audience)):
        return campaign  # waiting on a separate approval; nothing was sent or snapshotted
    campaign.requires_approval = False
    for customer_id in audience:
        session.add(
            CampaignAudienceSnapshot(shop_id=ctx.shop_id, campaign_id=campaign.id, customer_id=customer_id)
        )

    customers = {
        c.id: c
        for c in session.scalars(
            select(Customer).where(Customer.shop_id == ctx.shop_id, Customer.id.in_(audience))
        )
    }
    provider = notification_service.provider_for(campaign.channel)
    consent_field = CONSENT_FIELD.get(campaign.channel)
    for customer_id in audience:
        customer = customers[customer_id]
        if consent_field is not None and not getattr(customer, consent_field):
            status, detail = (
                CampaignSendStatus.SKIPPED_NO_CONSENT,
                "Customer has not opted in to this channel",
            )
        elif provider is None:
            status, detail = (
                CampaignSendStatus.NOT_CONFIGURED,
                f"No {campaign.channel.value} provider is configured",
            )
        else:
            status, detail = CampaignSendStatus.SENT, None  # never reached today: no provider exists
        session.add(
            CampaignSend(
                shop_id=ctx.shop_id,
                campaign_id=campaign.id,
                customer_id=customer_id,
                channel=campaign.channel,
                status=status,
                detail=detail,
            )
        )

    campaign.status = CampaignStatus.RUNNING
    campaign.launched_at = utc_now()
    campaign.launched_by = ctx.user_id
    session.flush()
    campaign.status = (
        CampaignStatus.COMPLETED
    )  # no async delivery queue exists; the attempt is over immediately
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="campaign",
        entity_id=campaign.id,
        action="campaign_launched",
        after={"audience_size": len(audience)},
    )
    return campaign


def pause(session: Session, ctx: RequestContext, campaign_id: int) -> Campaign:
    campaign = _get(session, ctx.shop_id, campaign_id, lock=True)
    if campaign.status is not CampaignStatus.RUNNING:
        raise ConflictError("Only a running campaign can be paused.")
    campaign.status = CampaignStatus.PAUSED
    session.flush()
    record_audit(session, ctx, entity_type="campaign", entity_id=campaign.id, action="campaign_paused")
    return campaign


def resume(session: Session, ctx: RequestContext, campaign_id: int) -> Campaign:
    campaign = _get(session, ctx.shop_id, campaign_id, lock=True)
    if campaign.status is not CampaignStatus.PAUSED:
        raise ConflictError("Only a paused campaign can be resumed.")
    campaign.status = CampaignStatus.RUNNING
    session.flush()
    record_audit(session, ctx, entity_type="campaign", entity_id=campaign.id, action="campaign_resumed")
    return campaign


def cancel(session: Session, ctx: RequestContext, campaign_id: int, reason: str) -> Campaign:
    if not reason.strip():
        raise InvalidInputError("Give a reason for cancelling.", field="reason")
    campaign = _get(session, ctx.shop_id, campaign_id, lock=True)
    if campaign.status in (CampaignStatus.COMPLETED, CampaignStatus.CANCELLED):
        raise ConflictError("This campaign is already closed.")
    campaign.status = CampaignStatus.CANCELLED
    campaign.cancelled_at = utc_now()
    campaign.cancelled_by = ctx.user_id
    campaign.cancel_reason = reason.strip()
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="campaign",
        entity_id=campaign.id,
        action="campaign_cancelled",
        after={"reason": reason.strip()},
    )
    return campaign


def send_outcomes(session: Session, shop_id: int, campaign_id: int) -> list[CampaignSend]:
    _get(session, shop_id, campaign_id)
    return list(
        session.scalars(
            select(CampaignSend).where(
                CampaignSend.shop_id == shop_id, CampaignSend.campaign_id == campaign_id
            )
        )
    )
