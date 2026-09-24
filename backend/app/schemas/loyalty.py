from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models import LoyaltyProgram
from app.models.enums import LoyaltyEntryType
from app.schemas.common import MoneyIn
from app.services.loyalty_service import LedgerEntryView


class ProgramIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool
    points_per_amount: MoneyIn
    min_transaction_amount: MoneyIn = Decimal("0")
    redemption_value: MoneyIn
    min_redemption_points: int | None = Field(default=None, gt=0)
    max_redeem_points_per_txn: int | None = Field(default=None, gt=0)
    points_expiry_days: int | None = Field(default=None, gt=0)


class ProgramOut(BaseModel):
    id: int
    is_active: bool
    points_per_amount: Decimal
    min_transaction_amount: Decimal
    redemption_value: Decimal
    min_redemption_points: int | None
    max_redeem_points_per_txn: int | None
    points_expiry_days: int | None

    @classmethod
    def of(cls, p: LoyaltyProgram) -> "ProgramOut":
        return cls(
            id=p.id, is_active=p.is_active, points_per_amount=p.points_per_amount,
            min_transaction_amount=p.min_transaction_amount, redemption_value=p.redemption_value,
            min_redemption_points=p.min_redemption_points,
            max_redeem_points_per_txn=p.max_redeem_points_per_txn,
            points_expiry_days=p.points_expiry_days,
        )  # fmt: skip


class RedeemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=300)


class AdjustIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points_delta: int
    note: str = Field(min_length=1, max_length=300)
    # Only for an adjustment above the shop's approval threshold: the id of the approved request to redeem.
    approval_request_id: int | None = None


class AdjustPendingOut(BaseModel):
    """Returned (202) instead of a ledger entry when the adjustment is waiting on a separate approval."""

    status: str = "APPROVAL_REQUIRED"
    approval_request_id: int


class LedgerEntryOut(BaseModel):
    id: int
    customer_id: int
    entry_type: LoyaltyEntryType
    points_delta: int
    reference_type: str
    reference_id: int | None
    note: str | None
    entry_date: date
    created_at: datetime

    @classmethod
    def of(cls, e: LedgerEntryView) -> "LedgerEntryOut":
        return cls(
            id=e.id, customer_id=e.customer_id, entry_type=e.entry_type, points_delta=e.points_delta,
            reference_type=e.reference_type, reference_id=e.reference_id, note=e.note,
            entry_date=e.entry_date, created_at=e.created_at,
        )  # fmt: skip


class LedgerListOut(BaseModel):
    items: list[LedgerEntryOut]
    total: int
    balance: int


class PointsSummaryOut(BaseModel):
    points_issued: int
    points_redeemed: int
    points_outstanding: int


class ExpiryOut(BaseModel):
    customers_expired: int
    points_expired: int
