from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import CustomerGroup, CustomerNote
from app.models.enums import CustomerGroupKind, CustomerSource, CustomerType, NotificationChannel
from app.schemas.common import Page
from app.services import crm_service
from app.services.customer_intelligence_service import CustomerAnalytics, CustomerSegment

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ClassificationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_type: CustomerType | None = None
    source: CustomerSource | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    preferred_contact_channel: NotificationChannel | None = None
    marketing_opt_in_email: bool | None = None
    marketing_opt_in_sms: bool | None = None
    marketing_opt_in_whatsapp: bool | None = None
    marketing_opt_in_push: bool | None = None


class ApprovalSettingsIO(BaseModel):
    """None = that extra approval is off. There is no built-in number."""

    model_config = ConfigDict(extra="forbid")

    campaign_audience_threshold: int | None = Field(default=None, ge=0)
    loyalty_adjustment_threshold: int | None = Field(default=None, ge=0)


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: Body


class NoteOut(BaseModel):
    id: int
    customer_id: int
    user_id: int
    body: str
    created_at: datetime

    @classmethod
    def of(cls, n: CustomerNote) -> "NoteOut":
        return cls(
            id=n.id, customer_id=n.customer_id, user_id=n.user_id, body=n.body, created_at=n.created_at
        )


class TimelineEventOut(BaseModel):
    kind: str
    occurred_at: datetime
    title: str
    detail: str
    amount: Decimal | None
    reference_type: str | None
    reference_id: int | None

    @classmethod
    def of(cls, e: crm_service.TimelineEvent) -> "TimelineEventOut":
        return cls(
            kind=e.kind, occurred_at=e.occurred_at, title=e.title, detail=e.detail,
            amount=e.amount, reference_type=e.reference_type, reference_id=e.reference_id,
        )  # fmt: skip


class CustomerAnalyticsOut(BaseModel):
    customer_id: int
    name: str
    phone: str | None
    is_active: bool
    detailed_sale_count: int
    quick_sale_count: int
    total_purchases: Decimal
    average_transaction_value: Decimal | None
    first_purchase: date | None
    last_purchase: date | None
    outstanding: Decimal
    advance: Decimal
    last_payment_date: date | None
    days_since_last_purchase: int | None
    days_since_last_payment: int | None
    segments: list[CustomerSegment]
    online_order_count: int

    @classmethod
    def of(cls, a: CustomerAnalytics) -> "CustomerAnalyticsOut":
        return cls(**a.__dict__)


class CustomerProfileOut(BaseModel):
    customer_id: int
    name: str
    phone: str | None
    email: str | None
    customer_type: CustomerType | None
    source: CustomerSource | None
    tags: list[str]
    is_active: bool
    preferred_contact_channel: str | None
    marketing_opt_in_email: bool
    marketing_opt_in_sms: bool
    marketing_opt_in_whatsapp: bool
    marketing_opt_in_push: bool
    analytics: CustomerAnalyticsOut
    loyalty_balance: int
    loyalty_program_active: bool
    referrals_made: int
    referral_code: str | None

    @classmethod
    def of(cls, p: crm_service.CustomerProfile) -> "CustomerProfileOut":
        c = p.customer
        return cls(
            customer_id=c.id, name=c.name, phone=c.phone, email=c.email, customer_type=c.customer_type,
            source=c.source, tags=c.tags, is_active=c.is_active,
            preferred_contact_channel=(
                c.preferred_contact_channel.value if c.preferred_contact_channel else None
            ),
            marketing_opt_in_email=c.marketing_opt_in_email,
            marketing_opt_in_sms=c.marketing_opt_in_sms,
            marketing_opt_in_whatsapp=c.marketing_opt_in_whatsapp,
            marketing_opt_in_push=c.marketing_opt_in_push,
            analytics=CustomerAnalyticsOut.of(p.analytics),
            loyalty_balance=p.loyalty_balance, loyalty_program_active=p.loyalty_program_active,
            referrals_made=p.referrals_made, referral_code=p.referral_code,
        )  # fmt: skip


class RuleIn(BaseModel):
    """A segmentation rule: see `crm_segment_service.RULE_KEYS` for every accepted filter."""

    model_config = ConfigDict(extra="allow")


class CreateManualGroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    customer_ids: list[int] = Field(default_factory=list, max_length=5000)


class CreateRuleGroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    rule: dict[str, Any] = Field(default_factory=dict)


class SetMembersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_ids: list[int] = Field(default_factory=list, max_length=5000)


class CustomerGroupOut(BaseModel):
    id: int
    name: str
    kind: CustomerGroupKind
    rule: dict[str, Any] | None
    last_recalculated_at: datetime | None
    created_by: int
    created_at: datetime
    member_count: int | None = None

    @classmethod
    def of(cls, g: CustomerGroup, *, member_count: int | None = None) -> "CustomerGroupOut":
        return cls(
            id=g.id, name=g.name, kind=g.kind, rule=g.rule, last_recalculated_at=g.last_recalculated_at,
            created_by=g.created_by, created_at=g.created_at, member_count=member_count,
        )  # fmt: skip


class CustomerGroupListOut(BaseModel):
    items: list[CustomerGroupOut]


class CustomerAnalyticsListOut(Page):
    items: list[CustomerAnalyticsOut]
