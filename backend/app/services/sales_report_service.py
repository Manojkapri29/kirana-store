"""Sales reports that tell Detailed, Quick and Combined sales apart, and Gross from Discount from Net.

Definitions (docs/BUSINESS_RULES.md, RP). Only POSTED sales count: a draft is not a sale and a voided one has
been reversed.

    Gross sales  what the goods were priced at, before any discount
                 Detailed: sum of quantity x price over the lines.   Quick: the amount entered.
    Discount     line discounts + the cashier's bill discounts + promotion and coupon discounts
                 (a Quick Sale has one transaction-level discount and no promotions)
    Net sales    what customers were charged = Gross - Discount = the sum of the sale totals

Profit exists only for Detailed Sales, whose lines carry a cost. A Quick Sale has no product and no cost,
so it is excluded from every cost and profit figure: the Combined figures have no profit and say so.

Offer analytics read the snapshots frozen on each posted sale (`sale_promotions`), never the current
promotions, so editing or ending an offer never changes a past report.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import QuickSale, Sale, SaleItem, SalePromotion, SalesReturn, SalesReturnItem
from app.models.enums import DocumentStatus, SaleStatus
from app.services import entitlement_service
from app.services.errors import InvalidInputError
from app.services.shop_service import get_shop, shop_today

ZERO = Decimal("0.00")
NOT_AVAILABLE = "Not Available"


@dataclass(frozen=True)
class Totals:
    sales_count: int = 0
    gross: Decimal = ZERO
    line_discount: Decimal = ZERO
    bill_discount: Decimal = ZERO  # the cashier's own (a Quick Sale's transaction discount is counted here)
    promotion_discount: Decimal = ZERO
    net: Decimal = ZERO

    @property
    def discount(self) -> Decimal:
        return self.line_discount + self.bill_discount + self.promotion_discount

    def plus(self, other: "Totals") -> "Totals":
        return Totals(
            self.sales_count + other.sales_count,
            self.gross + other.gross,
            self.line_discount + other.line_discount,
            self.bill_discount + other.bill_discount,
            self.promotion_discount + other.promotion_discount,
            self.net + other.net,
        )


@dataclass(frozen=True)
class DayRow:
    day: date
    detailed: Totals
    quick: Totals

    @property
    def combined(self) -> Totals:
        return self.detailed.plus(self.quick)


@dataclass(frozen=True)
class SalesSummary:
    date_from: date
    date_to: date
    detailed: Totals
    quick: Totals
    days: list[DayRow]
    detailed_gross_profit: Decimal | None  # Detailed Sales whose every line's cost is known; None if none are
    detailed_sales_without_cost: int  # Detailed sales left out of that profit because a cost was unknown
    returns_count: int = 0  # sales returns dated in the period (live ones)
    returns_total: Decimal = ZERO  # what they refunded
    # returns left out of the profit adjustment because a returned item's cost was unknown
    returns_without_cost: int = 0

    @property
    def combined(self) -> Totals:
        return self.detailed.plus(self.quick)

    @property
    def net_after_returns(self) -> Decimal:
        """Net sales less what was refunded on returns (BUSINESS_RULES R5)."""
        return self.combined.net - self.returns_total


@dataclass(frozen=True)
class PromotionUse:
    promotion_id: int
    name: str  # as it was on the most recent sale that used it
    uses: int
    discount: Decimal


@dataclass(frozen=True)
class CouponUse:
    code: str
    uses: int
    discount: Decimal


@dataclass(frozen=True)
class DiscountDay:
    day: date
    line_discount: Decimal
    bill_discount: Decimal
    promotion_discount: Decimal

    @property
    def total(self) -> Decimal:
        return self.line_discount + self.bill_discount + self.promotion_discount


@dataclass(frozen=True)
class DiscountReport:
    date_from: date
    date_to: date
    gross: Decimal  # gross sales of Detailed and Quick together, for scale
    net: Decimal
    line_discount: Decimal
    bill_discount: Decimal
    promotion_discount: Decimal
    promotions_used: int  # different promotions that were used
    promotion_applications: int  # times a promotion was applied to a sale
    coupon_uses: int
    coupon_discount: Decimal
    by_promotion: list[PromotionUse] = field(default_factory=list)
    by_coupon: list[CouponUse] = field(default_factory=list)
    by_date: list[DiscountDay] = field(default_factory=list)

    @property
    def total_discount(self) -> Decimal:
        return self.line_discount + self.bill_discount + self.promotion_discount


def resolve_period(
    session: Session, shop_id: int, date_from: date | None, date_to: date | None
) -> tuple[date, date]:
    """The month so far, unless dates are given."""
    today = shop_today(get_shop(session, shop_id))
    end = date_to or today
    start = date_from or end.replace(day=1)
    if start > end:
        raise InvalidInputError("The 'from' date is after the 'to' date.", field="date_from")
    return start, end


def _money(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else ZERO


def _detailed_by_day(session: Session, shop_id: int, start: date, end: date) -> dict[date, Totals]:
    in_range = (
        Sale.shop_id == shop_id,
        Sale.status == SaleStatus.POSTED,
        Sale.sale_date >= start,
        Sale.sale_date <= end,
    )
    lines = dict(
        session.execute(
            select(Sale.sale_date, func.sum(SaleItem.discount))
            .join(SaleItem, (SaleItem.shop_id == Sale.shop_id) & (SaleItem.sale_id == Sale.id))
            .where(*in_range)
            .group_by(Sale.sale_date)
        ).all()
    )
    out: dict[date, Totals] = {}
    for day, count, subtotal, bill, promo, net in session.execute(
        select(
            Sale.sale_date,
            func.count(),
            func.sum(Sale.subtotal),
            func.sum(Sale.discount),
            func.sum(Sale.promotion_discount),
            func.sum(Sale.total_amount),
        )
        .where(*in_range)
        .group_by(Sale.sale_date)
    ):
        line_discount = _money(lines.get(day))
        out[day] = Totals(
            count, _money(subtotal) + line_discount, line_discount, _money(bill), _money(promo), _money(net)
        )
    return out


def _quick_by_day(session: Session, shop_id: int, start: date, end: date) -> dict[date, Totals]:
    return {
        day: Totals(count, _money(gross), ZERO, _money(discount), ZERO, _money(net))
        for day, count, gross, discount, net in session.execute(
            select(
                QuickSale.sale_date,
                func.count(),
                func.sum(QuickSale.gross_amount),
                func.sum(QuickSale.discount),
                func.sum(QuickSale.total_amount),
            )
            .where(
                QuickSale.shop_id == shop_id,
                QuickSale.status == SaleStatus.POSTED,
                QuickSale.sale_date >= start,
                QuickSale.sale_date <= end,
            )
            .group_by(QuickSale.sale_date)
        )
    }


def _sum(rows: dict[date, Totals]) -> Totals:
    total = Totals()
    for row in rows.values():
        total = total.plus(row)
    return total


def sales_summary(
    session: Session, shop_id: int, date_from: date | None = None, date_to: date | None = None
) -> SalesSummary:
    """Gross, discount and net for Detailed, Quick and Combined sales, with a row per day."""
    start, end = resolve_period(session, shop_id, date_from, date_to)
    detailed, quick = (
        _detailed_by_day(session, shop_id, start, end),
        _quick_by_day(session, shop_id, start, end),
    )
    days = [
        DayRow(day, detailed.get(day, Totals()), quick.get(day, Totals()))
        for day in sorted(set(detailed) | set(quick))
    ]
    # Profit: Detailed Sales only, and only those whose every line has a known cost.
    costed = (
        Sale.shop_id == shop_id,
        Sale.status == SaleStatus.POSTED,
        Sale.sale_date >= start,
        Sale.sale_date <= end,
    )
    unknown = (
        select(SaleItem.sale_id)
        .where(SaleItem.shop_id == shop_id, SaleItem.cogs_amount.is_(None))
        .scalar_subquery()
    )
    known_total, known_count = session.execute(
        select(func.sum(Sale.total_amount), func.count()).where(*costed, Sale.id.not_in(unknown))
    ).one()
    cogs = session.scalar(
        select(func.sum(SaleItem.cogs_amount))
        .join(Sale, (Sale.shop_id == SaleItem.shop_id) & (Sale.id == SaleItem.sale_id))
        .where(*costed, Sale.id.not_in(unknown))
    )
    profit = _money(known_total) - _money(cogs) if known_count else None
    excluded = (
        session.scalar(select(func.count()).select_from(Sale).where(*costed, Sale.id.in_(unknown))) or 0
    )

    # Returns dated in the period: what they refunded, and what they did to profit (the refund less the
    # cost of the goods that came back). A return whose cost is unknown is counted but leaves the profit.
    live_returns = (
        SalesReturn.shop_id == shop_id,
        SalesReturn.status == DocumentStatus.POSTED,
        SalesReturn.return_date >= start,
        SalesReturn.return_date <= end,
    )
    returns_count, returns_total = session.execute(
        select(func.count(), func.coalesce(func.sum(SalesReturn.total_refund), 0)).where(*live_returns)
    ).one()
    unknown_cost = (
        select(SalesReturnItem.sales_return_id)
        .where(SalesReturnItem.shop_id == shop_id, SalesReturnItem.cogs_amount.is_(None))
        .scalar_subquery()
    )
    known_refund, known_cogs = session.execute(
        select(
            func.coalesce(func.sum(SalesReturn.total_refund), 0),
            func.coalesce(func.sum(SalesReturnItem.cogs_amount), 0),
        )
        .join(
            SalesReturnItem,
            (SalesReturnItem.shop_id == SalesReturn.shop_id)
            & (SalesReturnItem.sales_return_id == SalesReturn.id),
        )
        .where(*live_returns, SalesReturn.id.not_in(unknown_cost))
    ).one()
    without_cost = (
        session.scalar(
            select(func.count())
            .select_from(SalesReturn)
            .where(*live_returns, SalesReturn.id.in_(unknown_cost))
        )
        or 0
    )
    if profit is not None and known_refund:
        profit = profit - (_money(known_refund) - _money(known_cogs))
    return SalesSummary(
        start,
        end,
        _sum(detailed),
        _sum(quick),
        days,
        profit,
        excluded,
        returns_count=returns_count,
        returns_total=_money(returns_total),
        returns_without_cost=without_cost,
    )


def discount_report(
    session: Session, shop_id: int, date_from: date | None = None, date_to: date | None = None
) -> DiscountReport:
    """Where the discounts went: by kind, by promotion, by coupon and by day. Needs advanced reports."""
    entitlement_service.require_feature(session, shop_id, "advanced_reports")
    summary = sales_summary(session, shop_id, date_from, date_to)
    uses = session.execute(
        select(SalePromotion, Sale.sale_date)
        .join(Sale, (Sale.shop_id == SalePromotion.shop_id) & (Sale.id == SalePromotion.sale_id))
        .where(
            SalePromotion.shop_id == shop_id,
            Sale.status == SaleStatus.POSTED,
            Sale.sale_date >= summary.date_from,
            Sale.sale_date <= summary.date_to,
        )
        .order_by(SalePromotion.id)
    ).all()
    per_promotion: dict[int, PromotionUse] = {}
    per_coupon: dict[str, CouponUse] = {}
    for use, _day in uses:
        current = per_promotion.get(use.promotion_id)
        per_promotion[use.promotion_id] = PromotionUse(
            use.promotion_id,
            use.name,  # the rows are in order, so the last one seen is the most recent name
            (current.uses if current else 0) + 1,
            (current.discount if current else ZERO) + use.discount_amount,
        )
        if use.coupon_code:
            seen = per_coupon.get(use.coupon_code)
            per_coupon[use.coupon_code] = CouponUse(
                use.coupon_code,
                (seen.uses if seen else 0) + 1,
                (seen.discount if seen else ZERO) + use.discount_amount,
            )
    combined = summary.combined
    return DiscountReport(
        date_from=summary.date_from,
        date_to=summary.date_to,
        gross=combined.gross,
        net=combined.net,
        line_discount=combined.line_discount,
        bill_discount=combined.bill_discount,
        promotion_discount=combined.promotion_discount,
        promotions_used=len(per_promotion),
        promotion_applications=len(uses),
        coupon_uses=sum(c.uses for c in per_coupon.values()),
        coupon_discount=sum((c.discount for c in per_coupon.values()), ZERO),
        by_promotion=sorted(per_promotion.values(), key=lambda p: (-p.discount, p.promotion_id)),
        by_coupon=sorted(per_coupon.values(), key=lambda c: (-c.discount, c.code)),
        by_date=[
            DiscountDay(
                d.day, d.detailed.line_discount, d.combined.bill_discount, d.detailed.promotion_discount
            )
            for d in summary.days
            if d.combined.discount > 0
        ],
    )
