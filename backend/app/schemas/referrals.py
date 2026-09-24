from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models import ReferralCode, ReferralEvent, ReferralProgram
from app.models.enums import ReferralEventStatus
from app.schemas.common import MoneyIn


class ProgramIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool
    referrer_reward_points: int | None = Field(default=None, gt=0)
    referred_reward_points: int | None = Field(default=None, gt=0)
    min_purchase_amount: MoneyIn | None = None
    max_referrals_per_customer: int | None = Field(default=None, gt=0)
    expiry_days: int | None = Field(default=None, gt=0)


class ProgramOut(BaseModel):
    id: int
    is_active: bool
    referrer_reward_points: int | None
    referred_reward_points: int | None
    min_purchase_amount: Decimal | None
    max_referrals_per_customer: int | None
    expiry_days: int | None

    @classmethod
    def of(cls, p: ReferralProgram) -> "ProgramOut":
        return cls(
            id=p.id, is_active=p.is_active, referrer_reward_points=p.referrer_reward_points,
            referred_reward_points=p.referred_reward_points, min_purchase_amount=p.min_purchase_amount,
            max_referrals_per_customer=p.max_referrals_per_customer, expiry_days=p.expiry_days,
        )  # fmt: skip


class CodeOut(BaseModel):
    id: int
    customer_id: int
    code: str
    is_active: bool

    @classmethod
    def of(cls, c: ReferralCode) -> "CodeOut":
        return cls(id=c.id, customer_id=c.customer_id, code=c.code, is_active=c.is_active)


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=20)
    referred_customer_id: int


class EventOut(BaseModel):
    id: int
    referral_code_id: int
    referrer_customer_id: int
    referred_customer_id: int
    status: ReferralEventStatus
    qualifying_reference_type: str | None
    qualifying_reference_id: int | None
    qualified_at: datetime | None
    rewarded_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, e: ReferralEvent) -> "EventOut":
        return cls(
            id=e.id, referral_code_id=e.referral_code_id, referrer_customer_id=e.referrer_customer_id,
            referred_customer_id=e.referred_customer_id, status=e.status,
            qualifying_reference_type=e.qualifying_reference_type,
            qualifying_reference_id=e.qualifying_reference_id, qualified_at=e.qualified_at,
            rewarded_at=e.rewarded_at, created_at=e.created_at,
        )  # fmt: skip


class EventListOut(BaseModel):
    items: list[EventOut]
