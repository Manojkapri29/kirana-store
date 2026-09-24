"""Advanced inventory analytics. THE INVENTORY TRANSACTION LEDGER IS THE ONLY SOURCE OF STOCK: current stock, movement,
turnover, ageing and stock-outs are all read from it (through `inventory_service` where it already offers the reading, or
directly, read-only, from the ledger). Cost always comes from the costing service's average cost; nothing here stores or
recomputes a second copy of stock or cost.

Quick Sales never appear: they have no product lines and create no inventory movement. Units in kg and pieces are never
added together: totals are per product, or restricted to count-sold products where that is stated.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Category, InventoryTransaction, Product, StockCount, StockCountItem
from app.models.enums import InventoryTxnType, StockCountStatus
from app.reporting.filters import ReportFilters, ReportTable
from app.reporting.sales import _table
from app.services import (
    ai_insights_service,
    analytics_service,
    inventory_intelligence_service,
    inventory_service,
)

ZERO = Decimal("0.00")
Q3 = Decimal("0.001")
NO_COST = "Not Available"

_HONOURED = {"product_id", "category_id", "brand", "active"}


def _rows(session: Session, shop_id: int, f: ReportFilters):  # noqa: ANN202
    active = True if f.active is None else f.active
    rows, _ = inventory_service.list_inventory(session, shop_id, active=active, limit=None)
    keep = []
    for r in rows:
        if f.product_id is not None and r.product_id != f.product_id:
            continue
        if f.brand and (r.brand or "") != f.brand:
            continue
        keep.append(r)
    if f.category_id is not None:
        allowed = set(
            session.scalars(
                select(Product.id).where(Product.shop_id == shop_id, Product.category_id == f.category_id)
            )
        )
        keep = [r for r in keep if r.product_id in allowed]
    return keep


def _value(stock: Decimal, cost: Decimal | None) -> Decimal | None:
    return (stock * cost).quantize(Decimal("0.01")) if cost is not None else None


def stock(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Current stock and value per product (the position right now: past positions are rebuilt from the ledger, not stored)."""
    rows = [
        {
            "product_id": r.product_id,
            "product": r.name,
            "sku": r.sku,
            "brand": r.brand or "",
            "category": r.category_name,
            "unit": r.unit_code,
            "current_stock": r.current_stock,
            "reorder_level": r.reorder_level,
            "status": r.status.value,
            "average_cost": r.avg_cost,
            "stock_value": _value(r.current_stock, r.avg_cost),
        }
        for r in _rows(session, shop_id, f)
    ]
    rows.sort(key=lambda r: (r["stock_value"] is None, -(r["stock_value"] or ZERO), r["product"].casefold()))
    cols = [
        ("product", "Product", "text"),
        ("sku", "SKU", "text"),
        ("category", "Category", "text"),
        ("unit", "Unit", "text"),
        ("current_stock", "Current stock", "quantity"),
        ("reorder_level", "Reorder level", "quantity"),
        ("status", "Status", "text"),
        ("average_cost", "Average cost", "money"),
        ("stock_value", "Stock value", "money"),
    ]
    unknown = sum(1 for r in rows if r["stock_value"] is None and r["current_stock"] > 0)
    notes = [
        "Average cost comes from the costing service. Value is Not Available for stock whose cost is unknown; it is never counted as zero."
    ]
    if unknown:
        notes.append(f"{unknown} product(s) with stock have no known cost.")
    return _table(
        f, "Current stock", cols, rows, _HONOURED, "Inventory transaction ledger and average cost", notes
    )


