"""Recommendations, insights and unusual-activity notices, worked out from the shop's own data.

Every figure here is calculated from the database by the analytics, inventory and report services. Nothing is
estimated by a language model, and nothing here changes anything: a recommendation is a list for a person to
read,
and the action proposals it carries only become real after a person confirms them (`ai_action_service`).

Wording is neutral on purpose. An unusual pattern is "unusual activity", never an accusation: it has ordinary
explanations (a sale, a stock count, a big customer) and the notice says how to check.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, Supplier
from app.services import ai_dates, analytics_service, inventory_service, khata_service, sales_report_service
from app.services import ai_format as fmt
from app.services.errors import EntitlementError

ZERO = Decimal("0")
WINDOW_DAYS = 30  # how far back "recent sales" looks
COVER_DAYS = 21  # a reorder aims to cover this many days of recent sales
RUNNING_OUT_DAYS = 7  # stock that lasts less than this at the recent pace is "running out"
LOW_HISTORY_UNITS = Decimal("5")  # fewer units sold than this in the window is "limited history"
SLOW_DAYS = 60
SLOW_COVER_DAYS = 90

# Unusual-activity thresholds (documented in BUSINESS_RULES AI).
DROP_PERCENT = Decimal("-40")
SPIKE_PERCENT = Decimal("60")
DISCOUNT_RATIO = Decimal("2")  # the recent discount rate is this many times the usual one
DISCOUNT_MIN_RATE = Decimal("10")  # and at least this many percent of gross sales
RETURN_RATE = Decimal("15")  # returns above this percent of net sales
ADJUSTMENT_SHARE = Decimal("25")  # an adjustment above this percent of the stock it started from
ADJUSTMENT_MIN_UNITS = Decimal("5")
QUICK_RATIO = Decimal("2")


@dataclass(frozen=True)
class Recommendation:
    product_id: int
    name: str
    sku: str
    unit_code: str
    current_stock: Decimal
    reorder_level: Decimal
    sold_recently: Decimal
    window_days: int
    per_day: Decimal
    days_of_cover: Decimal | None
    suggested_quantity: Decimal
    supplier_id: int | None
    supplier_name: str | None
    latest_purchase_price: Decimal | None
    avg_cost: Decimal | None
    unit_cost_used: Decimal | None
    cost_basis: str | None  # "latest purchase price" | "average cost" | None
    estimated_cost: Decimal | None
    reasons: list[str] = field(default_factory=list)
    low_history: bool = False
    pack_size: Decimal | None = None
    moq: Decimal | None = None
    lead_time_days: int | None = None


@dataclass(frozen=True)
class SupplierGroup:
    supplier_id: int | None
    supplier_name: str | None
    lines: list[Recommendation]
    estimated_total: Decimal  # of the lines that have a known price
    lines_without_price: int


@dataclass(frozen=True)
class SlowMover:
    product_id: int
    name: str
    unit_code: str
    stock: Decimal
    sold: Decimal
    days: int
    days_of_cover: Decimal | None
    stock_value: Decimal | None


@dataclass(frozen=True)
class Decline:
    product_id: int
    name: str
    unit_code: str
    previous_quantity: Decimal
    current_quantity: Decimal
    change_percent: Decimal


@dataclass(frozen=True)
class Insight:
    kind: str
    attention: bool
    text: str
    data: dict[str, object] = field(default_factory=dict)
    source: str = ""


@dataclass(frozen=True)
class Anomaly:
    kind: str
    title: str  # always begins "Unusual activity detected"
    detail: str
    check: str  # how a person can look into it
    data: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PromotionIdea:
    title: str
    reason: str
    proposal: dict[str, object]  # a draft for `ai_action_service`; nothing is created by showing it
    caution: str | None = None


def _round_up(quantity: Decimal, allows_decimal: bool) -> Decimal:
    if not allows_decimal:
        return Decimal(math.ceil(quantity))
    return quantity.quantize(Decimal("0.1"), rounding=ROUND_CEILING)


# --- Reorder and purchase suggestions ---------------------------------------------------------------


def reorder_recommendations(
    session: Session,
    shop_id: int,
    today: date,
    *,
    window_days: int = WINDOW_DAYS,
    cover_days: int = COVER_DAYS,
) -> list[Recommendation]:
    """Active products that are at or below their reorder level, or will run out within a week at the recent pace.

    Suggested quantity brings stock up to (recent daily sales x `cover_days`) + the reorder level; a product with no
    recent sales is brought to twice its reorder level. When the product has a pack size or a minimum order quantity
    set, the suggestion is rounded up to respect them, and a reason says so. Wording and figures are for a person to
    review: no purchase is created here.
    """
    rows, _ = inventory_service.list_inventory(session, shop_id, active=True, limit=None)
    sold = analytics_service.sales_velocity(session, shop_id, today, window_days)
    products = {p.id: p for p in session.scalars(select(Product).where(Product.shop_id == shop_id))}
    suppliers = {s.id: s for s in session.scalars(select(Supplier).where(Supplier.shop_id == shop_id))}
    out: list[Recommendation] = []
    for row in rows:
        recent = max(sold.get(row.product_id, ZERO), ZERO)
        per_day = recent / window_days
        cover = row.current_stock / per_day if per_day > 0 else None
        at_or_below = row.current_stock <= row.reorder_level and (row.reorder_level > 0 or recent > 0)
        running_out = cover is not None and cover < RUNNING_OUT_DAYS
        if not (at_or_below or running_out):
            continue
        target = recent * cover_days / window_days + row.reorder_level  # multiply first: no rounding drift
        if per_day == 0:
            target = row.reorder_level * 2
        suggested = _round_up(max(target - row.current_stock, ZERO), row.allows_decimal)
        if suggested <= 0:
            continue
        product = products[row.product_id]
        supplier = suppliers.get(product.default_supplier_id) if product.default_supplier_id else None
        pack_reasons: list[str] = []
        if product.pack_size and product.pack_size > 0:
            packs = (suggested / product.pack_size).quantize(Decimal("1"), rounding=ROUND_CEILING)
            rounded = packs * product.pack_size
            if rounded != suggested:
                pack_reasons.append(
                    f"Rounded up to whole packs of {fmt.quantity(product.pack_size)} {row.unit_code}"
                )
            suggested = rounded
        if product.moq and suggested < product.moq:
            pack_reasons.append(
                f"Raised to the supplier's minimum order quantity of {fmt.quantity(product.moq)} {row.unit_code}"
            )
            suggested = product.moq
        unit_cost, basis = (
            (product.purchase_price, "latest purchase price")
            if product.purchase_price is not None
            else (product.avg_cost, "average cost")
            if product.avg_cost is not None
            else (None, None)
        )
        reasons = []
        if row.current_stock <= 0:
            reasons.append("Out of stock")
        elif at_or_below:
            reasons.append("At or below the reorder level")
        if running_out:
            reasons.append(f"About {cover:.0f} days of stock left at the recent pace")
        reasons.extend(pack_reasons)
        if supplier is not None and supplier.lead_time_days is None:
            reasons.append("The supplier's lead time is not set: this does not account for delivery time")
        out.append(
            Recommendation(
                product_id=row.product_id,
                name=row.name,
                sku=row.sku,
                unit_code=row.unit_code,
                current_stock=row.current_stock,
                reorder_level=row.reorder_level,
                sold_recently=recent,
                window_days=window_days,
                per_day=per_day.quantize(Decimal("0.01")),
                days_of_cover=cover.quantize(Decimal("0.1")) if cover is not None else None,
                suggested_quantity=suggested,
                supplier_id=product.default_supplier_id,
                supplier_name=supplier.name if supplier else None,
                latest_purchase_price=product.purchase_price,
                avg_cost=product.avg_cost,
                unit_cost_used=unit_cost,
                cost_basis=basis,
                estimated_cost=(suggested * unit_cost).quantize(Decimal("0.01"))
                if unit_cost is not None
                else None,
                reasons=reasons,
                low_history=recent < LOW_HISTORY_UNITS,
                pack_size=product.pack_size,
                moq=product.moq,
                lead_time_days=supplier.lead_time_days if supplier else None,
            )
        )
    return sorted(out, key=lambda r: (r.days_of_cover is None, r.days_of_cover or ZERO, r.name.casefold()))


def purchase_suggestions(
    session: Session,
    shop_id: int,
    today: date,
    *,
    window_days: int = WINDOW_DAYS,
    cover_days: int = COVER_DAYS,
) -> list[SupplierGroup]:
    """The reorder list grouped by each product's preferred supplier (products with none are grouped last)."""
    groups: dict[int | None, list[Recommendation]] = {}
    for rec in reorder_recommendations(
        session, shop_id, today, window_days=window_days, cover_days=cover_days
    ):
        groups.setdefault(rec.supplier_id, []).append(rec)
    result = [
        SupplierGroup(
            supplier_id=sid,
            supplier_name=lines[0].supplier_name,
            lines=lines,
            estimated_total=sum((ln.estimated_cost or ZERO for ln in lines), ZERO),
            lines_without_price=sum(1 for ln in lines if ln.estimated_cost is None),
        )
        for sid, lines in groups.items()
    ]
    return sorted(result, key=lambda g: (g.supplier_id is None, (g.supplier_name or "").casefold()))


