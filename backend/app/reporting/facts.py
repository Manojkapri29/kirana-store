"""Read-only fact queries shared by the BI reports. Everything here READS the documents and ledgers that already
exist (posted sales and quick sales, the inventory transaction ledger, purchases); nothing here writes, and nothing
here is a second source of truth. Only POSTED documents are ever read: drafts and voided documents never appear.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    InventoryTransaction,
    Product,
    QuickSale,
    Sale,
    Unit,
)
from app.models.enums import SaleStatus

ZERO = Decimal("0.00")


def identified_purchases(
    session: Session, shop_id: int, start: date, end: date
) -> list[tuple[int, date, Decimal, str]]:
    """(customer_id, date, amount, kind) for posted sales and quick sales that name a customer."""
    out: list[tuple[int, date, Decimal, str]] = []
    for model, kind in ((Sale, "DETAILED"), (QuickSale, "QUICK")):
        rows = session.execute(
            select(model.customer_id, model.sale_date, model.total_amount).where(
                model.shop_id == shop_id,
                model.status == SaleStatus.POSTED,
                model.customer_id.is_not(None),
                model.sale_date >= start,
                model.sale_date <= end,
            )
        ).all()
        out.extend((cid, day, Decimal(total), kind) for cid, day, total in rows)
    return out


def first_purchase_dates(session: Session, shop_id: int) -> dict[int, date]:
    """Each identified customer's first posted purchase date (detailed or quick), ever."""
    first: dict[int, date] = {}
    for model in (Sale, QuickSale):
        for cid, day in session.execute(
            select(model.customer_id, func.min(model.sale_date))
            .where(
                model.shop_id == shop_id, model.status == SaleStatus.POSTED, model.customer_id.is_not(None)
            )
            .group_by(model.customer_id)
        ):
            if cid not in first or day < first[cid]:
                first[cid] = day
    return first


def stock_units_at(session: Session, shop_id: int, day: date) -> Decimal:
    """Total units on hand at the END of `day`, counting only products sold by whole units (pieces, packs...).
    Quantities in kg, litres etc. are not summed with pieces: that total would mean nothing. Read from the inventory
    transaction ledger, the only source of stock."""
    total = session.scalar(
        select(func.coalesce(func.sum(InventoryTransaction.qty_delta), 0))
        .join(
            Product,
            (Product.shop_id == InventoryTransaction.shop_id)
            & (Product.id == InventoryTransaction.product_id),
        )
        .join(Unit, Unit.id == Product.unit_id)
        .where(
            InventoryTransaction.shop_id == shop_id,
            InventoryTransaction.txn_date <= day,
            Unit.allows_decimal.is_(False),
        )
    )
    return Decimal(total or 0)
