"""Advanced sales analytics. Read-only, shop-scoped, posted documents only.

Kinds of sale are kept apart on purpose:
  * DETAILED: a bill with product lines. The only source of product-level quantity, revenue, cost and profit.
  * QUICK: a money-only total. Contributes to revenue and payment/cash analytics ONLY; never to a product, category,
    brand, quantity, cost or profit figure, and never to inventory.
  * ONLINE: this application has no online orders, so it is reported as Not Available (nothing is counted, and nothing
    can be double-counted with detailed sales).
  * COMBINED revenue = detailed + quick (before returns; returns are shown separately).

Product revenue = line total (after the line's own discount) - the offer discount allocated to that line, less refunds on
returns dated in the period. A bill-level discount typed by the cashier is not allocated to lines and is reported
separately ("unallocated bill discount") so the product rows plus that figure reconcile with the bill totals.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import case, func, select, type_coerce
from sqlalchemy.orm import Session

from app.db.types import Money
from app.models import (
    Category,
    Product,
    QuickSale,
    Sale,
    SaleItem,
    SalePromotion,
    SalesReturn,
    SalesReturnItem,
    Unit,
)
from app.models.enums import DocumentStatus, FinanceEventType, SaleStatus
from app.reporting.filters import Channel, ReportFilters, ReportTable
from app.services import finance_ledger_service

ZERO = Decimal("0.00")
Q3 = Decimal("0.001")
NA = "Not Available"
QUICK_NOTE = "Quick Sales are money-only: they are in revenue and payment figures but never in any product, category, brand, quantity, cost or profit figure."
ONLINE_NOTE = "Online sales: Not Available. This application has no online orders, so nothing is counted here and nothing can be double-counted."
INSUFFICIENT_COST = "Insufficient Cost Data"


def _m(v: object) -> Decimal:
    return Decimal(v or 0).quantize(Decimal("0.01"))


def _pct(n: Decimal, d: Decimal) -> Decimal | None:
    return (n / d * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN) if d > 0 else None


def _bucket_key(day: date, bucket: str) -> str:
    if bucket == "day":
        return day.isoformat()
    if bucket == "week":
        return (day - timedelta(days=day.weekday())).isoformat()  # the Monday that starts the week
    return day.strftime("%Y-%m")


def _sale_conditions(model, shop_id: int, f: ReportFilters, start: date, end: date) -> list:  # noqa: ANN001
    cond = [
        model.shop_id == shop_id,
        model.status == SaleStatus.POSTED,
        model.sale_date >= start,
        model.sale_date <= end,
    ]
    if f.customer_id is not None:
        cond.append(model.customer_id == f.customer_id)
    if f.payment_method:
        cond.append(model.payment_method == f.payment_method)
    return cond


def _want(f: ReportFilters, kind: Channel) -> bool:
    return f.channel in (Channel.ALL, kind)


def daily(session: Session, shop_id: int, f: ReportFilters, start: date, end: date) -> dict[date, dict]:
    """Per day: detailed and quick counts, gross, discount and net, honouring channel, customer and payment method."""
    out: dict[date, dict] = {}

    def slot(day: date) -> dict:
        return out.setdefault(
            day,
            {
                "detailed_count": 0,
                "detailed_net": ZERO,
                "detailed_discount": ZERO,
                "quick_count": 0,
                "quick_net": ZERO,
                "quick_discount": ZERO,
            },
        )

    if _want(f, Channel.DETAILED):
        cond = _sale_conditions(Sale, shop_id, f, start, end)
        line_disc = dict(session.execute(
            select(Sale.sale_date, func.sum(SaleItem.discount)).join(SaleItem, (SaleItem.shop_id == Sale.shop_id) & (SaleItem.sale_id == Sale.id))
            .where(*cond).group_by(Sale.sale_date)
        ).all())  # fmt: skip
        for day, n, disc, promo, net in session.execute(
            select(
                Sale.sale_date,
                func.count(),
                func.sum(Sale.discount),
                func.sum(Sale.promotion_discount),
                func.sum(Sale.total_amount),
            )
            .where(*cond)
            .group_by(Sale.sale_date)
        ):
            s = slot(day)
            s["detailed_count"], s["detailed_net"] = n, _m(net)
            s["detailed_discount"] = _m(disc) + _m(promo) + _m(line_disc.get(day))
    if _want(f, Channel.QUICK):
        cond = _sale_conditions(QuickSale, shop_id, f, start, end)
        for day, n, disc, net in session.execute(
            select(
                QuickSale.sale_date,
                func.count(),
                func.sum(QuickSale.discount),
                func.sum(QuickSale.total_amount),
            )
            .where(*cond)
            .group_by(QuickSale.sale_date)
        ):
            s = slot(day)
            s["quick_count"], s["quick_net"], s["quick_discount"] = n, _m(net), _m(disc)
    return out


def _table(
    f: ReportFilters, title: str, columns, rows, honoured: set[str], source: str, notes=None, total=None
) -> ReportTable:
    effect = f.effect(honoured)
    extra = (
        [f"Filters that do not apply to this report and were not used: {', '.join(effect.ignored)}."]
        if effect.ignored
        else []
    )
    return ReportTable(
        columns=columns,
        rows=rows[f.offset : f.offset + f.limit] if total is None else rows,
        total=len(rows) if total is None else total,
        limit=f.limit,
        offset=f.offset,
        title=title,
        period=f.period,
        comparison=f.comparison,
        notes=[*(notes or []), *extra],
        filters_applied=effect.applied,
        filters_ignored=effect.ignored,
        source=source,
    )


def trend(session: Session, shop_id: int, f: ReportFilters, bucket: str = "day") -> ReportTable:
    """Sales per day/week/month. Detailed and quick are separate columns; combined is their sum."""
    if bucket not in ("day", "week", "month"):
        from app.services.errors import InvalidInputError

        raise InvalidInputError("Choose day, week or month.", field="bucket")
    grouped: dict[str, dict] = {}
    for day, d in daily(session, shop_id, f, f.period.start, f.period.end).items():
        g = grouped.setdefault(
            _bucket_key(day, bucket),
            {"detailed_count": 0, "detailed_net": ZERO, "quick_count": 0, "quick_net": ZERO},
        )
        for k in g:
            g[k] += d[k]
    rows = []
    for key in sorted(grouped):
        g = grouped[key]
        count, net = g["detailed_count"] + g["quick_count"], g["detailed_net"] + g["quick_net"]
        rows.append(
            {
                "period": key,
                "detailed_net": g["detailed_net"],
                "quick_net": g["quick_net"],
                "combined_net": net,
                "transactions": count,
                "average_transaction_value": (net / count).quantize(Decimal("0.01")) if count else None,
            }
        )
    cols = [
        ("period", "Period", "text"),
        ("detailed_net", "Detailed sales", "money"),
        ("quick_net", "Quick sales", "money"),
        ("combined_net", "Combined revenue (before returns)", "money"),
        ("transactions", "Transactions", "integer"),
        ("average_transaction_value", "Average transaction value", "money"),
    ]
    return _table(
        f,
        f"Sales by {bucket}",
        cols,
        rows,
        {"customer_id", "payment_method", "channel"},
        "Posted sales and quick sales",
        [ONLINE_NOTE],
    )


def _product_rows(session: Session, shop_id: int, f: ReportFilters) -> list[dict]:
    """One row per product that sold on DETAILED bills in the period."""
    cond = _sale_conditions(Sale, shop_id, f, f.period.start, f.period.end)
    item_cond = list(cond) + [SaleItem.shop_id == shop_id]
    if f.product_id is not None:
        item_cond.append(SaleItem.product_id == f.product_id)
    if f.category_id is not None:
        item_cond.append(Product.category_id == f.category_id)
    if f.brand:
        item_cond.append(Product.brand == f.brand)
    if f.active is not None:
        item_cond.append(Product.is_active.is_(f.active))
    sold = session.execute(
        select(
            Product.id,
            Product.name,
            Product.sku,
            Product.brand,
            Category.id,
            Category.name,
            Unit.code,
            func.sum(SaleItem.quantity),
            type_coerce(func.sum(SaleItem.line_total - SaleItem.promotion_discount), Money),
            func.sum(SaleItem.cogs_amount),
            func.sum(case((SaleItem.cogs_amount.is_(None), 1), else_=0)),
            func.count(func.distinct(Sale.id)),
        )
        .select_from(SaleItem)
        .join(Sale, (Sale.shop_id == SaleItem.shop_id) & (Sale.id == SaleItem.sale_id))
        .join(Product, (Product.shop_id == SaleItem.shop_id) & (Product.id == SaleItem.product_id))
        .join(Category, (Category.shop_id == Product.shop_id) & (Category.id == Product.category_id))
        .join(Unit, Unit.id == Product.unit_id)
        .where(*item_cond)
        .group_by(Product.id, Product.name, Product.sku, Product.brand, Category.id, Category.name, Unit.code)
    ).all()
    ret_cond = [
        SalesReturn.shop_id == shop_id,
        SalesReturn.status == DocumentStatus.POSTED,
        SalesReturn.return_date >= f.period.start,
        SalesReturn.return_date <= f.period.end,
    ]
    if f.customer_id is not None or f.payment_method:
        ret_cond.append(
            SalesReturn.sale_id.in_(
                select(Sale.id).where(
                    Sale.shop_id == shop_id,
                    *([Sale.customer_id == f.customer_id] if f.customer_id is not None else []),
                    *([Sale.payment_method == f.payment_method] if f.payment_method else []),
                )
            )
        )
    returned = {
        pid: (Decimal(q or 0), _m(refund), _m(cost), unknown)
        for pid, q, refund, cost, unknown in session.execute(
            select(
                SalesReturnItem.product_id,
                func.sum(SalesReturnItem.quantity),
                func.sum(SalesReturnItem.refund_amount),
                func.sum(SalesReturnItem.cogs_amount),
                func.sum(case((SalesReturnItem.cogs_amount.is_(None), 1), else_=0)),
            )
            .join(
                SalesReturn,
                (SalesReturn.shop_id == SalesReturnItem.shop_id)
                & (SalesReturn.id == SalesReturnItem.sales_return_id),
            )
            .where(SalesReturnItem.shop_id == shop_id, *ret_cond)
            .group_by(SalesReturnItem.product_id)
        )
    }
    rows = []
    for pid, name, sku, brand, cat_id, cat_name, unit, qty, rev, cost, unknown_lines, bills in sold:
        rq, refund, rcost, runknown = returned.get(pid, (Decimal(0), ZERO, ZERO, 0))
        quantity = (Decimal(qty or 0) - rq).quantize(Q3)
        revenue = _m(rev) - refund
        known = unknown_lines == 0 and runknown == 0
        cogs = (_m(cost) - rcost) if known else None
        rows.append({
            "product_id": pid, "product": name, "sku": sku, "brand": brand or "", "category_id": cat_id, "category": cat_name, "unit": unit,
            "quantity": quantity, "revenue": revenue, "bills": bills, "cogs": cogs, "gross_profit": (revenue - cogs) if cogs is not None else None,
            "profit_note": None if known else INSUFFICIENT_COST,
        })  # fmt: skip
    return rows


PRODUCT_COLUMNS = [
    ("product", "Product", "text"),
    ("sku", "SKU", "text"),
    ("brand", "Brand", "text"),
    ("category", "Category", "text"),
    ("unit", "Unit", "text"),
    ("quantity", "Quantity sold (net of returns)", "quantity"),
    ("revenue", "Revenue (net of returns)", "money"),
    ("bills", "Bills", "integer"),
    ("cogs", "Cost of goods sold", "money"),
    ("gross_profit", "Gross profit", "money"),
]


def products(session: Session, shop_id: int, f: ReportFilters, order: str = "top") -> ReportTable:
    """Top or bottom selling products by quantity, DETAILED bills only. Ties break by revenue then name, so the order is
    stable. `bottom` lists the slowest products that DID sell; products that did not sell at all are dead stock, an
    inventory question."""
    rows = _product_rows(session, shop_id, f)
    rows.sort(
        key=lambda r: (
            (-r["quantity"], -r["revenue"], r["product"].casefold())
            if order != "bottom"
            else (r["quantity"], r["revenue"], r["product"].casefold())
        )
    )
    notes = [
        QUICK_NOTE,
        "Profit is shown only for products whose every sold and returned line has a known cost; otherwise it is Not Available (Insufficient Cost Data).",
    ]
    if f.channel is Channel.QUICK:
        rows, notes = (
            [],
            notes + ["Quick Sales have no product detail, so a quick-sale-only view has no product rows."],
        )
    return _table(
        f,
        "Top-selling products" if order != "bottom" else "Slowest-selling products",
        PRODUCT_COLUMNS,
        rows,
        {"product_id", "category_id", "brand", "customer_id", "payment_method", "channel", "active"},
        "Posted detailed-sale lines and their returns",
        notes,
    )


def _group(rows: list[dict], key: str, label: str) -> list[dict]:
    grouped: dict[str, dict] = {}
    for r in rows:
        g = grouped.setdefault(
            r[key], {label: r[key], "revenue": ZERO, "cogs": ZERO, "unknown": False, "products": 0}
        )
        g["revenue"] += r["revenue"]
        g["products"] += 1
        if r["cogs"] is None:
            g["unknown"] = True
        else:
            g["cogs"] += r["cogs"]
    out = []
    for g in grouped.values():
        out.append(
            {
                label: g[label],
                "revenue": g["revenue"],
                "products": g["products"],
                "gross_profit": None if g["unknown"] else g["revenue"] - g["cogs"],
                "profit_note": INSUFFICIENT_COST if g["unknown"] else None,
            }
        )
    return sorted(out, key=lambda r: (-r["revenue"], str(r[label]).casefold()))


def categories(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    rows = _group(_product_rows(session, shop_id, f), "category", "category")
    cols = [
        ("category", "Category", "text"),
        ("products", "Products sold", "integer"),
        ("revenue", "Revenue (net of returns)", "money"),
        ("gross_profit", "Gross profit", "money"),
    ]
    return _table(
        f,
        "Sales by category",
        cols,
        rows,
        {"product_id", "category_id", "brand", "customer_id", "payment_method", "channel", "active"},
        "Posted detailed-sale lines and their returns",
        [
            QUICK_NOTE,
            "Quantities are not added across categories: a total of kilograms and pieces would mean nothing.",
        ],
    )


def brands(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    raw = _product_rows(session, shop_id, f)
    for r in raw:
        r["brand"] = r["brand"] or "(no brand)"
    rows = _group(raw, "brand", "brand")
    cols = [
        ("brand", "Brand", "text"),
        ("products", "Products sold", "integer"),
        ("revenue", "Revenue (net of returns)", "money"),
        ("gross_profit", "Gross profit", "money"),
    ]
    return _table(
        f,
        "Sales by brand",
        cols,
        rows,
        {"product_id", "category_id", "brand", "customer_id", "payment_method", "channel", "active"},
        "Posted detailed-sale lines and their returns",
        [QUICK_NOTE],
    )


def channels(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Detailed vs quick vs online vs combined revenue."""
    d = daily(
        session,
        shop_id,
        ReportFilters(
            period=f.period, customer_id=f.customer_id, payment_method=f.payment_method, channel=f.channel
        ),
        f.period.start,
        f.period.end,
    )
    det = sum((x["detailed_net"] for x in d.values()), ZERO)
    quick = sum((x["quick_net"] for x in d.values()), ZERO)
    dn, qn = sum(x["detailed_count"] for x in d.values()), sum(x["quick_count"] for x in d.values())
    total = det + quick
    rows = [
        {"channel": "Detailed sales", "revenue": det, "transactions": dn, "share_pct": _pct(det, total)},
        {"channel": "Quick sales", "revenue": quick, "transactions": qn, "share_pct": _pct(quick, total)},
        {"channel": "Online sales", "revenue": None, "transactions": None, "share_pct": None},
        {
            "channel": "Combined revenue (before returns)",
            "revenue": total,
            "transactions": dn + qn,
            "share_pct": _pct(total, total),
        },
    ]
    cols = [
        ("channel", "Channel", "text"),
        ("revenue", "Revenue", "money"),
        ("transactions", "Transactions", "integer"),
        ("share_pct", "Share of combined %", "percent"),
    ]
    return _table(
        f,
        "Sales by channel",
        cols,
        rows,
        {"customer_id", "payment_method", "channel"},
        "Posted sales and quick sales",
        [ONLINE_NOTE, "Offline and detailed/quick are the only channels that exist."],
        total=len(rows),
    )