def category_summary(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Stock value per category: the first drill-down step (category -> product -> transactions)."""
    grouped: dict[str, dict] = {}
    cat_ids = dict(
        session.execute(select(Category.name, Category.id).where(Category.shop_id == shop_id)).all()
    )
    for r in _rows(session, shop_id, f):
        g = grouped.setdefault(
            r.category_name,
            {
                "category_id": cat_ids.get(r.category_name),
                "category": r.category_name,
                "products": 0,
                "in_stock": 0,
                "out_of_stock": 0,
                "stock_value": ZERO,
                "unknown_cost": 0,
            },
        )
        g["products"] += 1
        g["in_stock"] += 1 if r.current_stock > 0 else 0
        g["out_of_stock"] += 1 if r.current_stock <= 0 else 0
        v = _value(r.current_stock, r.avg_cost)
        if v is None and r.current_stock > 0:
            g["unknown_cost"] += 1
        elif v is not None:
            g["stock_value"] += v
    rows = sorted(grouped.values(), key=lambda g: (-g["stock_value"], g["category"].casefold()))
    for g in rows:
        g["value_note"] = (
            f"{g['unknown_cost']} product(s) with unknown cost are left out" if g["unknown_cost"] else None
        )
    cols = [
        ("category", "Category", "text"),
        ("products", "Products", "integer"),
        ("in_stock", "In stock", "integer"),
        ("out_of_stock", "Out of stock", "integer"),
        ("stock_value", "Stock value (known cost)", "money"),
        ("unknown_cost", "Products with unknown cost", "integer"),
    ]
    return _table(
        f, "Inventory by category", cols, rows, _HONOURED, "Inventory transaction ledger and average cost"
    )


def _qty_at(
    session: Session, shop_id: int, day: date, product_ids: set[int] | None = None
) -> dict[int, Decimal]:
    q = (
        select(InventoryTransaction.product_id, func.sum(InventoryTransaction.qty_delta))
        .where(InventoryTransaction.shop_id == shop_id, InventoryTransaction.txn_date <= day)
        .group_by(InventoryTransaction.product_id)
    )
    return {pid: Decimal(v or 0) for pid, v in session.execute(q)}


def turnover(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Per product: units sold / average stock ((opening + closing) / 2), read from the ledger and detailed sales."""
    sold = {
        s.product_id: s
        for s in analytics_service.product_sales(session, shop_id, f.period.start, f.period.end)
    }
    opening = _qty_at(session, shop_id, f.period.start - timedelta(days=1))
    closing = _qty_at(session, shop_id, f.period.end)
    rows = []
    for r in _rows(session, shop_id, f):
        s = sold.get(r.product_id)
        qty = s.quantity if s else Decimal("0.000")
        avg = (opening.get(r.product_id, Decimal(0)) + closing.get(r.product_id, Decimal(0))) / 2
        turns = (qty / avg).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN) if avg > 0 else None
        cover = (avg / (qty / f.period.days)).quantize(Decimal("0.1")) if qty > 0 and avg > 0 else None
        if qty == 0 and avg <= 0:
            continue
        rows.append(
            {
                "product_id": r.product_id,
                "product": r.name,
                "unit": r.unit_code,
                "opening_stock": opening.get(r.product_id, Decimal(0)).quantize(Q3),
                "closing_stock": closing.get(r.product_id, Decimal(0)).quantize(Q3),
                "units_sold": qty,
                "turnover": turns,
                "days_of_cover": cover,
                "note": None if turns is not None else "Insufficient Data (no average stock)",
            }
        )
    rows.sort(key=lambda x: (x["turnover"] is None, -(x["turnover"] or 0), x["product"].casefold()))
    cols = [
        ("product", "Product", "text"),
        ("unit", "Unit", "text"),
        ("opening_stock", "Opening stock", "quantity"),
        ("closing_stock", "Closing stock", "quantity"),
        ("units_sold", "Sold (net of returns)", "quantity"),
        ("turnover", "Turnover (times)", "ratio"),
        ("days_of_cover", "Days of cover", "text"),
    ]
    cols = [(key, label, "text" if kind == "ratio" else kind) for key, label, kind in cols]
    return _table(
        f,
        "Stock turnover",
        cols,
        rows,
        _HONOURED,
        "Inventory ledger and posted detailed sales",
        [
            "Turnover = units sold / average of opening and closing stock for the period; not annualised.",
            "Quick Sales have no product detail and are not part of any turnover.",
        ],
    )