# --- Slow movers and declines -----------------------------------------------------------------------


def slow_moving(session: Session, shop_id: int, today: date, days: int = SLOW_DAYS) -> list[SlowMover]:
    """Products with plenty of stock but little recent selling: none sold, or more than 90 days of stock at the pace."""
    rows, _ = inventory_service.list_inventory(session, shop_id, active=True, limit=None)
    sold = analytics_service.sales_velocity(session, shop_id, today, days)
    out: list[SlowMover] = []
    for row in rows:
        if row.current_stock <= 0:
            continue
        recent = max(sold.get(row.product_id, ZERO), ZERO)
        per_day = recent / days
        cover = row.current_stock / per_day if per_day > 0 else None
        plenty = row.current_stock > row.reorder_level * 2
        if plenty and (cover is None or cover > SLOW_COVER_DAYS):
            out.append(
                SlowMover(
                    row.product_id,
                    row.name,
                    row.unit_code,
                    row.current_stock,
                    recent,
                    days,
                    cover.quantize(Decimal("0.1")) if cover is not None else None,
                    (row.current_stock * row.avg_cost).quantize(Decimal("0.01"))
                    if row.avg_cost is not None
                    else None,
                )
            )
    return sorted(
        out, key=lambda m: (m.stock_value is None, -(m.stock_value or ZERO), -m.stock, m.name.casefold())
    )


