from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.services.sales_report_service import (
    NOT_AVAILABLE,
    DiscountReport,
    SalesSummary,
    Totals,
)


class TotalsOut(BaseModel):
    """Gross - discount = net. `discount` is the sum of its three parts."""

    sales_count: int
    gross_sales: Decimal
    line_discount: Decimal
    bill_discount: Decimal  # a Quick Sale's one discount is counted here
    promotion_discount: Decimal
    discount: Decimal
    net_sales: Decimal

    @classmethod
    def of(cls, t: Totals) -> "TotalsOut":
        return cls(
            sales_count=t.sales_count,
            gross_sales=t.gross,
            line_discount=t.line_discount,
            bill_discount=t.bill_discount,
            promotion_discount=t.promotion_discount,
            discount=t.discount,
            net_sales=t.net,
        )


class DayOut(BaseModel):
    day: date
    detailed: TotalsOut
    quick: TotalsOut
    combined: TotalsOut


class SalesSummaryOut(BaseModel):
    date_from: date
    date_to: date
    detailed: TotalsOut
    quick: TotalsOut
    combined: TotalsOut
    # Profit is only known for Detailed Sales. A Quick Sale has no product and no cost, so the combined
    # figures have no profit.
    detailed_gross_profit: Decimal | None
    detailed_sales_without_cost: int
    # Sales returns dated in the period (BUSINESS_RULES R5): the profit above already has them taken off.
    returns_count: int
    returns_total: Decimal
    net_after_returns: Decimal
    returns_without_cost: int
    combined_profit: None = None
    combined_profit_label: str = NOT_AVAILABLE
    days: list[DayOut]

    @classmethod
    def from_summary(cls, s: SalesSummary) -> "SalesSummaryOut":
        return cls(
            date_from=s.date_from,
            date_to=s.date_to,
            detailed=TotalsOut.of(s.detailed),
            quick=TotalsOut.of(s.quick),
            combined=TotalsOut.of(s.combined),
            detailed_gross_profit=s.detailed_gross_profit,
            detailed_sales_without_cost=s.detailed_sales_without_cost,
            returns_count=s.returns_count,
            returns_total=s.returns_total,
            net_after_returns=s.net_after_returns,
            returns_without_cost=s.returns_without_cost,
            days=[
                DayOut(
                    day=d.day,
                    detailed=TotalsOut.of(d.detailed),
                    quick=TotalsOut.of(d.quick),
                    combined=TotalsOut.of(d.combined),
                )
                for d in s.days
            ],
        )


class PromotionUseOut(BaseModel):
    promotion_id: int
    name: str
    uses: int
    discount: Decimal


class CouponUseOut(BaseModel):
    code: str
    uses: int
    discount: Decimal


class DiscountDayOut(BaseModel):
    day: date
    line_discount: Decimal
    bill_discount: Decimal
    promotion_discount: Decimal
    total: Decimal


class DiscountReportOut(BaseModel):
    date_from: date
    date_to: date
    gross_sales: Decimal
    net_sales: Decimal
    total_discount: Decimal
    line_discount: Decimal
    bill_discount: Decimal
    promotion_discount: Decimal
    promotions_used: int
    promotion_applications: int
    coupon_uses: int
    coupon_discount: Decimal
    by_promotion: list[PromotionUseOut]
    by_coupon: list[CouponUseOut]
    by_date: list[DiscountDayOut]

    @classmethod
    def from_report(cls, r: DiscountReport) -> "DiscountReportOut":
        return cls(
            date_from=r.date_from,
            date_to=r.date_to,
            gross_sales=r.gross,
            net_sales=r.net,
            total_discount=r.total_discount,
            line_discount=r.line_discount,
            bill_discount=r.bill_discount,
            promotion_discount=r.promotion_discount,
            promotions_used=r.promotions_used,
            promotion_applications=r.promotion_applications,
            coupon_uses=r.coupon_uses,
            coupon_discount=r.coupon_discount,
            by_promotion=[
                PromotionUseOut(promotion_id=p.promotion_id, name=p.name, uses=p.uses, discount=p.discount)
                for p in r.by_promotion
            ],
            by_coupon=[CouponUseOut(code=c.code, uses=c.uses, discount=c.discount) for c in r.by_coupon],
            by_date=[
                DiscountDayOut(
                    day=d.day,
                    line_discount=d.line_discount,
                    bill_discount=d.bill_discount,
                    promotion_discount=d.promotion_discount,
                    total=d.total,
                )
                for d in r.by_date
            ],
        )