def payment_methods(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Money received per payment method (the settled part of each sale; the unpaid part is on credit, in khata)."""
    grouped: dict[str, dict] = {}
    for r in finance_ledger_service.movements(session, shop_id, f.period.start, f.period.end):
        if r.event_type is not FinanceEventType.SALE:
            continue
        if (
            f.channel is Channel.DETAILED
            and r.source_type != "SALE"
            or f.channel is Channel.QUICK
            and r.source_type != "QUICK_SALE"
        ):
            continue
        if f.customer_id is not None and r.customer_id != f.customer_id:
            continue
        if f.payment_method and r.payment_method != f.payment_method and r.settled_amount > 0:
            continue
        credit = r.amount - r.settled_amount
        if r.settled_amount > 0:
            g = grouped.setdefault(
                r.payment_method, {"method": r.payment_method, "received": ZERO, "transactions": 0}
            )
            g["received"] += r.settled_amount
            g["transactions"] += 1
        if credit > 0 and not f.payment_method:
            c = grouped.setdefault("CREDIT", {"method": "CREDIT", "received": ZERO, "transactions": 0})
            c["received"] += credit
            c["transactions"] += 1
    rows = sorted(grouped.values(), key=lambda g: (-g["received"], g["method"]))
    labels = {"NOT_RECORDED": "Not recorded", "CREDIT": "On credit (not yet received)"}
    for g in rows:
        g["method"] = labels.get(g["method"], g["method"])
    cols = [
        ("method", "Payment method", "text"),
        ("received", "Amount", "money"),
        ("transactions", "Transactions", "integer"),
    ]
    return _table(
        f,
        "Sales by payment method",
        cols,
        rows,
        {"customer_id", "payment_method", "channel"},
        "The financial ledger view of posted sales",
        ["'On credit' is the unpaid part of credit sales: it is in khata, not yet received."],
    )


def discounts(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    cond = _sale_conditions(Sale, shop_id, f, f.period.start, f.period.end)
    subtotal, bill, promo, net, n = session.execute(
        select(
            func.sum(Sale.subtotal),
            func.sum(Sale.discount),
            func.sum(Sale.promotion_discount),
            func.sum(Sale.total_amount),
            func.count(),
        ).where(*cond)
    ).one()
    line = session.scalar(
        select(func.sum(SaleItem.discount))
        .join(Sale, (Sale.shop_id == SaleItem.shop_id) & (Sale.id == SaleItem.sale_id))
        .where(*cond)
    )
    qcond = _sale_conditions(QuickSale, shop_id, f, f.period.start, f.period.end)
    qgross, qdisc = session.execute(
        select(func.sum(QuickSale.gross_amount), func.sum(QuickSale.discount)).where(*qcond)
    ).one()
    if f.channel is Channel.QUICK:
        subtotal = bill = promo = line = None
    if f.channel is Channel.DETAILED:
        qgross = qdisc = None
    gross_d = _m(subtotal) + _m(line)
    rows = [
        {"kind": "Line discounts (detailed)", "amount": _m(line), "share_pct": _pct(_m(line), gross_d)},
        {
            "kind": "Cashier bill discounts (detailed)",
            "amount": _m(bill),
            "share_pct": _pct(_m(bill), gross_d),
        },
        {"kind": "Offers and coupons (detailed)", "amount": _m(promo), "share_pct": _pct(_m(promo), gross_d)},
        {"kind": "Quick-sale discounts", "amount": _m(qdisc), "share_pct": _pct(_m(qdisc), _m(qgross))},
    ]
    cols = [
        ("kind", "Discount", "text"),
        ("amount", "Amount", "money"),
        ("share_pct", "Share of gross %", "percent"),
    ]
    return _table(
        f,
        "Discount analysis",
        cols,
        rows,
        {"customer_id", "payment_method", "channel"},
        "Posted sales and quick sales",
        [
            "Detailed shares are of gross detailed sales; the quick-sale share is of gross quick sales. The two are not added together."
        ],
        total=len(rows),
    )


def promotions(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Revenue of bills that used a promotion. 'Associated with' only: a bill that used an offer is not evidence the
    offer caused the sale."""
    cond = _sale_conditions(Sale, shop_id, f, f.period.start, f.period.end)
    rows_db = session.execute(
        select(
            SalePromotion.name,
            func.count(func.distinct(Sale.id)),
            func.sum(Sale.total_amount),
            func.sum(SalePromotion.discount_amount),
        )
        .join(Sale, (Sale.shop_id == SalePromotion.shop_id) & (Sale.id == SalePromotion.sale_id))
        .where(*cond)
        .group_by(SalePromotion.name)
    ).all()
    linked_count = (
        session.scalar(
            select(func.count(func.distinct(SalePromotion.sale_id)))
            .join(Sale, (Sale.shop_id == SalePromotion.shop_id) & (Sale.id == SalePromotion.sale_id))
            .where(*cond)
        )
        or 0
    )
    linked_revenue = (
        session.scalar(
            select(func.sum(Sale.total_amount)).where(
                *cond, Sale.id.in_(select(SalePromotion.sale_id).where(SalePromotion.shop_id == shop_id))
            )
        )
        or 0
    )
    total_count, total_revenue = session.execute(
        select(func.count(), func.sum(Sale.total_amount)).where(*cond)
    ).one()
    rows = [
        {"promotion": name, "bills": n, "bill_revenue": _m(rev), "discount_given": _m(disc)}
        for name, n, rev, disc in rows_db
    ]
    rows.sort(key=lambda r: (-r["bill_revenue"], r["promotion"].casefold()))
    rows.append(
        {
            "promotion": "All bills that used any promotion",
            "bills": linked_count,
            "bill_revenue": _m(linked_revenue),
            "discount_given": _m(sum((r["discount_given"] for r in rows), ZERO)),
        }
    )
    rows.append(
        {
            "promotion": "All detailed bills",
            "bills": total_count,
            "bill_revenue": _m(total_revenue),
            "discount_given": None,
        }
    )
    cols = [
        ("promotion", "Promotion", "text"),
        ("bills", "Bills", "integer"),
        ("bill_revenue", "Revenue of those bills", "money"),
        ("discount_given", "Discount given", "money"),
    ]
    return _table(
        f,
        "Promotion-linked sales",
        cols,
        rows,
        {"customer_id", "payment_method", "channel"},
        "Posted detailed sales and their promotion snapshots",
        [
            "Revenue is that of the whole bills that used the promotion, observed together with it. It is not revenue caused by the promotion. A bill using two promotions appears under each.",
            "Quick Sales cannot use promotions.",
        ],
        total=len(rows),
    )


def summary(session: Session, shop_id: int, f: ReportFilters) -> dict:
    """The headline sales figures of the period, on one page."""
    d = daily(session, shop_id, f, f.period.start, f.period.end)
    det = sum((x["detailed_net"] for x in d.values()), ZERO)
    quick = sum((x["quick_net"] for x in d.values()), ZERO)
    dn, qn = sum(x["detailed_count"] for x in d.values()), sum(x["quick_count"] for x in d.values())
    cond = _sale_conditions(Sale, shop_id, f, f.period.start, f.period.end)
    units = session.scalar(
        select(func.sum(SaleItem.quantity))
        .join(Sale, (Sale.shop_id == SaleItem.shop_id) & (Sale.id == SaleItem.sale_id))
        .join(Product, (Product.shop_id == SaleItem.shop_id) & (Product.id == SaleItem.product_id))
        .join(Unit, Unit.id == Product.unit_id)
        .where(*cond, Unit.allows_decimal.is_(False))
    )
    refunds = (
        session.scalar(
            select(func.sum(SalesReturn.total_refund)).where(
                SalesReturn.shop_id == shop_id,
                SalesReturn.status == DocumentStatus.POSTED,
                SalesReturn.return_date >= f.period.start,
                SalesReturn.return_date <= f.period.end,
            )
        )
        or 0
    )
    total = det + quick
    return {
        "period": f.period,
        "comparison": f.comparison,
        "detailed_revenue": det,
        "quick_revenue": quick,
        "online_revenue": None,
        "online_note": ONLINE_NOTE,
        "combined_revenue": total,
        "sales_returns": _m(refunds),
        "net_revenue_after_returns": total - _m(refunds),
        "transactions": dn + qn,
        "detailed_transactions": dn,
        "quick_transactions": qn,
        "average_transaction_value": (total / (dn + qn)).quantize(Decimal("0.01")) if dn + qn else None,
        "units_per_transaction": (Decimal(units) / dn).quantize(Decimal("0.01"))
        if dn and units is not None
        else None,
        "units_per_transaction_note": "Detailed bills only, counting products sold by whole units; Quick Sales have no items.",
        "return_rate_pct": _pct(_m(refunds), total),
        "notes": [QUICK_NOTE, ONLINE_NOTE],
    }