def movers(session: Session, shop_id: int, f: ReportFilters, kind: str) -> ReportTable:
    """Fast-moving, slow-moving or dead stock, through the inventory intelligence service (same definitions everywhere)."""
    end = f.period.end
    days = max(f.period.days, 1)
    if kind == "fast":
        found = inventory_intelligence_service.fast_moving(session, shop_id, end, days, limit=100_000)
    elif kind == "slow":
        found = inventory_intelligence_service.slow_moving(session, shop_id, end, days)
    elif kind == "dead":
        found = inventory_intelligence_service.dead_stock(session, shop_id, end, days)
    else:
        from app.services.errors import InvalidInputError

        raise InvalidInputError("Choose fast, slow or dead.", field="kind")
    allowed = {r.product_id for r in _rows(session, shop_id, f)}
    rows = [
        {
            "product_id": m.product_id,
            "product": m.name,
            "sku": m.sku,
            "unit": m.unit_code,
            "current_stock": m.current_stock,
            "quantity_sold": m.quantity_sold,
            "revenue": m.revenue,
            "days_of_cover": m.days_of_cover,
            "stock_value": m.stock_value,
        }
        for m in found
        if m.product_id in allowed
    ]
    cols = [
        ("product", "Product", "text"),
        ("sku", "SKU", "text"),
        ("unit", "Unit", "text"),
        ("current_stock", "Current stock", "quantity"),
        ("quantity_sold", "Sold in period", "quantity"),
        ("revenue", "Revenue", "money"),
        ("days_of_cover", "Days of cover", "text"),
        ("stock_value", "Stock value", "money"),
    ]
    titles = {"fast": "Fast-moving products", "slow": "Slow-moving products", "dead": "Dead stock"}
    notes = [
        "'Slow' and 'dead' describe selling pace over the chosen period, not product quality.",
        "Stock is the current position; sales are for the chosen period.",
    ]
    return _table(
        f,
        titles[kind],
        cols,
        rows,
        _HONOURED,
        "Inventory intelligence over the ledger and detailed sales",
        notes,
    )


