"""Supplier and purchase analytics from posted purchases, their lines and purchase returns. Purely factual.

NOT AVAILABLE, because no supporting data exists: supplier quality score, supplier reliability, delivery performance, lead-time
adherence. Suppliers have no payment terms and purchases record no delivery date, so none of those is computed or implied.
Supplier comparisons are comparisons of money, counts and unit costs; not a verdict on a supplier.
"""

from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, Purchase, PurchaseItem, PurchaseReturn, Supplier
from app.models.enums import DocumentStatus, PurchaseStatus
from app.reporting.filters import ReportFilters, ReportTable
from app.reporting.sales import _table

ZERO = Decimal("0.00")
NOT_MEASURED = "Not Available: no quality, reliability or delivery data is recorded, so none is computed."
_HONOURED = {"supplier_id", "product_id", "category_id", "brand", "active"}


def _pct(n: Decimal, d: Decimal) -> Decimal | None:
    return (n / d * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN) if d > 0 else None


def _purchase_cond(shop_id: int, f: ReportFilters) -> list:
    cond = [
        Purchase.shop_id == shop_id,
        Purchase.status == PurchaseStatus.POSTED,
        Purchase.purchase_date >= f.period.start,
        Purchase.purchase_date <= f.period.end,
    ]
    if f.supplier_id is not None:
        cond.append(Purchase.supplier_id == f.supplier_id)
    return cond


