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
from typing import Any

from sqlalchemy import func, select, type_coerce
from sqlalchemy.orm import Session

from app.db.types import Money
from app.models import (
    Category,
    Product,
    Purchase,
    PurchaseItem,
    PurchaseReturn,
    QuickSale,
    Sale,
    SaleItem,
    SalesReturn,
    SalesReturnItem,
    Supplier,
    Unit,
)
from app.models.enums import DocumentStatus, PaymentType, PurchaseStatus, SaleStatus

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


# NOTE: a SUM of a difference has no type of its own and comes back as raw paise; type_coerce restores Money.
def product_sales(session: Session, shop_id: int, start: date, end: date) -> list[ProductSales]:
    """Quantity and revenue per product for posted sales in the period, less live returns dated in the period.
    Products that sold nothing are not listed. Highest revenue first."""
    sold = session.execute(
        select(
            SaleItem.product_id,
            func.sum(SaleItem.quantity),
            type_coerce(func.sum(SaleItem.line_total - SaleItem.promotion_discount), Money),
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


def credit_sales(session: Session, shop_id: int, start: date, end: date) -> tuple[int, Decimal]:
    """Bills in the period sold on credit: how many, and the unpaid part in total (Detailed and Quick)."""
    unpaid = Sale.total_amount - func.coalesce(Sale.amount_paid, 0)
    count, total = session.execute(
        select(func.count(), func.coalesce(func.sum(unpaid), 0)).where(
            Sale.shop_id == shop_id,
            Sale.status == SaleStatus.POSTED,
            Sale.payment_type == PaymentType.CREDIT,
            Sale.sale_date >= start,
            Sale.sale_date <= end,
        )
    ).one()
    q_unpaid = QuickSale.total_amount - func.coalesce(QuickSale.amount_paid, 0)
    q_count, q_total = session.execute(
        select(func.count(), func.coalesce(func.sum(q_unpaid), 0)).where(
            QuickSale.shop_id == shop_id,
            QuickSale.status == SaleStatus.POSTED,
            QuickSale.payment_type == PaymentType.CREDIT,
            QuickSale.sale_date >= start,
            QuickSale.sale_date <= end,
        )
    ).one()
    return count + q_count, _money(total) + _money(q_total)


def customer_activity(session: Session, shop_id: int, start: date, end: date) -> tuple[int, int]:
    """(customers who bought in the period, how many of them bought before it). Named customers only."""
    in_period = (
        select(Sale.customer_id)
        .where(
            Sale.shop_id == shop_id,
            Sale.status == SaleStatus.POSTED,
            Sale.customer_id.is_not(None),
            Sale.sale_date >= start,
            Sale.sale_date <= end,
        )
        .distinct()
    )
    ids = [row for row in session.scalars(in_period)]
    if not ids:
        return 0, 0
    returning = session.scalar(
        select(func.count(func.distinct(Sale.customer_id))).where(
            Sale.shop_id == shop_id,
            Sale.status == SaleStatus.POSTED,
            Sale.customer_id.in_(ids),
            Sale.sale_date < start,
        )
    )
    return len(ids), returning or 0


def purchase_trend(session: Session, shop_id: int, end: date, months: int = 6) -> list[tuple[str, Decimal]]:
    """Posted purchase value per calendar month for the last `months` months, oldest first."""
    first = end.replace(day=1)
    for _ in range(months - 1):
        first = (first - timedelta(days=1)).replace(day=1)
    rows = session.execute(
        select(Purchase.purchase_date, Purchase.total_amount).where(
            Purchase.shop_id == shop_id,
            Purchase.status == PurchaseStatus.POSTED,
            Purchase.purchase_date >= first,
            Purchase.purchase_date <= end,
        )
    ).all()
    totals: dict[str, Decimal] = {}
    cursor = first
    for _ in range(months):
        totals[f"{cursor:%Y-%m}"] = ZERO
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    for day, amount in rows:
        totals[f"{day:%Y-%m}"] = totals.get(f"{day:%Y-%m}", ZERO) + _money(amount)
    return list(totals.items())


@dataclass(frozen=True)
class CostMove:
    product_id: int
    name: str
    first_cost: Decimal
    last_cost: Decimal
    change_percent: Decimal


def cost_movement(session: Session, shop_id: int, start: date, end: date, limit: int = 5) -> list[CostMove]:
    """Products whose purchase price changed in the period (first to last posted purchase), biggest first."""
    rows = session.execute(
        select(
            PurchaseItem.product_id, Product.name, PurchaseItem.unit_cost, Purchase.purchase_date, Purchase.id
        )
        .join(
            Purchase, (Purchase.shop_id == PurchaseItem.shop_id) & (Purchase.id == PurchaseItem.purchase_id)
        )
        .join(Product, (Product.shop_id == PurchaseItem.shop_id) & (Product.id == PurchaseItem.product_id))
        .where(
            Purchase.shop_id == shop_id,
            Purchase.status == PurchaseStatus.POSTED,
            Purchase.purchase_date >= start,
            Purchase.purchase_date <= end,
        )
        .order_by(Purchase.purchase_date, Purchase.id)
    ).all()
    seen: dict[int, list[Any]] = {}
    for product_id, name, cost, _day, _pid in rows:
        seen.setdefault(product_id, [name, cost, cost])[2] = cost
    moves = []
    for product_id, (name, first, last) in seen.items():
        if first and first != last:
            moves.append(
                CostMove(
                    product_id, name, first, last, ((last - first) * 100 / first).quantize(Decimal("0.1"))
                )
            )
    return sorted(moves, key=lambda m: -abs(m.change_percent))[:limit]
