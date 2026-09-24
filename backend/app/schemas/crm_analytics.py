from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import NotificationChannel
from app.services import crm_dashboard_service, reactivation_service, retention_service


class PurchasePatternOut(BaseModel):
    customer_id: int
    purchase_count: int
    average_interval_days: Decimal | None
    days_since_last_purchase: int | None
    at_risk: bool
    at_risk_reason: str | None
    reactivated: bool
    reactivated_reason: str | None

    @classmethod
    def of(cls, p: retention_service.PurchasePattern) -> "PurchasePatternOut":
        return cls(**p.__dict__)


class RetentionSummaryOut(BaseModel):
    period_days: int
    customers_with_purchases: int
    repeat_customers: int
    repeat_purchase_rate: Decimal | None
    first_time_customers_this_period: int
    returning_customers_this_period: int
    inactive_customer_count: int
    reactivated_count: int
    average_purchase_interval_days: Decimal | None
    cohort_retention_rate: Decimal | str

    @classmethod
    def of(cls, s: retention_service.RetentionSummary) -> "RetentionSummaryOut":
        return cls(**s.__dict__)


class CustomerOverviewOut(BaseModel):
    total_customers: int
    new_customers: int
    active_customers: int
    returning_customers: int
    inactive_customers: int


class RevenueOverviewOut(BaseModel):
    customer_revenue: Decimal
    average_transaction_value: Decimal | None
    repeat_customer_revenue: Decimal


class LoyaltyOverviewOut(BaseModel):
    points_issued: int
    points_redeemed: int
    points_outstanding: int
    program_active: bool


class CampaignOverviewOut(BaseModel):
    total_campaigns: int
    running: int
    completed: int
    draft: int


class ReferralOverviewOut(BaseModel):
    total_referrals: int
    successful_referrals: int
    pending_referrals: int


class CrmDashboardOut(BaseModel):
    period_days: int
    customers: CustomerOverviewOut
    revenue: RevenueOverviewOut
    retention: RetentionSummaryOut
    loyalty: LoyaltyOverviewOut
    campaigns: CampaignOverviewOut
    referrals: ReferralOverviewOut

    @classmethod
    def of(cls, d: crm_dashboard_service.CrmDashboard) -> "CrmDashboardOut":
        return cls(
            period_days=d.period_days,
            customers=CustomerOverviewOut(**d.customers.__dict__),
            revenue=RevenueOverviewOut(**d.revenue.__dict__),
            retention=RetentionSummaryOut.of(d.retention),
            loyalty=LoyaltyOverviewOut(**d.loyalty.__dict__),
            campaigns=CampaignOverviewOut(**d.campaigns.__dict__),
            referrals=ReferralOverviewOut(**d.referrals.__dict__),
        )


class ReactivationRowOut(BaseModel):
    customer_id: int
    name: str
    days_since_last_purchase: int | None
    verdict: str
    reason: str


class ReactivationPreviewOut(BaseModel):
    inactive_days: int
    cooldown_days: int
    channel: NotificationChannel
    eligible_count: int
    excluded_count: int
    rows: list[ReactivationRowOut]

    @classmethod
    def of(cls, p: reactivation_service.ReactivationPreview) -> "ReactivationPreviewOut":
        return cls(
            inactive_days=p.inactive_days, cooldown_days=p.cooldown_days, channel=p.channel,
            eligible_count=len(p.eligible_ids), excluded_count=len(p.rows) - len(p.eligible_ids),
            rows=[ReactivationRowOut(**r.__dict__) for r in p.rows],
        )  # fmt: skip


class ReactivationDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    message_template: str = Field(min_length=1, max_length=4000)
    inactive_days: int = Field(default=60, ge=1, le=730)
    cooldown_days: int = Field(default=30, ge=0, le=365)
    channel: NotificationChannel = NotificationChannel.IN_APP
