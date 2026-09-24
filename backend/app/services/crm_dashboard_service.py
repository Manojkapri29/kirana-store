"""The CRM dashboard: one call combining every number from the services that already compute it. Nothing is
calculated here that isn't already calculated somewhere else — this module only assembles and never
duplicates the underlying logic (`customer_intelligence_service`, `retention_service`, `loyalty_service`,
`campaign_service`, `referral_service`).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Campaign, ReferralEvent
from app.models.enums import CampaignStatus, ReferralEventStatus
from app.services import customer_intelligence_service as cis
from app.services import loyalty_service, retention_service

ZERO = Decimal("0")


@dataclass(frozen=True)
class CustomerOverview:
    total_customers: int
    new_customers: int
    active_customers: int
    returning_customers: int
    inactive_customers: int


@dataclass(frozen=True)
class RevenueOverview:
    customer_revenue: Decimal
    average_transaction_value: Decimal | None
    repeat_customer_revenue: Decimal


@dataclass(frozen=True)
class LoyaltyOverview:
    points_issued: int
    points_redeemed: int
    points_outstanding: int
    program_active: bool


@dataclass(frozen=True)
class CampaignOverview:
    total_campaigns: int
    running: int
    completed: int
    draft: int


@dataclass(frozen=True)
class ReferralOverview:
    total_referrals: int
    successful_referrals: int  # REWARDED
    pending_referrals: int


@dataclass(frozen=True)
class CrmDashboard:
    period_days: int
    customers: CustomerOverview
    revenue: RevenueOverview
    retention: retention_service.RetentionSummary
    loyalty: LoyaltyOverview
    campaigns: CampaignOverview
    referrals: ReferralOverview


def build(session: Session, shop_id: int, today: date, *, period_days: int = 90) -> CrmDashboard:
    rows = cis.list_analytics(session, shop_id, today, active_days=period_days, limit=None)
    total = len(rows)
    new_count = sum(1 for r in rows if cis.CustomerSegment.NEW in r.segments)
    active_count = sum(1 for r in rows if cis.CustomerSegment.ACTIVE in r.segments)
    returning_count = sum(
        1
        for r in rows
        if cis.CustomerSegment.ACTIVE in r.segments and r.detailed_sale_count + r.quick_sale_count > 1
    )
    inactive_count = sum(1 for r in rows if cis.CustomerSegment.INACTIVE in r.segments)

    customer_revenue = sum((r.total_purchases for r in rows), ZERO)
    with_transactions = [r for r in rows if r.average_transaction_value is not None]
    avg_txn = (
        (
            sum((r.average_transaction_value for r in with_transactions), ZERO) / len(with_transactions)
        ).quantize(Decimal("0.01"))
        if with_transactions
        else None
    )
    repeat_revenue = sum(
        (r.total_purchases for r in rows if (r.detailed_sale_count + r.quick_sale_count) > 1), ZERO
    )

    retention = retention_service.retention_summary(session, shop_id, today, period_days=period_days)

    program = loyalty_service.get_program(session, shop_id)
    points = loyalty_service.points_summary(session, shop_id)

    campaign_counts = dict(
        session.execute(
            select(Campaign.status, func.count()).where(Campaign.shop_id == shop_id).group_by(Campaign.status)
        ).all()
    )  # fmt: skip
    total_campaigns = sum(campaign_counts.values())

    referral_counts = dict(
        session.execute(
            select(ReferralEvent.status, func.count())
            .where(ReferralEvent.shop_id == shop_id)
            .group_by(ReferralEvent.status)
        ).all()
    )

    return CrmDashboard(
        period_days=period_days,
        customers=CustomerOverview(total, new_count, active_count, returning_count, inactive_count),
        revenue=RevenueOverview(customer_revenue, avg_txn, repeat_revenue),
        retention=retention,
        loyalty=LoyaltyOverview(
            points["points_issued"],
            points["points_redeemed"],
            points["points_outstanding"],
            bool(program and program.is_active),
        ),  # fmt: skip
        campaigns=CampaignOverview(
            total_campaigns,
            campaign_counts.get(CampaignStatus.RUNNING, 0),
            campaign_counts.get(CampaignStatus.COMPLETED, 0),
            campaign_counts.get(CampaignStatus.DRAFT, 0),
        ),
        referrals=ReferralOverview(
            sum(referral_counts.values()),
            referral_counts.get(ReferralEventStatus.REWARDED, 0),
            referral_counts.get(ReferralEventStatus.PENDING, 0),
        ),
    )