def suppliers(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    cond = _purchase_cond(shop_id, f)
    per = {
        sid: (n, _m, first, last)
        for sid, n, _m, first, last in session.execute(
            select(
                Purchase.supplier_id,
                func.count(),
                func.sum(Purchase.total_amount),
                func.min(Purchase.purchase_date),
                func.max(Purchase.purchase_date),
            )
            .where(*cond)
            .group_by(Purchase.supplier_id)
        )
    }
    returns = dict(
        session.execute(
            select(Purchase.supplier_id, func.sum(PurchaseReturn.total_amount))
            .join(
                Purchase,
                (Purchase.shop_id == PurchaseReturn.shop_id) & (Purchase.id == PurchaseReturn.purchase_id),
            )
            .where(
                PurchaseReturn.shop_id == shop_id,
                PurchaseReturn.status == DocumentStatus.POSTED,
                PurchaseReturn.return_date >= f.period.start,
                PurchaseReturn.return_date <= f.period.end,
                *([Purchase.supplier_id == f.supplier_id] if f.supplier_id is not None else []),
            )
            .group_by(Purchase.supplier_id)
        ).all()
    )
    products = dict(
        session.execute(
            select(Purchase.supplier_id, func.count(func.distinct(PurchaseItem.product_id)))
            .join(
                PurchaseItem,
                (PurchaseItem.shop_id == Purchase.shop_id) & (PurchaseItem.purchase_id == Purchase.id),
            )
            .where(*cond)
            .group_by(Purchase.supplier_id)
        ).all()
    )
    ids = set(per) | {k for k in returns}
    names = (
        {
            s.id: (s.name, s.is_active)
            for s in session.scalars(
                select(Supplier).where(Supplier.shop_id == shop_id, Supplier.id.in_(ids))
            )
        }
        if ids
        else {}
    )
    rows = []
    for sid in ids:
        n, gross, first, last = per.get(sid, (0, ZERO, None, None))
        gross = Decimal(gross or 0)
        ret = Decimal(returns.get(sid) or 0)
        name, is_active = names.get(sid, ("?", True))
        if f.active is not None and is_active != f.active:
            continue
        rows.append(
            {
                "supplier_id": sid,
                "supplier": name,
                "purchases": n,
                "purchase_value": gross,
                "purchase_returns": ret,
                "net_spend": gross - ret,
                "product_count": products.get(sid, 0),
                "average_purchase_value": (gross / n).quantize(Decimal("0.01")) if n else None,
                "first_purchase": first,
                "last_purchase": last,
            }
        )
    total = sum((r["net_spend"] for r in rows), ZERO)
    for r in rows:
        r["share_pct"] = _pct(r["net_spend"], total)
    rows.sort(key=lambda r: (-r["net_spend"], r["supplier"].casefold()))
    cols = [
        ("supplier", "Supplier", "text"),
        ("purchases", "Purchases", "integer"),
        ("purchase_value", "Purchase value", "money"),
        ("purchase_returns", "Returns", "money"),
        ("net_spend", "Net spend", "money"),
        ("share_pct", "Share of spend %", "percent"),
        ("product_count", "Products", "integer"),
        ("average_purchase_value", "Average purchase", "money"),
        ("last_purchase", "Last purchase", "date"),
    ]
    return _table(
        f,
        "Spend by supplier",
        cols,
        rows,
        {"supplier_id", "active"},
        "Posted purchases and purchase returns",
        [NOT_MEASURED],
    )


def concentration(session: Session, shop_id: int, f: ReportFilters) -> dict:
    """How concentrated spend is: the share of the largest supplier and of the top three. A fact about the spend, not a risk verdict."""
    rows = suppliers(
        session, shop_id, ReportFilters(period=f.period, comparison=f.comparison, limit=200)
    ).rows
    spend = [r["net_spend"] for r in rows if r["net_spend"] > 0]
    total = sum(spend, ZERO)
    return {
        "period": f.period,
        "suppliers": len(spend),
        "total_net_spend": total,
        "top_supplier": rows[0]["supplier"] if spend else None,
        "top_supplier_share_pct": _pct(spend[0], total) if spend else None,
        "top_three_share_pct": _pct(sum(spend[:3], ZERO), total) if spend else None,
        "notes": [
            "Shares are of net spend (purchases less returns) in the period.",
            "This describes where money went; it is not a judgement of any supplier.",
            NOT_MEASURED,
        ],
    }


def products(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Per supplier and product: how much was bought, the average and latest unit cost, and how the cost moved inside the period."""
    cond = _purchase_cond(shop_id, f)
    if f.product_id is not None:
        cond.append(PurchaseItem.product_id == f.product_id)
    if f.category_id is not None:
        cond.append(Product.category_id == f.category_id)
    lines = session.execute(
        select(
            Purchase.supplier_id,
            Supplier.name,
            PurchaseItem.product_id,
            Product.name,
            Purchase.purchase_date,
            Purchase.id,
            PurchaseItem.quantity,
            PurchaseItem.unit_cost,
            PurchaseItem.line_total,
        )
        .join(
            PurchaseItem,
            (PurchaseItem.shop_id == Purchase.shop_id) & (PurchaseItem.purchase_id == Purchase.id),
        )
        .join(Product, (Product.shop_id == PurchaseItem.shop_id) & (Product.id == PurchaseItem.product_id))
        .join(Supplier, (Supplier.shop_id == Purchase.shop_id) & (Supplier.id == Purchase.supplier_id))
        .where(*cond)
        .order_by(Purchase.purchase_date, Purchase.id)
    ).all()
    grouped: dict[tuple[int, int], dict] = {}
    for sid, sname, pid, pname, day, _purchase_id, qty, cost, total in lines:
        g = grouped.setdefault(
            (sid, pid),
            {
                "supplier_id": sid,
                "supplier": sname,
                "product_id": pid,
                "product": pname,
                "purchases": 0,
                "quantity": Decimal(0),
                "spend": ZERO,
                "first_cost": cost,
                "latest_cost": cost,
                "latest_date": day,
            },
        )
        g["purchases"] += 1
        g["quantity"] += qty
        g["spend"] += total
        g["latest_cost"], g["latest_date"] = cost, day
    rows = []
    for g in grouped.values():
        avg = (g["spend"] / g["quantity"]).quantize(Decimal("0.01")) if g["quantity"] > 0 else None
        change = None
        if g["purchases"] >= 2 and g["first_cost"] > 0:
            change = ((g["latest_cost"] - g["first_cost"]) / g["first_cost"] * 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_EVEN
            )
        rows.append(
            {
                **{
                    k: g[k]
                    for k in (
                        "supplier_id",
                        "supplier",
                        "product_id",
                        "product",
                        "purchases",
                        "spend",
                        "latest_date",
                    )
                },
                "quantity": g["quantity"].quantize(Decimal("0.001")),
                "average_unit_cost": avg,
                "latest_unit_cost": g["latest_cost"],
                "cost_change_pct": change,
                "cost_note": None if change is not None else "Insufficient Data (fewer than two purchases)",
            }
        )
    rows.sort(key=lambda r: (-r["spend"], r["product"].casefold()))
    cols = [
        ("supplier", "Supplier", "text"),
        ("product", "Product", "text"),
        ("purchases", "Purchases", "integer"),
        ("quantity", "Quantity", "quantity"),
        ("spend", "Spend", "money"),
        ("average_unit_cost", "Average unit cost", "money"),
        ("latest_unit_cost", "Latest unit cost", "money"),
        ("cost_change_pct", "Cost change first to latest %", "percent"),
        ("latest_date", "Latest purchase", "date"),
    ]
    return _table(
        f,
        "Purchases by supplier and product",
        cols,
        rows,
        {"supplier_id", "product_id", "category_id"},
        "Posted purchase lines",
        [
            "Cost change compares the first and the latest purchase price inside the period, for the same supplier and product.",
            NOT_MEASURED,
        ],
    )


def cost_trend(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Average purchase unit cost per month for the chosen product (all suppliers): how what you pay has moved."""
    if f.product_id is None:
        from app.services.errors import InvalidInputError

        raise InvalidInputError("Choose a product to see its cost trend.", field="product_id")
    rows_db = session.execute(
        select(Purchase.purchase_date, PurchaseItem.quantity, PurchaseItem.line_total)
        .join(
            PurchaseItem,
            (PurchaseItem.shop_id == Purchase.shop_id) & (PurchaseItem.purchase_id == Purchase.id),
        )
        .where(*_purchase_cond(shop_id, f), PurchaseItem.product_id == f.product_id)
        .order_by(Purchase.purchase_date)
    ).all()
    grouped: dict[str, list[Decimal]] = {}
    for day, qty, total in rows_db:
        g = grouped.setdefault(day.strftime("%Y-%m"), [Decimal(0), ZERO, 0])
        g[0] += qty
        g[1] += total
        g[2] += 1
    rows = [
        {
            "month": m,
            "purchases": g[2],
            "quantity": g[0].quantize(Decimal("0.001")),
            "average_unit_cost": (g[1] / g[0]).quantize(Decimal("0.01")) if g[0] > 0 else None,
        }
        for m, g in sorted(grouped.items())
    ]
    cols = [
        ("month", "Month", "text"),
        ("purchases", "Purchases", "integer"),
        ("quantity", "Quantity", "quantity"),
        ("average_unit_cost", "Average unit cost", "money"),
    ]
    return _table(
        f, "Purchase cost trend", cols, rows, {"product_id", "supplier_id"}, "Posted purchase lines"
    )


def returns(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    rows_db = session.execute(
        select(Purchase.supplier_id, Supplier.name, func.count(), func.sum(PurchaseReturn.total_amount))
        .join(
            Purchase,
            (Purchase.shop_id == PurchaseReturn.shop_id) & (Purchase.id == PurchaseReturn.purchase_id),
        )
        .join(Supplier, (Supplier.shop_id == Purchase.shop_id) & (Supplier.id == Purchase.supplier_id))
        .where(
            PurchaseReturn.shop_id == shop_id,
            PurchaseReturn.status == DocumentStatus.POSTED,
            PurchaseReturn.return_date >= f.period.start,
            PurchaseReturn.return_date <= f.period.end,
            *([Purchase.supplier_id == f.supplier_id] if f.supplier_id is not None else []),
        )
        .group_by(Purchase.supplier_id, Supplier.name)
    ).all()
    rows = sorted(
        (
            {"supplier_id": sid, "supplier": name, "returns": n, "returned_value": Decimal(v or 0)}
            for sid, name, n, v in rows_db
        ),
        key=lambda r: (-r["returned_value"], r["supplier"].casefold()),
    )
    cols = [
        ("supplier", "Supplier", "text"),
        ("returns", "Returns", "integer"),
        ("returned_value", "Returned value", "money"),
    ]
    return _table(
        f,
        "Purchase returns by supplier",
        cols,
        rows,
        {"supplier_id"},
        "Posted purchase returns",
        ["A return is a fact about a purchase, not a rating of the supplier."],
    )


def overview(session: Session, shop_id: int, f: ReportFilters) -> dict:
    c = concentration(session, shop_id, f)
    from app.services import analytics_service

    t = analytics_service.purchase_totals(session, shop_id, f.period.start, f.period.end)
    return {**c, "purchase_count": t.count, "purchase_value": t.total, "net_purchase_value": t.net}