def declining_products(
    session: Session,
    shop_id: int,
    current: tuple[date, date],
    previous: tuple[date, date],
    minimum: Decimal = Decimal("3"),
) -> list[Decline]:
    """Products whose units sold fell by 30% or more against the previous period (at least `minimum` units then)."""
    now = {r.product_id: r for r in analytics_service.product_sales(session, shop_id, *current)}
    before = analytics_service.product_sales(session, shop_id, *previous)
    out: list[Decline] = []
    for old in before:
        if old.quantity < minimum:
            continue
        new = now.get(old.product_id)
        quantity = new.quantity if new else ZERO
        change = fmt.percent_change(quantity, old.quantity)
        if change is not None and change <= Decimal("-30"):
            out.append(Decline(old.product_id, old.name, old.unit_code, old.quantity, quantity, change))
    return sorted(out, key=lambda d: (d.change_percent, d.name.casefold()))


# --- Insights ---------------------------------------------------------------------------------------


def insights(session: Session, shop_id: int, today: date) -> list[Insight]:
    """Plain statements, each backed by figures retrieved for this call."""
    out: list[Insight] = []
    rows, _ = inventory_service.list_inventory(session, shop_id, active=True, limit=None)
    low = [r for r in rows if r.status is not inventory_service.StockStatus.IN_STOCK]
    if low:
        out.append(
            Insight(
                "inventory",
                True,
                f"{len(low)} product{'s are' if len(low) != 1 else ' is'} at or below the reorder level.",
                {"count": len(low)},
                "Based on the current inventory ledger",
            )
        )

    month = ai_dates.resolve("this month", today)
    assert month is not None
    prior = ai_dates.previous(month, today)
    now = sales_report_service.sales_summary(session, shop_id, month.start, month.end)
    then = sales_report_service.sales_summary(session, shop_id, prior.start, prior.end)
    change = fmt.percent_change(now.combined.net, then.combined.net)
    source = f"Based on sales data from {ai_dates.describe(month.start, month.end)}"
    if change is not None:
        word = "increased" if change > 0 else "decreased" if change < 0 else "is unchanged"
        text = (
            f"Sales {word} by {abs(change)}% compared with {prior.label}."
            if change != 0
            else f"Sales are unchanged compared with {prior.label}."
        )
        out.append(
            Insight(
                "sales",
                change <= DROP_PERCENT,
                text,
                {
                    "net": str(now.combined.net),
                    "previous_net": str(then.combined.net),
                    "change_percent": str(change),
                },
                source,
            )
        )
    if now.detailed_sales_without_cost:
        out.append(
            Insight(
                "profit",
                False,
                f"Profit Not Available for {now.detailed_sales_without_cost} sale"
                f"{'s' if now.detailed_sales_without_cost != 1 else ''} because cost data is missing.",
                {"sales_without_cost": now.detailed_sales_without_cost},
                source,
            )
        )
    if now.returns_count:
        out.append(
            Insight(
                "returns",
                False,
                f"{now.returns_count} sales return{'s' if now.returns_count != 1 else ''} this month refunded "
                f"{fmt.money(now.returns_total)}.",
                {"count": now.returns_count, "total": str(now.returns_total)},
                source,
            )
        )

    slow = slow_moving(session, shop_id, today)
    if slow:
        out.append(
            Insight(
                "slow_moving",
                False,
                f"{len(slow)} product{'s have' if len(slow) != 1 else ' has'} high stock but low recent sales.",
                {"count": len(slow), "examples": [m.name for m in slow[:3]]},
                f"Based on the inventory ledger and sales from the last {SLOW_DAYS} days",
            )
        )

    accounts, _ = khata_service.list_accounts(
        session, shop_id, balance=khata_service.BalanceStatus.OUTSTANDING, biggest_first=True, limit=None
    )
    if accounts:
        total = sum((a.outstanding for a in accounts), ZERO)
        out.append(
            Insight(
                "khata",
                True,
                f"{len(accounts)} customer{'s have' if len(accounts) != 1 else ' has'} an outstanding balance "
                f"requiring attention, {fmt.money(total)} in total.",
                {"customers": len(accounts), "total": str(total)},
                "Based on the customer Khata ledger",
            )
        )
    try:
        report = sales_report_service.discount_report(session, shop_id, month.start, month.end)
    except EntitlementError:
        report = None  # advanced reports are not in this plan: say nothing rather than guess
    if report is not None and report.by_promotion:
        best = max(report.by_promotion, key=lambda p: (p.uses, p.discount))
        out.append(
            Insight(
                "promotion",
                False,
                f"The offer '{best.name}' was applied {best.uses} time{'s' if best.uses != 1 else ''} and gave "
                f"{fmt.money(best.discount)} in discount.",
                {"promotion_id": best.promotion_id, "uses": best.uses, "discount": str(best.discount)},
                source,
            )
        )
    return out


