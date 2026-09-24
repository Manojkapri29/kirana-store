"""Inventory intelligence: read-only metrics worked out from the inventory ledger (through
`inventory_service`) and sales history (through `analytics_service`). Nothing here is a second source of
truth for stock — every current-stock number still comes from
`inventory_service.list_inventory`/`get_stock`, and nothing here writes anything.

Periods are configurable (the caller passes how many days back to look); there is no one "correct"
definition of fast or slow moving, so a screen or the AI can ask for 7, 30, 60 or 90 days and get an
honestly different answer, each one saying what period it used. Money figures are "Not Available" (never
0) wherever a product's cost is unknown.

Stock aging is a documented estimate, not batch tracking: the architecture has no lot or batch numbers, so
"age" here means days since the most recent transaction that added stock to a product (an opening entry, a
purchase or a positive adjustment) — the oldest a single unit could be, not necessarily the age of the
units actually left on the shelf once a product has been part-sold and part-restocked. The wording always
says so."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services import analytics_service, inventory_service

ZERO = Decimal("0")
DEFAULT_PERIODS = (7, 30, 60, 90)


@dataclass(frozen=True)
class MoverRow:
    product_id: int
    name: str
    sku: str
    unit_code: str
    current_stock: Decimal
    quantity_sold: Decimal
    revenue: Decimal
    days_of_cover: Decimal | None  # current stock / (quantity_sold / period_days); None when nothing sold
    stock_value: Decimal | None  # None when average cost is unknown


@dataclass(frozen=True)
class AgingRow:
    product_id: int
    name: str
    sku: str
    unit_code: str
    current_stock: Decimal
    days_since_last_inbound: (
        int | None
    )  # None when the product has never received stock (should not happen in practice)
    stock_value: Decimal | None


@dataclass(frozen=True)
class InventoryHealth:
    period_days: int
    total_products: int
    in_stock: int
    low_stock: int
    out_of_stock: int
    total_stock_value: Decimal | None
    products_without_cost: int
    fast_moving_count: int
    slow_moving_count: int
    dead_stock_count: int
    risk_stockout: int  # in stock but selling out within the period at the recent pace
    risk_overstock: int  # far more stock than the recent selling pace would use up


def _period(today: date, days: int) -> tuple[date, date]:
    return today - timedelta(days=days - 1), today


def stock_value(session: Session, shop_id: int) -> tuple[Decimal | None, int]:
    """Total value of stock on hand at average cost, and how many active products with stock have no known
    cost.

    Returns `(None, count)` only when EVERY product with stock has no known cost; otherwise the value is the
    sum of what is known, and `count` says how many products are left out of it (so the number is never
    silently short).
    """
    rows, _ = inventory_service.list_inventory(session, shop_id, active=True, limit=None)
    held = [r for r in rows if r.current_stock > 0]
    known = [r for r in held if r.avg_cost is not None]
    missing = len(held) - len(known)
    if not held:
        return ZERO, 0
    if not known:
        return None, missing
    return sum((r.current_stock * r.avg_cost for r in known if r.avg_cost is not None), ZERO), missing


def _movers(session: Session, shop_id: int, today: date, days: int) -> list[MoverRow]:
    """Every active product with current stock or recent sales, ranked fastest to slowest, for one period."""
    start, end = _period(today, days)
    rows, _ = inventory_service.list_inventory(session, shop_id, active=True, limit=None)
    stock_by_product = {r.product_id: r for r in rows}
    sold = {s.product_id: s for s in analytics_service.product_sales(session, shop_id, start, end)}
    out: list[MoverRow] = []
    for product_id, row in stock_by_product.items():
        sale = sold.get(product_id)
        quantity_sold = sale.quantity if sale else ZERO
        revenue = sale.revenue if sale else ZERO
        per_day = quantity_sold / days if days > 0 else ZERO
        cover = (row.current_stock / per_day) if per_day > 0 else None
        value = (row.current_stock * row.avg_cost) if row.avg_cost is not None else None
        if quantity_sold <= 0 and row.current_stock <= 0:
            continue  # nothing to say: no stock and nothing sold
        out.append(
            MoverRow(
                product_id, row.name, row.sku, row.unit_code, row.current_stock, quantity_sold, revenue,
                cover.quantize(Decimal("0.1")) if cover is not None else None,
                value.quantize(Decimal("0.01")) if value is not None else None,
            )
        )  # fmt: skip
    return out


def fast_moving(
    session: Session, shop_id: int, today: date, days: int = 30, limit: int = 20
) -> list[MoverRow]:
    """Highest units sold in the period, among products that still have stock (nothing to reorder from an
    empty shelf)."""
    movers = [m for m in _movers(session, shop_id, today, days) if m.quantity_sold > 0]
    return sorted(movers, key=lambda m: (-m.quantity_sold, m.name.casefold()))[:limit]


def slow_moving(
    session: Session, shop_id: int, today: date, days: int = 60, cover_multiplier: Decimal = Decimal("3")
) -> list[MoverRow]:
    """Stock on hand that would take unusually long to sell through at the period's pace: "low recent sales
    velocity", never "bad" or "write off". A product with plenty of stock and either no sales, or more than
    `cover_multiplier` times the period's length of cover, qualifies."""
    ceiling = Decimal(days) * cover_multiplier
    out = [
        m
        for m in _movers(session, shop_id, today, days)
        if m.current_stock > 0 and (m.days_of_cover is None or m.days_of_cover > ceiling)
    ]
    return sorted(
        out,
        key=lambda m: (m.stock_value is None, -(m.stock_value or ZERO), -m.current_stock, m.name.casefold()),
    )


