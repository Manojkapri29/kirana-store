from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import PromotionAudience, PromotionScope, PromotionStatus, PromotionType, SaleStatus
from app.schemas.common import MoneyIn, Page, PercentIn, QuantityIn
from app.services import promotion_calculation as calc
from app.services import promotion_service
from app.services.promotion_service import AppliedPromotion, PromotionView, UsageRow

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
Coupon = Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]
Ids = Annotated[list[int], Field(max_length=500)]
Count = Annotated[int, Field(ge=1, le=1_000_000)]


class PromotionFields(BaseModel):
    """What a promotion can be told. The benefit fields that apply depend on `promo_type`:
    PERCENT -> percent; AMOUNT -> amount; OFFER_PRICE -> offer_price; BUY_X_GET_Y -> buy_quantity,
    get_quantity and optionally get_percent (default 100, i.e. free). Percentages are text ("12.5")."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    description: Description | None = None
    priority: Annotated[int, Field(ge=-1000, le=1000)] | None = None
    stackable: bool | None = None  # can it be combined with other offers on the same bill?
    starts_at: datetime | None = None  # without a timezone, the shop's own
    ends_at: datetime | None = None
    coupon_code: Coupon | None = None  # set: applies only when this code is entered; unset: automatic
    audience: PromotionAudience | None = None
    percent: PercentIn | None = None
    amount: MoneyIn | None = None
    offer_price: MoneyIn | None = None
    buy_quantity: Count | None = None
    get_quantity: Count | None = None
    get_percent: PercentIn | None = None
    min_cart_value: MoneyIn | None = None
    min_quantity: QuantityIn | None = None
    max_discount: MoneyIn | None = None
    usage_limit: Count | None = None
    per_customer_limit: Count | None = None
    product_ids: Ids | None = None
    category_ids: Ids | None = None
    customer_ids: Ids | None = None


class PromotionCreate(PromotionFields):
    name: Name  # type: ignore[assignment]
    promo_type: PromotionType
    scope: PromotionScope = PromotionScope.CART


class PromotionUpdate(PromotionFields):
    """Only the fields sent are changed. The kind (`promo_type`) and `scope` cannot change."""


class IdName(BaseModel):
    id: int
    name: str


class PromotionOut(BaseModel):
    id: int
    name: str
    description: str | None
    promo_type: PromotionType
    scope: PromotionScope
    status: PromotionStatus  # as stored
    effective_status: PromotionStatus  # as it is now: an ended offer is EXPIRED
    is_live: bool  # active and inside its dates: the only state in which it can apply
    terms: str  # the offer in a few words, e.g. "10% off"
    priority: int
    stackable: bool
    starts_at: datetime | None
    ends_at: datetime | None
    coupon_code: str | None
    audience: PromotionAudience
    percent: Decimal | None
    amount: Decimal | None
    offer_price: Decimal | None
    buy_quantity: int | None
    get_quantity: int | None
    get_percent: Decimal | None
    min_cart_value: Decimal | None
    min_quantity: Decimal | None
    max_discount: Decimal | None
    usage_limit: int | None
    per_customer_limit: int | None
    product_ids: list[int]
    category_ids: list[int]
    customer_ids: list[int]
    products: list[str]
    categories: list[str]
    customers: list[str]
    used_count: int  # posted sales that used it
    discount_given: Decimal
    created_by_name: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_view(cls, view: PromotionView) -> "PromotionOut":
        p = view.promotion
        rule = promotion_service.rule_of(p)
        targets = p.targets if isinstance(p.targets, dict) else {}
        return cls(
            id=p.id,
            name=p.name,
            description=p.description,
            promo_type=p.promo_type,
            scope=p.scope,
            status=p.status,
            effective_status=view.effective_status,
            is_live=view.is_live,
            terms=calc.describe_terms(rule),
            priority=p.priority,
            stackable=p.stackable,
            starts_at=p.starts_at,
            ends_at=p.ends_at,
            coupon_code=p.coupon_code,
            audience=p.audience,
            percent=None if p.percent_bp is None else Decimal(p.percent_bp).scaleb(-2),
            amount=p.amount,
            offer_price=p.offer_price,
            buy_quantity=p.buy_quantity,
            get_quantity=p.get_quantity,
            get_percent=None if p.get_percent_bp is None else Decimal(p.get_percent_bp).scaleb(-2),
            min_cart_value=p.min_cart_value,
            min_quantity=p.min_quantity,
            max_discount=p.max_discount,
            usage_limit=p.usage_limit,
            per_customer_limit=p.per_customer_limit,
            product_ids=list(targets.get("product_ids", [])),
            category_ids=list(targets.get("category_ids", [])),
            customer_ids=list(targets.get("customer_ids", [])),
            products=view.product_names,
            categories=view.category_names,
            customers=view.customer_names,
            used_count=view.used_count,
            discount_given=view.discount_given,
            created_by_name=view.created_by_name,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


class PromotionListOut(Page):
    items: list[PromotionOut]


class AppliedPromotionOut(BaseModel):
    """What one offer gave a bill. On a posted sale this is the frozen snapshot, so it never changes."""

    promotion_id: int
    name: str
    promo_type: PromotionType
    terms: str
    coupon_code: str | None
    amount: Decimal
    basis: str  # why it applied, e.g. "the whole bill, 4 units, eligible amount 450.00"

    @classmethod
    def from_applied(cls, a: AppliedPromotion) -> "AppliedPromotionOut":
        return cls(
            promotion_id=a.promotion_id,
            name=a.name,
            promo_type=a.promo_type,
            terms=a.terms,
            coupon_code=a.coupon_code,
            amount=a.amount,
            basis=a.basis,
        )


class NotAppliedOut(BaseModel):
    promotion_id: int | None
    name: str
    reason: str


class CouponOut(BaseModel):
    code: str
    applied: bool
    message: str


class UsageOut(BaseModel):
    sale_id: int
    invoice_no: str | None
    sale_date: date
    sale_status: SaleStatus  # VOID means the use was given back
    customer_name: str | None
    promotion_id: int
    name: str
    terms: str
    coupon_code: str | None
    discount_amount: Decimal
    basis: str

    @classmethod
    def from_row(cls, row: UsageRow) -> "UsageOut":
        u, s = row.use, row.sale
        return cls(
            sale_id=s.id,
            invoice_no=s.invoice_no,
            sale_date=s.sale_date,
            sale_status=s.status,
            customer_name=row.customer_name,
            promotion_id=u.promotion_id,
            name=u.name,
            terms=u.terms,
            coupon_code=u.coupon_code,
            discount_amount=u.discount_amount,
            basis=u.basis,
        )


class UsageListOut(BaseModel):
    items: list[UsageOut]
