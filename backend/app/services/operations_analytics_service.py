"""Operational analytics for a shop owner: one overview, built ONLY from the existing read-only services.

There is no separate analytics store, cache or copy of the data, so this can never disagree with the transactional records:
sales come from `sales_report_service`, stock from `inventory_service`, customers from `khata_service`, purchases and trends
from `analytics_service`, and discounts from the discount report. Where something does not exist in the application (the online
store) or cannot be known (profit without costs) the section says so plainly instead of showing a made-up figure.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.reporting import online as online_report
from app.services import ai_format as fmt
from app.services import (
    ai_insights_service,
    analytics_service,
    inventory_service,
    khata_service,
    sales_report_service,
)
from app.services.errors import EntitlementError, InvalidInputError
from app.services.shop_service import get_shop, shop_today

ZERO = Decimal("0")
INSIDE_DETAILED = "Online store orders are ordinary detailed sales once delivered, so they are already inside the detailed figures."
NOT_AVAILABLE = "Not Available"
MAX_DAYS = 366


def _online_store(session: Session, sid: int, start: date, end: date) -> dict:
    r = online_report.period_summary(session, sid, start, end)
    return {
        "placed": r["placed"], "delivered": r["delivered"], "rejected_or_cancelled": r["rejected_or_cancelled"],
        "delivered_value": _s(r["delivered_value"]), "note": r["note"],
    }  # fmt: skip


def _s(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def _bucket_key(day: date, bucket: str) -> str:
    if bucket == "month":
        return f"{day:%Y-%m}"
    if bucket == "week":
        monday = day - timedelta(days=day.weekday())
        return monday.isoformat()
    return day.isoformat()


def overview(
    session: Session,
    ctx: RequestContext,
    date_from: date | None = None,
    date_to: date | None = None,
    bucket: str = "day",
) -> dict[str, Any]:
    if bucket not in ("day", "week", "month"):
        raise InvalidInputError("Choose day, week or month.", field="bucket")
    sid = ctx.shop_id
    today = shop_today(get_shop(session, sid))
    start, end = sales_report_service.resolve_period(session, sid, date_from, date_to)
    if (end - start).days + 1 > MAX_DAYS:
        raise InvalidInputError("Choose a period of at most one year.", field="date_from")

    s = sales_report_service.sales_summary(session, sid, start, end)
    combined = s.combined
    transactions = combined.sales_count
    series: dict[str, dict[str, Decimal | int]] = {}
    for day in s.days:
        entry = series.setdefault(_bucket_key(day.day, bucket), {"net": ZERO, "gross": ZERO, "count": 0})
        entry["net"] += day.combined.net  # type: ignore[operator]
        entry["gross"] += day.combined.gross  # type: ignore[operator]
        entry["count"] += day.combined.sales_count  # type: ignore[operator]
    sales = {
        "gross": _s(combined.gross),
        "discounts": _s(combined.discount),
        "net": _s(combined.net),
        "transactions": transactions,
        "average_bill": _s((combined.net / transactions).quantize(Decimal("0.01"))) if transactions else None,
        "detailed": {
            "count": s.detailed.sales_count,
            "gross": _s(s.detailed.gross),
            "discounts": _s(s.detailed.discount),
            "net": _s(s.detailed.net),
        },
        "quick": {
            "count": s.quick.sales_count,
            "gross": _s(s.quick.gross),
            "discounts": _s(s.quick.discount),
            "net": _s(s.quick.net),
        },
        "online": {"available": False, "reason": INSIDE_DETAILED},
        "returns": {"count": s.returns_count, "refunded": _s(s.returns_total)},
        "net_after_returns": _s(s.net_after_returns),
        "series": [
            {"period": key, "net": _s(v["net"]), "gross": _s(v["gross"]), "transactions": v["count"]}
            for key, v in sorted(series.items())
        ],  # type: ignore[arg-type]
        "bucket": bucket,
    }

    rows, total_products = inventory_service.list_inventory(session, sid, active=True, limit=None)
    status = inventory_service.StockStatus
    known = [r for r in rows if r.avg_cost is not None and r.current_stock > 0]
    value = sum((r.current_stock * r.avg_cost for r in known if r.avg_cost is not None), ZERO)
    slow = ai_insights_service.slow_moving(session, sid, min(end, today))
    inventory = {
        "products": total_products,
        "in_stock": sum(1 for r in rows if r.status is status.IN_STOCK),
        "low_stock": sum(1 for r in rows if r.status is status.LOW_STOCK),
        "out_of_stock": sum(1 for r in rows if r.status is status.OUT_OF_STOCK),
        "stock_value": _s(value) if known else None,
        "stock_value_note": None if known else NOT_AVAILABLE,
        "products_without_cost": sum(1 for r in rows if r.avg_cost is None and r.current_stock > 0),
        "slow_moving": [
            {"name": m.name, "stock": fmt.quantity(m.stock), "sold": fmt.quantity(m.sold), "days": m.days}
            for m in slow[:10]
        ],
        "movement": {
            k: {kk: (_s(vv) if isinstance(vv, Decimal) else vv) for kk, vv in v.items()}
            for k, v in inventory_service.movement_summary(session, sid, start, end).items()
        },
    }

    accounts, owing = khata_service.list_accounts(
        session, sid, balance=khata_service.BalanceStatus.OUTSTANDING, limit=None
    )
    credit_count, credit_total = analytics_service.credit_sales(session, sid, start, end)
    buyers, returning = analytics_service.customer_activity(session, sid, start, end)
    customers = {
        "outstanding_total": _s(sum((a.outstanding for a in accounts), ZERO)),
        "customers_owing": owing,
        "collections": _s(khata_service.collections_between(session, sid, start, end)),
        "credit_sales": {"bills": credit_count, "unpaid": _s(credit_total)},
        "customers_who_bought": buyers,
        "returning_customers": returning,
        "online_customers": {"available": False, "reason": INSIDE_DETAILED},
    }

    p = analytics_service.purchase_totals(session, sid, start, end)
    purchases = {
        "count": p.count,
        "total": _s(p.total),
        "returned": _s(p.returns_total),
        "net": _s(p.net),
        "by_supplier": [
            {"supplier": x.name, "purchases": x.purchases, "total": _s(x.total)} for x in p.by_supplier[:5]
        ],
        "trend": [
            {"month": m, "total": _s(t)} for m, t in analytics_service.purchase_trend(session, sid, end)
        ],
        "cost_movement": [
            {
                "product": m.name,
                "from": _s(m.first_cost),
                "to": _s(m.last_cost),
                "change_percent": str(m.change_percent),
            }
            for m in analytics_service.cost_movement(session, sid, start, end)
        ],
    }

    if s.detailed_gross_profit is None:
        profit: dict[str, Any] = {
            "available": False,
            "message": "Profit Not Available because cost data is missing."
            if s.detailed.sales_count
            else "There were no product-wise sales in this period.",
            "sales_without_cost": s.detailed_sales_without_cost,
        }
    else:
        net = s.costed_net or ZERO
        profit = {
            "available": True,
            "cogs": _s(s.costed_cogs),
            "gross_profit": _s(s.detailed_gross_profit),
            "margin_percent": str((s.detailed_gross_profit * 100 / net).quantize(Decimal("0.1")))
            if net
            else None,
            "sales_without_cost": s.detailed_sales_without_cost,
            "note": "Only Detailed Sales whose every cost is known are included. Quick Sales have no cost and are excluded.",
        }

    try:
        d = sales_report_service.discount_report(session, sid, start, end)
        promotions: dict[str, Any] = {
            "available": True,
            "discount_total": _s(d.total_discount),
            "from_offers_and_coupons": _s(d.promotion_discount),
            "offer_applications": d.promotion_applications,
            "coupon_uses": d.coupon_uses,
            "net_after_discounts": _s(combined.net),
            "top_offers": [
                {"name": x.name, "uses": x.uses, "discount": _s(x.discount)}
                for x in sorted(d.by_promotion, key=lambda x: -x.discount)[:5]
            ],
        }
    except EntitlementError as error:
        promotions = {"available": False, "reason": error.message}

    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "sales": sales,
        "inventory": inventory,
        "customers": customers,
        "purchases": purchases,
        "profit": profit,
        "promotions": promotions,
        "online_store": {"available": True, **_online_store(session, sid, start, end)},
        "source": "Calculated from your sales, stock, purchase and khata records. Nothing is estimated.",
    }
