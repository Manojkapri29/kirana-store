"""Supplier analytics: read-only figures from POSTED purchases (`purchases`/`purchase_items`), the only
record of what a shop actually paid a supplier. There is no separate receiving/delivery-date field in the
architecture, so delivery performance ("were they on time?") is reported as unavailable rather than
guessed from the purchase date, which is when the purchase was entered, not necessarily when goods
arrived.

Suppliers are never ranked "best" here: the numbers (total value, frequency, average price, price history)
are shown side by side, and a screen or a person draws their own conclusion."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, Purchase, PurchaseItem, Supplier
from app.models.enums import PurchaseStatus
from app.services.errors import NotFoundError

ZERO = Decimal("0")
DELIVERY_PERFORMANCE_NOTE = (
    "Delivery performance data unavailable: the system does not record a separate receiving date."
)


@dataclass(frozen=True)
class SupplierAnalytics:
    supplier_id: int
    name: str
    is_active: bool
    purchase_count: int
    total_value: Decimal
    average_purchase_value: Decimal | None
    supplied_product_count: int
    first_purchase_date: date | None
    last_purchase_date: date | None
    delivery_performance_note: str = DELIVERY_PERFORMANCE_NOTE


@dataclass(frozen=True)
class PriceHistoryPoint:
    purchase_id: int
    purchase_no: str | None
    purchase_date: date
    unit_cost: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class ProductPriceHistory:
    product_id: int
    product_name: str
    supplier_id: int
    supplier_name: str
    points: list[PriceHistoryPoint]  # oldest first
    lowest: Decimal
    highest: Decimal
    latest: Decimal
    average: Decimal


def list_analytics(session: Session, shop_id: int, *, limit: int | None = 100) -> list[SupplierAnalytics]:
    """One row per supplier, all from a handful of GROUP BY queries (not one query per supplier)."""
    suppliers = {s.id: s for s in session.scalars(select(Supplier).where(Supplier.shop_id == shop_id))}
    if not suppliers:
        return []
    totals = {
        row[0]: row[1:]
        for row in session.execute(
            select(
                Purchase.supplier_id, func.count(), func.coalesce(func.sum(Purchase.total_amount), 0),
                func.min(Purchase.purchase_date), func.max(Purchase.purchase_date),
            )
            .where(Purchase.shop_id == shop_id, Purchase.status == PurchaseStatus.POSTED)
            .group_by(Purchase.supplier_id)
        )
    }  # fmt: skip
    product_counts = dict(
        session.execute(
            select(Purchase.supplier_id, func.count(func.distinct(PurchaseItem.product_id)))
            .join(
                PurchaseItem,
                (PurchaseItem.shop_id == Purchase.shop_id) & (PurchaseItem.purchase_id == Purchase.id),
            )
            .where(Purchase.shop_id == shop_id, Purchase.status == PurchaseStatus.POSTED)
            .group_by(Purchase.supplier_id)
        ).all()
    )
    out = []
    for supplier_id, supplier in suppliers.items():
        count, total, first, last = totals.get(supplier_id, (0, Decimal("0"), None, None))
        total = Decimal(total or 0)
        out.append(
            SupplierAnalytics(
                supplier_id=supplier_id, name=supplier.name, is_active=supplier.is_active,
                purchase_count=count, total_value=total,
                average_purchase_value=(total / count).quantize(Decimal("0.01")) if count else None,
                supplied_product_count=product_counts.get(supplier_id, 0),
                first_purchase_date=first, last_purchase_date=last,
            )
        )  # fmt: skip
    out.sort(key=lambda a: (-a.total_value, a.name.casefold()))
    return out[:limit] if limit is not None else out


def analytics_for(session: Session, shop_id: int, supplier_id: int) -> SupplierAnalytics:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None or supplier.shop_id != shop_id:
        raise NotFoundError("Supplier not found")
    for row in list_analytics(session, shop_id, limit=None):
        if row.supplier_id == supplier_id:
            return row
    return SupplierAnalytics(supplier_id, supplier.name, supplier.is_active, 0, ZERO, None, 0, None, None)


def price_history(
    session: Session, shop_id: int, product_id: int, supplier_id: int, *, limit: int = 50
) -> ProductPriceHistory:
    """Recorded purchase costs of one product from one supplier, oldest first, with lowest/highest/latest/
    average.

    These are the actual `unit_cost` values from posted purchase lines: not the MRP, not the selling price
    and not the weighted-average cost the product carries today (which blends every supplier together).
    """
    product = session.get(Product, product_id)
    supplier = session.get(Supplier, supplier_id)
    if product is None or product.shop_id != shop_id:
        raise NotFoundError("Product not found")
    if supplier is None or supplier.shop_id != shop_id:
        raise NotFoundError("Supplier not found")
    rows = session.execute(
        select(PurchaseItem, Purchase)
        .join(
            Purchase, (Purchase.shop_id == PurchaseItem.shop_id) & (Purchase.id == PurchaseItem.purchase_id)
        )
        .where(
            PurchaseItem.shop_id == shop_id, PurchaseItem.product_id == product_id,
            Purchase.supplier_id == supplier_id, Purchase.status == PurchaseStatus.POSTED,
        )
        .order_by(Purchase.purchase_date, Purchase.id)
        .limit(limit)
    ).all()  # fmt: skip
    points = [
        PriceHistoryPoint(p.id, p.purchase_no, p.purchase_date, item.unit_cost, item.quantity)
        for item, p in rows
    ]
    costs = [pt.unit_cost for pt in points]
    if not costs:
        return ProductPriceHistory(
            product_id, product.name, supplier_id, supplier.name, [], ZERO, ZERO, ZERO, ZERO
        )
    average = (sum(costs, ZERO) / len(costs)).quantize(Decimal("0.01"))
    return ProductPriceHistory(
        product_id,
        product.name,
        supplier_id,
        supplier.name,
        points,
        min(costs),
        max(costs),
        costs[-1],
        average,
    )