# --- Unusual activity ---------------------------------------------------------------------------------


def anomalies(session: Session, shop_id: int, today: date) -> list[Anomaly]:
    """Patterns that differ from the shop's own recent normal. Notices, never conclusions."""
    out: list[Anomaly] = []
    recent = (today - timedelta(days=6), today)
    before = (today - timedelta(days=13), today - timedelta(days=7))
    usual = (today - timedelta(days=34), today - timedelta(days=7))  # four weeks before the recent week
    now = sales_report_service.sales_summary(session, shop_id, *recent)
    then = sales_report_service.sales_summary(session, shop_id, *before)
    long = sales_report_service.sales_summary(session, shop_id, *usual)
    window = f"{recent[0]:%d %b} to {recent[1]:%d %b}"

    change = fmt.percent_change(now.combined.net, then.combined.net)
    if change is not None and len(then.days) >= 3:
        if change <= DROP_PERCENT:
            out.append(
                Anomaly(
                    "sales_drop",
                    "Unusual activity detected: sales are well below the previous week",
                    f"Sales for {window} were {fmt.money(now.combined.net)}, {abs(change)}% lower than the week before "
                    f"({fmt.money(then.combined.net)}).",
                    "Look at the daily sales in Reports. A holiday, a closed day or a supply problem can explain this.",
                    {
                        "net": str(now.combined.net),
                        "previous_net": str(then.combined.net),
                        "change_percent": str(change),
                    },
                )
            )
        elif change >= SPIKE_PERCENT:
            out.append(
                Anomaly(
                    "sales_spike",
                    "Unusual activity detected: sales are well above the previous week",
                    f"Sales for {window} were {fmt.money(now.combined.net)}, {change}% higher than the week before "
                    f"({fmt.money(then.combined.net)}).",
                    "Look at the daily sales in Reports. A festival, a bulk order or an offer can explain this.",
                    {
                        "net": str(now.combined.net),
                        "previous_net": str(then.combined.net),
                        "change_percent": str(change),
                    },
                )
            )

    def rate(summary: sales_report_service.SalesSummary) -> Decimal | None:
        gross = summary.combined.gross
        return (summary.combined.discount * 100 / gross) if gross > 0 else None

    recent_rate, usual_rate = rate(now), rate(long)
    if (
        recent_rate is not None
        and usual_rate is not None
        and recent_rate >= DISCOUNT_MIN_RATE
        and recent_rate >= usual_rate * DISCOUNT_RATIO
    ):
        out.append(
            Anomaly(
                "discount_rate",
                "Unusual activity detected: discounts are higher than usual",
                f"Discounts were {recent_rate:.1f}% of gross sales for {window}, against {usual_rate:.1f}% in the four "
                "weeks before.",
                "Open the discount report to see whether offers, coupons or bill discounts account for it.",
                {"recent_rate": f"{recent_rate:.1f}", "usual_rate": f"{usual_rate:.1f}"},
            )
        )

    month = (today - timedelta(days=29), today)
    thirty = sales_report_service.sales_summary(session, shop_id, *month)
    if thirty.combined.net > 0 and thirty.returns_count >= 3:
        rate_returns = thirty.returns_total * 100 / thirty.combined.net
        if rate_returns >= RETURN_RATE:
            out.append(
                Anomaly(
                    "returns",
                    "Unusual activity detected: returns are high",
                    f"{thirty.returns_count} returns in the last 30 days refunded {fmt.money(thirty.returns_total)}, "
                    f"{rate_returns:.1f}% of net sales.",
                    "Open the Returns list and check the reasons given.",
                    {
                        "count": thirty.returns_count,
                        "total": str(thirty.returns_total),
                        "rate": f"{rate_returns:.1f}",
                    },
                )
            )

    for adj in inventory_service.adjustments_between(session, shop_id, *month)[:50]:
        size = abs(adj.quantity_delta)
        base = adj.stock_before if adj.stock_before > 0 else ZERO
        if size >= ADJUSTMENT_MIN_UNITS and base > 0 and size * 100 / base >= ADJUSTMENT_SHARE:
            direction = "removed" if adj.quantity_delta < 0 else "added"
            out.append(
                Anomaly(
                    "stock_adjustment",
                    "Unusual activity detected: a large stock adjustment",
                    f"{fmt.quantity(size)} {adj.unit_code} of {adj.product_name} was {direction} on {adj.day:%d %b %Y} "
                    f"({size * 100 / base:.0f}% of the {fmt.quantity(base)} in stock), reason: "
                    f"{(adj.reason_code or 'not given').replace('_', ' ').lower()}.",
                    "Open the product's stock history to see the entry and its note.",
                    {
                        "product_id": adj.product_id,
                        "quantity": str(adj.quantity_delta),
                        "day": adj.day.isoformat(),
                    },
                )
            )

    quick_recent = now.quick.net
    quick_usual_week = long.quick.net / 4
    if quick_usual_week > 0 and quick_recent >= quick_usual_week * QUICK_RATIO and now.quick.sales_count >= 3:
        out.append(
            Anomaly(
                "quick_sales",
                "Unusual activity detected: Quick Sales are higher than usual",
                f"Quick Sales were {fmt.money(quick_recent)} for {window}, against about {fmt.money(quick_usual_week)} "
                "a week before.",
                "Open the Quick Sales list. Quick Sales carry no product or cost, so they are easy to overlook.",
                {"recent": str(quick_recent), "usual_week": str(quick_usual_week)},
            )
        )
    return out