def aging(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    allowed = {r.product_id for r in _rows(session, shop_id, f)}
    rows = [
        {
            "product_id": a.product_id,
            "product": a.name,
            "sku": a.sku,
            "unit": a.unit_code,
            "current_stock": a.current_stock,
            "days_since_last_inbound": a.days_since_last_inbound,
            "stock_value": a.stock_value,
        }
        for a in inventory_intelligence_service.stock_aging(session, shop_id, f.period.end)
        if a.product_id in allowed
    ]
    cols = [
        ("product", "Product", "text"),
        ("sku", "SKU", "text"),
        ("unit", "Unit", "text"),
        ("current_stock", "Current stock", "quantity"),
        ("days_since_last_inbound", "Days since last stock in", "integer"),
        ("stock_value", "Stock value", "money"),
    ]
    return _table(
        f,
        "Stock ageing",
        cols,
        rows,
        _HONOURED,
        "Inventory ledger (last inbound entry)",
        ["An estimate from the last inbound entry: there is no batch tracking."],
    )


def movement(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """What came in and went out in the period, by transaction type (the ledger's own vocabulary)."""
    t = InventoryTransaction
    cond = [t.shop_id == shop_id, t.txn_date >= f.period.start, t.txn_date <= f.period.end]
    if f.product_id is not None:
        cond.append(t.product_id == f.product_id)
    if f.category_id is not None:
        cond.append(
            t.product_id.in_(
                select(Product.id).where(Product.shop_id == shop_id, Product.category_id == f.category_id)
            )
        )
    rows_db = session.execute(
        select(
            t.txn_type,
            func.count(),
            func.sum(case((t.qty_delta > 0, t.qty_delta), else_=0)),
            func.sum(case((t.qty_delta < 0, t.qty_delta), else_=0)),
        )
        .where(*cond)
        .group_by(t.txn_type)
    ).all()
    rows = [
        {
            "txn_type": tt.value,
            "movements": n,
            "quantity_in": Decimal(qin or 0).quantize(Q3),
            "quantity_out": (-Decimal(qout or 0)).quantize(Q3),
        }
        for tt, n, qin, qout in rows_db
    ]
    rows.sort(key=lambda r: r["txn_type"])
    cols = [
        ("txn_type", "Type", "text"),
        ("movements", "Movements", "integer"),
        ("quantity_in", "Quantity in", "quantity"),
        ("quantity_out", "Quantity out", "quantity"),
    ]
    return _table(
        f,
        "Stock movement",
        cols,
        rows,
        {"product_id", "category_id"},
        "Inventory transaction ledger",
        [
            "Quantities across products with different units are added here only as a count of movement; use the product view for meaningful totals."
        ],
    )


def purchase_vs_sales(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Per product: units bought (purchases less purchase returns) against units sold (sales less sales returns)."""
    t = InventoryTransaction
    cond = [
        t.shop_id == shop_id,
        t.txn_date >= f.period.start,
        t.txn_date <= f.period.end,
        t.txn_type.in_(
            [
                InventoryTxnType.PURCHASE,
                InventoryTxnType.PURCHASE_RETURN,
                InventoryTxnType.SALE,
                InventoryTxnType.SALE_RETURN,
            ]
        ),
    ]
    per: dict[int, dict[str, Decimal]] = {}
    for pid, tt, q in session.execute(
        select(t.product_id, t.txn_type, func.sum(t.qty_delta))
        .where(*cond)
        .group_by(t.product_id, t.txn_type)
    ):
        per.setdefault(pid, {})[tt.value] = Decimal(q or 0)
    allowed = {r.product_id: r for r in _rows(session, shop_id, f)}
    rows = []
    for pid, d in per.items():
        if pid not in allowed:
            continue
        bought = d.get("PURCHASE", Decimal(0)) + d.get("PURCHASE_RETURN", Decimal(0))
        sold = -(d.get("SALE", Decimal(0)) + d.get("SALE_RETURN", Decimal(0)))
        rows.append(
            {
                "product_id": pid,
                "product": allowed[pid].name,
                "unit": allowed[pid].unit_code,
                "units_bought": bought.quantize(Q3),
                "units_sold": sold.quantize(Q3),
                "difference": (bought - sold).quantize(Q3),
            }
        )
    rows.sort(key=lambda r: (-abs(r["difference"]), r["product"].casefold()))
    cols = [
        ("product", "Product", "text"),
        ("unit", "Unit", "text"),
        ("units_bought", "Bought (net of returns)", "quantity"),
        ("units_sold", "Sold (net of returns)", "quantity"),
        ("difference", "Bought - sold", "quantity"),
    ]
    return _table(f, "Purchases against sales", cols, rows, _HONOURED, "Inventory transaction ledger")


def adjustments(session: Session, shop_id: int, f: ReportFilters, bucket: str = "month") -> ReportTable:
    t = InventoryTransaction
    rows_db = session.execute(
        select(t.txn_date, t.reason_code, t.qty_delta).where(
            t.shop_id == shop_id,
            t.txn_type == InventoryTxnType.ADJUSTMENT,
            t.txn_date >= f.period.start,
            t.txn_date <= f.period.end,
        )
    ).all()
    grouped: dict[tuple[str, str], dict] = {}
    for day, reason, q in rows_db:
        key = (
            day.strftime("%Y-%m") if bucket == "month" else day.isoformat(),
            reason.value if reason else "—",
        )
        g = grouped.setdefault(
            key,
            {
                "period": key[0],
                "reason": key[1],
                "adjustments": 0,
                "quantity_in": Decimal(0),
                "quantity_out": Decimal(0),
            },
        )
        g["adjustments"] += 1
        if q > 0:
            g["quantity_in"] += q
        else:
            g["quantity_out"] += -q
    rows = sorted(grouped.values(), key=lambda g: (g["period"], g["reason"]))
    cols = [
        ("period", "Period", "text"),
        ("reason", "Reason", "text"),
        ("adjustments", "Adjustments", "integer"),
        ("quantity_in", "Quantity in", "quantity"),
        ("quantity_out", "Quantity out", "quantity"),
    ]
    return _table(
        f,
        "Adjustment trend",
        cols,
        rows,
        set(),
        "Inventory transaction ledger (ADJUSTMENT rows)",
        ["Adjustments are corrections: a rise is worth a look, not a conclusion."],
    )


def count_variance(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Posted stock counts in the period: how far counted stock differed from expected."""
    q = (
        select(
            StockCount.id,
            StockCount.title,
            StockCount.posted_at,
            func.count(StockCountItem.id),
            func.sum(case((StockCountItem.variance != 0, 1), else_=0)),
            func.sum(func.abs(StockCountItem.variance) * StockCountItem.unit_cost_snapshot),
        )
        .join(
            StockCountItem,
            (StockCountItem.shop_id == StockCount.shop_id) & (StockCountItem.stock_count_id == StockCount.id),
        )
        .where(StockCount.shop_id == shop_id, StockCount.status == StockCountStatus.POSTED)
        .group_by(StockCount.id, StockCount.title, StockCount.posted_at)
    )
    rows = []
    for cid, title, posted, items, differing, _ in session.execute(q):
        if posted is None or not (f.period.start <= posted.date() <= f.period.end):
            continue
        rows.append(
            {
                "stock_count_id": cid,
                "title": title,
                "posted_on": posted.date(),
                "items_counted": items,
                "items_with_difference": differing or 0,
            }
        )
    rows.sort(key=lambda r: r["posted_on"], reverse=True)
    cols = [
        ("title", "Count", "text"),
        ("posted_on", "Posted on", "date"),
        ("items_counted", "Items counted", "integer"),
        ("items_with_difference", "Items that differed", "integer"),
    ]
    return _table(
        f,
        "Stock count variance",
        cols,
        rows,
        set(),
        "Posted stock counts",
        ["The value of the difference is on the stock-count screen; this report counts items that differed."],
    )


def reorder(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    allowed = {r.product_id for r in _rows(session, shop_id, f)}
    rows = [
        {
            "product_id": r.product_id,
            "product": r.name,
            "unit": r.unit_code,
            "current_stock": r.current_stock,
            "reorder_level": r.reorder_level,
            "sold_recently": r.sold_recently,
            "days_of_cover": r.days_of_cover,
            "suggested_quantity": r.suggested_quantity,
            "supplier": r.supplier_name or "",
            "latest_purchase_price": r.latest_purchase_price,
        }
        for r in ai_insights_service.reorder_recommendations(session, shop_id, f.period.end)
        if r.product_id in allowed
    ]
    cols = [
        ("product", "Product", "text"),
        ("unit", "Unit", "text"),
        ("current_stock", "Current stock", "quantity"),
        ("reorder_level", "Reorder level", "quantity"),
        ("sold_recently", "Sold recently", "quantity"),
        ("days_of_cover", "Days of cover", "text"),
        ("suggested_quantity", "Suggested quantity", "quantity"),
        ("supplier", "Supplier", "text"),
        ("latest_purchase_price", "Latest purchase price", "money"),
    ]
    return _table(
        f,
        "Reorder recommendations",
        cols,
        rows,
        _HONOURED,
        "The existing reorder recommendation service",
        ["A suggestion for a person to review: nothing is ordered."],
    )


def stock_outs(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Stock-out history: when a product's ledger balance reached zero or below inside the period, and for how many days
    it stayed there until it was replenished or the period ended."""
    t = InventoryTransaction
    allowed = {r.product_id: r for r in _rows(session, shop_id, f)}
    if not allowed:
        return _table(f, "Stock-out history", [], [], _HONOURED, "Inventory transaction ledger")
    opening = _qty_at(session, shop_id, f.period.start - timedelta(days=1))
    moves: dict[int, list[tuple[date, Decimal]]] = {}
    for pid, day, q in session.execute(
        select(t.product_id, t.txn_date, t.qty_delta)
        .where(t.shop_id == shop_id, t.txn_date >= f.period.start, t.txn_date <= f.period.end)
        .order_by(t.txn_date, t.id)
    ):
        moves.setdefault(pid, []).append((day, q))
    rows = []
    for pid, product in allowed.items():
        balance = opening.get(pid, Decimal(0))
        out_since: date | None = f.period.start if balance <= 0 and pid in opening else None
        events = 0
        days_out = 0
        first: date | None = out_since
        for day, q in moves.get(pid, []):
            balance += q
            if balance <= 0 and out_since is None:
                out_since = day
                first = first or day
                events += 1
            elif balance > 0 and out_since is not None:
                days_out += (day - out_since).days
                out_since = None
        if out_since is not None:
            days_out += (f.period.end - out_since).days + 1
        if first is not None:
            rows.append(
                {
                    "product_id": pid,
                    "product": product.name,
                    "first_stock_out": first,
                    "stock_out_events": events or (1 if first == f.period.start else 0),
                    "days_out_of_stock": days_out,
                    "still_out": out_since is not None,
                }
            )
    rows.sort(key=lambda r: (-r["days_out_of_stock"], r["product"].casefold()))
    cols = [
        ("product", "Product", "text"),
        ("first_stock_out", "First out of stock", "date"),
        ("stock_out_events", "Times", "integer"),
        ("days_out_of_stock", "Days out of stock", "integer"),
        ("still_out", "Still out", "text"),
    ]
    return _table(
        f,
        "Stock-out history",
        cols,
        rows,
        _HONOURED,
        "Inventory transaction ledger (running balance)",
        [
            "Rebuilt from the ledger's running balance. Days are counted between the ledger movement that emptied the shelf and the one that refilled it."
        ],
    )


def summary(session: Session, shop_id: int, f: ReportFilters, today: date) -> dict:
    health = inventory_intelligence_service.inventory_health(
        session, shop_id, min(f.period.end, today), max(f.period.days, 1)
    )
    return {
        "period": f.period,
        "health": health,
        "notes": [
            "Stock and value are the current position; movement and pace are for the chosen period.",
            "Products with stock but no known cost are counted in 'products_without_cost' and left out of the value.",
        ],
    }
