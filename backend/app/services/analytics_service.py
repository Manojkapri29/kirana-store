"""Read-only business analytics: questions the reports do not answer yet, asked of the ledgers and documents.

Everything here only reads, is scoped to one shop, and returns plain numbers. The AI layer calls these (and
the
existing report services) as its tools; it never writes SQL of its own. Only POSTED documents count, and
returns are
taken off (BUSINESS_RULES R5), the same as the sales reports.

Revenue per product is "after line discounts and offers, before any discount on the whole bill", because a
bill-level
discount belongs to the bill, not to one line.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    Product,
    Purchase,
    PurchaseReturn,
    Sale,
    SaleItem,
    SalesReturn,
    SalesReturnItem,
    Supplier,
    Unit,
)
from app.models.enums import DocumentStatus, PurchaseStatus, SaleStatus

ZERO = Decimal("0.00")
ZERO_QTY = Decimal("0.000")


@dataclass(frozen=True)
class ProductSales:
    product_id: int
    name: str
    sku: str
    category_id: int
    category_name: str
    unit_code: str
    quantity: Decimal  # sold less returned
    revenue: Decimal  # after line discounts and offers, less refunds


@dataclass(frozen=True)
class CategorySales:
    category_id: int
    name: str
    quantity: Decimal
    revenue: Decimal


@dataclass(frozen=True)
class SupplierPurchases:
    supplier_id: int
    name: str
    purchases: int
    total: Decimal


@dataclass(frozen=True)
class PurchaseTotals:
    count: int
    total: Decimal
    returns_count: int
    returns_total: Decimal
    by_supplier: list[SupplierPurchases]

    @property
    def net(self) -> Decimal:
        return self.total - self.returns_total


def _money(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else ZERO


def _qty(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else ZERO_QTY


def product_sales(session: Session, shop_id: int, start: date, end: date) -> list[ProductSales]:
    """Quantity and revenue per product for posted sales in the period, less live returns dated in the period.
    Products that sold nothing are not listed. Highest revenue first."""
    sold = session.execute(
        select(
            SaleItem.product_id,
            func.sum(SaleItem.quantity),
            func.sum(SaleItem.line_total - SaleItem.promotion_discount),
        )
        .join(Sale, (Sale.shop_id == SaleItem.shop_id) & (Sale.id == SaleItem.sale_id))
        .where(
            SaleItem.shop_id == shop_id,
            Sale.status == SaleStatus.POSTED,
            Sale.sale_date >= start,
            Sale.sale_date <= end,
        )
        .group_by(SaleItem.product_id)
    ).all()
    returned = {
        row[0]: (row[1], row[2])
        for row in session.execute(
            select(
                SalesReturnItem.product_id,
                func.sum(SalesReturnItem.quantity),
                func.sum(SalesReturnItem.refund_amount),
            )
            .join(
                SalesReturn,
                (SalesReturn.shop_id == SalesReturnItem.shop_id)
                & (SalesReturn.id == SalesReturnItem.sales_return_id),
            )
            .where(
                SalesReturnItem.shop_id == shop_id,
                SalesReturn.status == DocumentStatus.POSTED,
                SalesReturn.return_date >= start,
                SalesReturn.return_date <= end,
            )
            .group_by(SalesReturnItem.product_id)
        )
    }
    ids = {row[0] for row in sold} | set(returned)
    if not ids:
        return []
    info = {
        product.id: (product, category_name, unit_code)
        for product, category_name, unit_code in session.execute(
            select(Product, Category.name, Unit.code)
            .join(Category, (Category.shop_id == Product.shop_id) & (Category.id == Product.category_id))
            .join(Unit, Unit.id == Product.unit_id)
            .where(Product.shop_id == shop_id, Product.id.in_(ids))
        )
    }
    totals: dict[int, tuple[Decimal, Decimal]] = {}
    for product_id, quantity, revenue in sold:
        totals[product_id] = (_qty(quantity), _money(revenue))
    for product_id, (quantity, refund) in returned.items():
        q, r = totals.get(product_id, (ZERO_QTY, ZERO))
        totals[product_id] = (q - _qty(quantity), r - _money(refund))
    rows = [
        ProductSales(
            product_id=pid,
            name=info[pid][0].name,
            sku=info[pid][0].sku,
            category_id=info[pid][0].category_id,
            category_name=info[pid][1],
            unit_code=info[pid][2],
            quantity=quantity,
            revenue=revenue,
        )
        for pid, (quantity, revenue) in totals.items()
        if pid in info
    ]
    return sorted(rows, key=lambda row: (-row.revenue, row.name.casefold(), row.product_id))


def category_sales(rows: list[ProductSales]) -> list[CategorySales]:
    """Group product sales by category (a pure step over `product_sales`), highest revenue first."""
    grouped: dict[int, CategorySales] = {}
    for row in rows:
        current = grouped.get(row.category_id)
        grouped[row.category_id] = CategorySales(
            row.category_id,
            row.category_name,
            (current.quantity if current else ZERO_QTY) + row.quantity,
            (current.revenue if current else ZERO) + row.revenue,
        )
    return sorted(grouped.values(), key=lambda c: (-c.revenue, c.name.casefold()))


def sales_velocity(session: Session, shop_id: int, end: date, days: int) -> dict[int, Decimal]:
    """Units sold per product over the `days` ending on `end` (returns taken off)."""
    start = end - timedelta(days=days - 1)
    return {row.product_id: row.quantity for row in product_sales(session, shop_id, start, end)}


def purchase_totals(session: Session, shop_id: int, start: date, end: date) -> PurchaseTotals:
    """What was bought from suppliers (posted purchases) less what was sent back (posted purchase returns)."""
    by_supplier = session.execute(
        select(Supplier.id, Supplier.name, func.count(Purchase.id), func.sum(Purchase.total_amount))
        .join(Supplier, (Supplier.shop_id == Purchase.shop_id) & (Supplier.id == Purchase.supplier_id))
        .where(
            Purchase.shop_id == shop_id,
            Purchase.status == PurchaseStatus.POSTED,
            Purchase.purchase_date >= start,
            Purchase.purchase_date <= end,
        )
        .group_by(Supplier.id, Supplier.name)
    ).all()
    rows = sorted(
        (SupplierPurchases(sid, name, count, _money(total)) for sid, name, count, total in by_supplier),
        key=lambda r: (-r.total, r.name.casefold()),
    )
    returns = session.execute(
        select(func.count(PurchaseReturn.id), func.sum(PurchaseReturn.total_amount)).where(
            PurchaseReturn.shop_id == shop_id,
            PurchaseReturn.status == DocumentStatus.POSTED,
            PurchaseReturn.return_date >= start,
            PurchaseReturn.return_date <= end,
        )
    ).one()
    return PurchaseTotals(
        count=sum(r.purchases for r in rows),
        total=sum((r.total for r in rows), ZERO),
        returns_count=returns[0] or 0,
        returns_total=_money(returns[1]),
        by_supplier=rows,
    )