# --- Promotion ideas ------------------------------------------------------------------------------------


def promotion_ideas(session: Session, shop_id: int, today: date) -> list[PromotionIdea]:
    """Ideas for offers, from slow-moving stock and the average bill. Each is a draft to review, never an active offer."""
    ideas: list[PromotionIdea] = []
    products = {p.id: p for p in session.scalars(select(Product).where(Product.shop_id == shop_id))}
    for mover in slow_moving(session, shop_id, today)[:5]:
        product = products[mover.product_id]
        after = (product.selling_price * Decimal("0.90")).quantize(Decimal("0.01"))
        caution = None
        if product.avg_cost is None:
            caution = "Your cost for this product is not known, so check the margin before using this offer."
        elif after < product.avg_cost:
            caution = f"At 10% off the price ({fmt.money(after)}) is below your average cost ({fmt.money(product.avg_cost)})."
        cover = f"{mover.days_of_cover:.0f} days of stock" if mover.days_of_cover is not None else "no sales"
        ideas.append(
            PromotionIdea(
                f"10% discount for {mover.name}",
                f"{fmt.quantity(mover.stock)} {mover.unit_code} in stock and {cover} in the last {mover.days} days.",
                {
                    "name": f"10% off {mover.name}"[:120],
                    "promo_type": "PERCENT",
                    "scope": "PRODUCTS",
                    "percent": "10",
                    "product_ids": [mover.product_id],
                },
                caution,
            )
        )
    month_start = today.replace(day=1)
    summary = sales_report_service.sales_summary(session, shop_id, month_start, today)
    if summary.detailed.sales_count >= 5:
        average = summary.detailed.net / summary.detailed.sales_count
        threshold = int((average * Decimal("1.5")) / 100) * 100 or 100
        ideas.append(
            PromotionIdea(
                f"{fmt.money(Decimal(100))} off above {fmt.money(Decimal(threshold))}",
                f"Your average bill this month is {fmt.money(average.quantize(Decimal('0.01')))}; an offer above "
                f"{fmt.money(Decimal(threshold))} may encourage larger baskets.",
                {
                    "name": f"Rs 100 off above Rs {threshold}",
                    "promo_type": "AMOUNT",
                    "scope": "CART",
                    "amount": "100",
                    "min_cart_value": str(threshold),
                },
                "The effect on your margin depends on your products; review before using it.",
            )
        )
    return ideas
