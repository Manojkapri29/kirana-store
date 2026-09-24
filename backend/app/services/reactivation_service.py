"""Reactivation workflow: find lapsed customers, PREVIEW who would be contacted and why, and turn the
eligible ones into a DRAFT campaign. Nothing here sends anything: the draft goes through the normal campaign
lifecycle, where launching needs its own permission (and, above the shop's configured audience size, a second
person's approval).

Who is excluded, and why it is stated in the preview rather than silently dropped:
* no marketing consent for the chosen channel (an opt-out, or never opted in — such a customer is never
  contacted here; transactional messages are unaffected because they use a separate path);
* already contacted by a campaign within `cooldown_days` (a frequency limit, so nobody is nagged);
* deactivated customers (never in the candidate pool at all — see `retention_service`).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import Campaign, CampaignSend, Customer
from app.models.enums import CampaignSendStatus, NotificationChannel
from app.services import campaign_service, crm_segment_service, retention_service
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError

Verdict = Literal["ELIGIBLE", "NO_CONSENT", "IN_COOLDOWN"]
CONSENT_FIELD = campaign_service.CONSENT_FIELD
_NOT_A_CONTACT = (CampaignSendStatus.SKIPPED_NO_CONSENT, CampaignSendStatus.SKIPPED_OPTED_OUT)


@dataclass(frozen=True)
class ReactivationRow:
    customer_id: int
    name: str
    days_since_last_purchase: int | None
    verdict: Verdict
    reason: str


@dataclass(frozen=True)
class ReactivationPreview:
    inactive_days: int
    cooldown_days: int
    channel: NotificationChannel
    rows: list[ReactivationRow]

    @property
    def eligible_ids(self) -> list[int]:
        return [r.customer_id for r in self.rows if r.verdict == "ELIGIBLE"]


def preview(
    session: Session, shop_id: int, today: date, *, inactive_days: int, cooldown_days: int,
    channel: NotificationChannel,
) -> ReactivationPreview:  # fmt: skip
    if inactive_days < 1:
        raise InvalidInputError("Inactive days must be at least 1.", field="inactive_days")
    if cooldown_days < 0:
        raise InvalidInputError("Cooldown days cannot be negative.", field="cooldown_days")
    candidate_ids = retention_service.reactivation_candidates(
        session, shop_id, today, inactive_days=inactive_days
    )
    if not candidate_ids:
        return ReactivationPreview(inactive_days, cooldown_days, channel, [])
    customers = {
        c.id: c
        for c in session.scalars(
            select(Customer).where(Customer.shop_id == shop_id, Customer.id.in_(candidate_ids))
        )
    }
    patterns = {p.customer_id: p for p in retention_service.purchase_patterns(session, shop_id, today)}
    cutoff = today - timedelta(days=cooldown_days)
    last_contact = dict(
        session.execute(
            select(CampaignSend.customer_id, func.max(CampaignSend.created_at))
            .where(
                CampaignSend.shop_id == shop_id,
                CampaignSend.customer_id.in_(candidate_ids),
                CampaignSend.status.notin_(_NOT_A_CONTACT),
            )
            .group_by(CampaignSend.customer_id)
        ).all()
    )
    consent_field = CONSENT_FIELD.get(channel)
    rows = []
    for customer_id in sorted(candidate_ids):
        customer = customers[customer_id]
        pattern = patterns.get(customer_id)
        days = pattern.days_since_last_purchase if pattern else None
        interval = pattern.average_interval_days if pattern else None
        why = f"No purchase for {days} days" + (
            f"; typical interval {interval} days." if interval is not None else "."
        )
        contacted_at = last_contact.get(customer_id)
        if consent_field is not None and not getattr(customer, consent_field):
            verdict, reason = "NO_CONSENT", f"Excluded: has not opted in to marketing on {channel.value}."
        elif cooldown_days > 0 and contacted_at is not None and contacted_at.date() > cutoff:
            verdict, reason = (
                "IN_COOLDOWN",
                f"Excluded: contacted on {contacted_at.date()}, inside the cooldown.",
            )
        else:
            verdict, reason = "ELIGIBLE", why
        rows.append(ReactivationRow(customer_id, customer.name, days, verdict, reason))
    return ReactivationPreview(inactive_days, cooldown_days, channel, rows)


def create_draft(
    session: Session, ctx: RequestContext, today: date, *, name: str, message_template: str,
    inactive_days: int, cooldown_days: int, channel: NotificationChannel,
) -> Campaign:  # fmt: skip
    """Snapshot today's ELIGIBLE customers into a manual group and start a DRAFT campaign for them. Nothing is
    sent; launching is a separate, separately-permissioned step."""
    result = preview(
        session, ctx.shop_id, today, inactive_days=inactive_days, cooldown_days=cooldown_days, channel=channel
    )
    if not result.eligible_ids:
        raise ConflictError("No customers are eligible for reactivation right now.")
    group = crm_segment_service.create_manual_group(
        session, ctx, name=f"{name} — eligible on {today.isoformat()}"[:150], customer_ids=result.eligible_ids
    )
    campaign = campaign_service.create(
        session, ctx, name=name, description=f"Reactivation: no purchase for over {inactive_days} days.",
        channel=channel, target_group_id=group.id, target_segment=None, promotion_id=None,
        message_template=message_template,
    )  # fmt: skip
    record_audit(
        session, ctx, entity_type="campaign", entity_id=campaign.id, action="reactivation_draft_created",
        after={"eligible": len(result.eligible_ids), "excluded": len(result.rows) - len(result.eligible_ids)},
    )  # fmt: skip
    return campaign
