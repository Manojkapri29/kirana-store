"""Promotions: one generic discount system for Detailed Sales now, and for online orders and campaigns later.

* `promotions`      what a shop offers. Editing or pausing one never touches a sale that already used it.
* `sale_promotions` what a POSTED sale actually got: a snapshot of the promotion's name, terms, amount and the
                    reason it applied. Invoices read this table, never `promotions`, so history cannot change.

A promotion never changes a product's MRP, selling price or cost: the discount is a separate amount on the
bill (BUSINESS_RULES PR). Money is integer paise; a percentage is stored in basis points (1000 = 10%), so no
floating point is involved anywhere.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import Money, Quantity, UTCDateTime
from app.models.base import (
    Base,
    IdType,
    TimestampMixin,
    enum_type,
    id_column,
    non_negative,
    not_blank,
    positive,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import PromotionAudience, PromotionScope, PromotionStatus, PromotionType

# A promotion has the benefit fields of exactly one type and none of another's.
_TYPE_FIELDS = {
    "PERCENT": ("percent_bp",),
    "AMOUNT": ("amount",),
    "OFFER_PRICE": ("offer_price",),
    "BUY_X_GET_Y": ("buy_quantity", "get_quantity", "get_percent_bp"),
}
_BENEFIT_COLUMNS = tuple(column for columns in _TYPE_FIELDS.values() for column in columns)


def _type_fields_rule() -> str:
    branches = []
    for promo_type, used in _TYPE_FIELDS.items():
        parts = [f"promo_type = '{promo_type}'"]
        parts += [f"{c} IS NOT NULL" if c in used else f"{c} IS NULL" for c in _BENEFIT_COLUMNS]
        branches.append("(" + " AND ".join(parts) + ")")
    return " OR ".join(branches)


class Promotion(TimestampMixin, Base):
    """An offer. `targets` holds the ids it applies to:
    {"product_ids": [], "category_ids": [], "customer_ids": []}.

    Rules of use (docs/BUSINESS_RULES.md, PR):
      * only an ACTIVE promotion inside its date window can apply; expired, paused, draft and future ones
        never do;
      * with a `coupon_code` it applies only when that code is entered; without one it applies automatically;
      * `priority` (higher first) orders promotions; a promotion that is not `stackable` applies only when
        nothing else has and blocks anything after it;
      * `usage_limit` counts posted sales (a voided sale gives its use back), `per_customer_limit` counts a
        customer's posted sales; `max_discount` caps what one sale can get from this promotion.
    """

    __tablename__ = "promotions"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "coupon_code"),  # NULL for automatic promotions; NULLs never collide
        tenant_fk("created_by", "users"),
        Index("ix_promotions_shop_status", "shop_id", "status"),
        not_blank("name"),
        not_blank("coupon_code"),
        CheckConstraint("percent_bp BETWEEN 1 AND 10000", name="percent_bp_range"),
        CheckConstraint("get_percent_bp BETWEEN 1 AND 10000", name="get_percent_bp_range"),
        positive("amount"),
        non_negative("offer_price"),
        positive("buy_quantity"),
        positive("get_quantity"),
        CheckConstraint(_type_fields_rule(), name="benefit_matches_type"),
        CheckConstraint(
            "promo_type NOT IN ('OFFER_PRICE', 'BUY_X_GET_Y') OR scope <> 'CART'", name="type_needs_items"
        ),
        non_negative("min_cart_value"),
        positive("min_quantity"),
        positive("max_discount"),
        positive("usage_limit"),
        positive("per_customer_limit"),
        CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at", name="window_is_ordered"
        ),
    )  # fmt: skip

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    promo_type: Mapped[PromotionType] = mapped_column(enum_type(PromotionType, "promo_type"))
    scope: Mapped[PromotionScope] = mapped_column(enum_type(PromotionScope, "scope"))
    status: Mapped[PromotionStatus] = mapped_column(
        enum_type(PromotionStatus, "status"), default=PromotionStatus.DRAFT
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    stackable: Mapped[bool] = mapped_column(default=False, server_default="0")
    starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    audience: Mapped[PromotionAudience] = mapped_column(
        enum_type(PromotionAudience, "audience"), default=PromotionAudience.ALL
    )
    # Benefit: exactly the fields of `promo_type` are set.
    percent_bp: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[Decimal | None] = mapped_column(Money)
    offer_price: Mapped[Decimal | None] = mapped_column(Money)
    buy_quantity: Mapped[int | None] = mapped_column(Integer)
    get_quantity: Mapped[int | None] = mapped_column(Integer)
    get_percent_bp: Mapped[int | None] = mapped_column(Integer)
    # Conditions.
    min_cart_value: Mapped[Decimal | None] = mapped_column(Money)
    min_quantity: Mapped[Decimal | None] = mapped_column(Quantity)  # of the eligible items, in their units
    max_discount: Mapped[Decimal | None] = mapped_column(Money)
    usage_limit: Mapped[int | None] = mapped_column(Integer)
    per_customer_limit: Mapped[int | None] = mapped_column(Integer)
    targets: Mapped[Any] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(IdType)


class SalePromotion(TimestampMixin, Base):
    """What one promotion gave one sale, frozen at posting."""

    __tablename__ = "sale_promotions"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "sale_id", "promotion_id"),
        tenant_fk("sale_id", "sales"),
        tenant_fk("promotion_id", "promotions"),
        Index("ix_sale_promotions_shop_sale", "shop_id", "sale_id"),
        Index("ix_sale_promotions_shop_promotion", "shop_id", "promotion_id"),
        not_blank("name"),
        positive("discount_amount"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    sale_id: Mapped[int] = mapped_column(IdType)
    promotion_id: Mapped[int] = mapped_column(IdType)
    position: Mapped[int] = mapped_column(Integer)  # the order the promotions were applied in
    name: Mapped[str] = mapped_column(String(120))  # snapshot
    promo_type: Mapped[PromotionType] = mapped_column(enum_type(PromotionType, "promo_type"))  # snapshot
    terms: Mapped[str] = mapped_column(String(200))  # snapshot, e.g. "10% off" or "Buy 2 get 1 free"
    coupon_code: Mapped[str | None] = mapped_column(String(40))  # the code entered, when one was
    discount_amount: Mapped[Decimal] = mapped_column(Money)
    basis: Mapped[str] = mapped_column(String(300))  # why it applied, e.g. "3 eligible items, 450.00"