def dead_stock(session: Session, shop_id: int, today: date, days: int = 90) -> list[MoverRow]:
    """Stock on hand with NO sales at all in the period (a stricter, unambiguous case of slow moving)."""
    out = [m for m in _movers(session, shop_id, today, days) if m.current_stock > 0 and m.quantity_sold == 0]
    return sorted(out, key=lambda m: (m.stock_value is None, -(m.stock_value or ZERO), m.name.casefold()))


def stock_aging(session: Session, shop_id: int, today: date) -> list[AgingRow]:
    """See the module docstring: an estimate from the last inbound ledger entry, not real batch tracking."""
    rows, _ = inventory_service.list_inventory(session, shop_id, active=True, limit=None)
    held = [r for r in rows if r.current_stock > 0]
    if not held:
        return []
    last_inbound = inventory_service.last_inbound_dates(session, shop_id, [r.product_id for r in held])
    out = []
    for r in held:
        last = last_inbound.get(r.product_id)
        age = (today - last).days if last else None
        value = (r.current_stock * r.avg_cost).quantize(Decimal("0.01")) if r.avg_cost is not None else None
        out.append(AgingRow(r.product_id, r.name, r.sku, r.unit_code, r.current_stock, age, value))
    return sorted(out, key=lambda a: (a.days_since_last_inbound is None, -(a.days_since_last_inbound or 0)))


def inventory_health(session: Session, shop_id: int, today: date, days: int = 30) -> InventoryHealth:
    """One summary screen's worth of numbers, all for the same period, all from the functions above."""
    rows, total = inventory_service.list_inventory(session, shop_id, active=True, limit=None)
    status = inventory_service.StockStatus
    value, missing = stock_value(session, shop_id)
    fast = fast_moving(session, shop_id, today, days, limit=10_000)
    slow = slow_moving(session, shop_id, today, days)
    dead = dead_stock(session, shop_id, today, days)
    reorder_risk = sum(
        1 for r in rows if r.current_stock > 0 and r.current_stock <= r.reorder_level
    )  # already selling close to nothing left
    return InventoryHealth(
        period_days=days,
        total_products=total,
        in_stock=sum(1 for r in rows if r.status is status.IN_STOCK),
        low_stock=sum(1 for r in rows if r.status is status.LOW_STOCK),
        out_of_stock=sum(1 for r in rows if r.status is status.OUT_OF_STOCK),
        total_stock_value=value,
        products_without_cost=missing,
        fast_moving_count=len(fast),
        slow_moving_count=len(slow),
        dead_stock_count=len(dead),
        risk_stockout=reorder_risk,
        risk_overstock=len(slow),
    )
