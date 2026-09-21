"""Data integrity checks: a read-only audit of the database against the rules the application is built on.

It looks for things that should be impossible and reports them; it never changes, repairs or deletes anything. Where a
repair would be reasonable it says what a person could do, as a suggestion for them to weigh. Findings hold counts and a few
record ids (never names, amounts or contact details), so a report is safe to share with support.

Checks: sale totals against their lines, purchase totals against their lines, the offer discount recorded on each sale against
its offer snapshots, returns beyond what was sold, duplicate document numbers, duplicate SKUs and barcodes within a shop, stock
below zero where the shop forbids it, stock-ledger rows pointing at documents that do not exist, reversal links across
shops or products, customer-ledger credit sales that do not match their bills, and (on SQLite)
foreign-key violations.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    CustomerLedgerEntry,
    InventoryTransaction,
    Product,
    Purchase,
    PurchaseItem,
    QuickSale,
    Sale,
    SaleItem,
    SalePromotion,
    SalesReturn,
    SalesReturnItem,
    Shop,
)
from app.models.enums import (
    DocumentStatus,
    KhataReferenceType,
    PaymentType,
    SaleStatus,
    StockReferenceType,
)

SAMPLE = 5


@dataclass
class Finding:
    check: str
    severity: str  # ERROR (should be impossible) or WARNING (unusual)
    count: int
    description: str
    sample_ids: list[int] = field(default_factory=list)
    suggestion: str = "Report only. Review these records; nothing was changed."


def _scoped(shop_id: int | None, column: Any) -> list[Any]:
    return [] if shop_id is None else [column == shop_id]


def _found(
    check: str, severity: str, description: str, ids: list[int], suggestion: str | None = None
) -> Finding | None:
    if not ids:
        return None
    extra = {"suggestion": suggestion} if suggestion else {}
    return Finding(check, severity, len(ids), description, ids[:SAMPLE], **extra)


def run(session: Session, shop_id: int | None = None) -> list[Finding]:
    """Run every check (for one shop, or all). Returns only the problems found; an empty list means a clean bill of health."""
    findings: list[Finding | None] = []

    # 1. A sale's subtotal is the sum of its lines, and total = subtotal - discount - offer discount.
    line_sum = (
        select(SaleItem.sale_id, SaleItem.shop_id, func.sum(SaleItem.line_total).label("total"))
        .group_by(SaleItem.sale_id, SaleItem.shop_id)
        .subquery()
    )
    bad_subtotal = session.scalars(
        select(Sale.id)
        .join(line_sum, and_(line_sum.c.sale_id == Sale.id, line_sum.c.shop_id == Sale.shop_id))
        .where(Sale.subtotal != line_sum.c.total, *_scoped(shop_id, Sale.shop_id))
    ).all()
    findings.append(
        _found(
            "sale_subtotal_matches_lines",
            "ERROR",
            "A sale's subtotal differs from the sum of its lines.",
            list(bad_subtotal),
        )
    )
    bad_total = session.scalars(
        select(Sale.id).where(
            Sale.total_amount != Sale.subtotal - Sale.discount - Sale.promotion_discount,
            *_scoped(shop_id, Sale.shop_id),
        )
    ).all()
    findings.append(
        _found(
            "sale_total_arithmetic",
            "ERROR",
            "A sale's total is not subtotal less discounts.",
            list(bad_total),
        )
    )

    # 2. Offer snapshots add up to the discount on the sale.
    snap = (
        select(
            SalePromotion.sale_id,
            SalePromotion.shop_id,
            func.sum(SalePromotion.discount_amount).label("total"),
        )
        .group_by(SalePromotion.sale_id, SalePromotion.shop_id)
        .subquery()
    )
    bad_promo = session.scalars(
        select(Sale.id)
        .outerjoin(snap, and_(snap.c.sale_id == Sale.id, snap.c.shop_id == Sale.shop_id))
        .where(
            Sale.status == SaleStatus.POSTED,
            Sale.promotion_discount != func.coalesce(snap.c.total, 0),
            *_scoped(shop_id, Sale.shop_id),
        )
    ).all()
    findings.append(
        _found(
            "sale_offer_discount_matches_snapshots",
            "ERROR",
            "A posted sale's offer discount differs from its offer records.",
            list(bad_promo),
        )
    )

    # 3. A purchase's total is the sum of its lines.
    pline = (
        select(
            PurchaseItem.purchase_id, PurchaseItem.shop_id, func.sum(PurchaseItem.line_total).label("total")
        )
        .group_by(PurchaseItem.purchase_id, PurchaseItem.shop_id)
        .subquery()
    )
    bad_purchase = session.scalars(
        select(Purchase.id)
        .join(pline, and_(pline.c.purchase_id == Purchase.id, pline.c.shop_id == Purchase.shop_id))
        .where(Purchase.total_amount != pline.c.total, *_scoped(shop_id, Purchase.shop_id))
    ).all()
    findings.append(
        _found(
            "purchase_total_matches_lines",
            "ERROR",
            "A purchase's total differs from the sum of its lines.",
            list(bad_purchase),
        )
    )

    # 4. Nothing was returned beyond what was sold.
    returned = (
        select(
            SalesReturnItem.sale_item_id,
            SalesReturnItem.shop_id,
            func.sum(SalesReturnItem.quantity).label("quantity"),
        )
        .join(
            SalesReturn,
            and_(
                SalesReturn.id == SalesReturnItem.sales_return_id,
                SalesReturn.shop_id == SalesReturnItem.shop_id,
            ),
        )
        .where(SalesReturn.status == DocumentStatus.POSTED)
        .group_by(SalesReturnItem.sale_item_id, SalesReturnItem.shop_id)
        .subquery()
    )
    over_returned = session.scalars(
        select(SaleItem.id)
        .join(returned, and_(returned.c.sale_item_id == SaleItem.id, returned.c.shop_id == SaleItem.shop_id))
        .where(returned.c.quantity > SaleItem.quantity, *_scoped(shop_id, SaleItem.shop_id))
    ).all()
    findings.append(
        _found(
            "returns_within_quantity_sold",
            "ERROR",
            "More was returned on a sale line than was sold.",
            list(over_returned),
        )
    )

    # 5. Document numbers are unique within a shop.
    for label, model, column in (
        ("sale invoice numbers", Sale, Sale.invoice_no),
        ("purchase numbers", Purchase, Purchase.purchase_no),
        ("quick sale numbers", QuickSale, QuickSale.quick_no),
        ("sales return numbers", SalesReturn, SalesReturn.return_no),
    ):
        dup = (
            session.execute(
                select(func.min(model.id))
                .where(column.is_not(None), *_scoped(shop_id, model.shop_id))
                .group_by(model.shop_id, column)
                .having(func.count() > 1)
            )
            .scalars()
            .all()
        )
        findings.append(
            _found(
                f"duplicate_{label.replace(' ', '_')}",
                "ERROR",
                f"Duplicate {label} within a shop.",
                list(dup),
            )
        )

    # 6. SKU and barcode are unique within a shop.
    for label, column in (("sku", Product.sku), ("barcode", Product.barcode)):
        dup = (
            session.execute(
                select(func.min(Product.id))
                .where(column.is_not(None), *_scoped(shop_id, Product.shop_id))
                .group_by(Product.shop_id, column)
                .having(func.count() > 1)
            )
            .scalars()
            .all()
        )
        findings.append(
            _found(
                f"duplicate_product_{label}", "ERROR", f"Two products in one shop share a {label}.", list(dup)
            )
        )

    # 7. Stock below zero where the shop forbids it.
    stock = (
        select(
            InventoryTransaction.product_id,
            InventoryTransaction.shop_id,
            func.sum(InventoryTransaction.qty_delta).label("stock"),
        )
        .group_by(InventoryTransaction.product_id, InventoryTransaction.shop_id)
        .subquery()
    )
    negative = session.scalars(
        select(Product.id)
        .join(stock, and_(stock.c.product_id == Product.id, stock.c.shop_id == Product.shop_id))
        .join(Shop, Shop.id == Product.shop_id)
        .where(stock.c.stock < 0, Shop.allow_negative_stock.is_(False), *_scoped(shop_id, Product.shop_id))
    ).all()
    findings.append(
        _found(
            "negative_stock_where_forbidden",
            "ERROR",
            "A product's stock is below zero in a shop that does not allow that.",
            list(negative),
            "Review the product's stock history. A correcting adjustment or a reversal, made by a person, would fix it.",
        )
    )

    # 8. Stock-ledger rows point at documents that exist (in the same shop).
    for ref, model in (
        (StockReferenceType.SALE_ITEM, SaleItem),
        (StockReferenceType.PURCHASE_ITEM, PurchaseItem),
    ):
        orphan = session.scalars(
            select(InventoryTransaction.id).where(
                InventoryTransaction.reference_type == ref,
                ~exists().where(
                    model.id == InventoryTransaction.reference_id,
                    model.shop_id == InventoryTransaction.shop_id,
                ),
                *_scoped(shop_id, InventoryTransaction.shop_id),
            )
        ).all()
        findings.append(
            _found(
                f"stock_rows_reference_existing_{ref.value.lower()}",
                "ERROR",
                f"Stock ledger rows point at a {ref.value.lower().replace('_', ' ')} that does not exist.",
                list(orphan),
            )
        )

    # 9. Reversals reverse something real, of the same product and shop, by the opposite amount.
    original = InventoryTransaction.__table__.alias("orig")
    bad_reversal = session.scalars(
        select(InventoryTransaction.id)
        .join(original, original.c.id == InventoryTransaction.reverses_txn_id)
        .where(
            InventoryTransaction.reverses_txn_id.is_not(None),
            (original.c.shop_id != InventoryTransaction.shop_id)
            | (original.c.product_id != InventoryTransaction.product_id)
            | (original.c.qty_delta != -InventoryTransaction.qty_delta),
            *_scoped(shop_id, InventoryTransaction.shop_id),
        )
    ).all()
    findings.append(
        _found(
            "stock_reversals_are_exact",
            "ERROR",
            "A stock reversal does not exactly cancel the row it reverses.",
            list(bad_reversal),
        )
    )
    orig_entry = CustomerLedgerEntry.__table__.alias("orig_entry")
    bad_khata_reversal = session.scalars(
        select(CustomerLedgerEntry.id)
        .join(orig_entry, orig_entry.c.id == CustomerLedgerEntry.reverses_entry_id)
        .where(
            CustomerLedgerEntry.reverses_entry_id.is_not(None),
            (orig_entry.c.customer_id != CustomerLedgerEntry.customer_id)
            | (orig_entry.c.amount_delta != -CustomerLedgerEntry.amount_delta),
            *_scoped(shop_id, CustomerLedgerEntry.shop_id),
        )
    ).all()
    findings.append(
        _found(
            "khata_reversals_are_exact",
            "ERROR",
            "A khata reversal does not exactly cancel the entry it reverses.",
            list(bad_khata_reversal),
        )
    )

    # 10. A posted credit sale has a matching credit on the customer's ledger (net of any reversal).
    credited = (
        select(
            CustomerLedgerEntry.reference_id.label("sale_id"),
            CustomerLedgerEntry.shop_id,
            func.sum(CustomerLedgerEntry.amount_delta).label("net"),
        )
        .where(CustomerLedgerEntry.reference_type == KhataReferenceType.SALE)
        .group_by(CustomerLedgerEntry.reference_id, CustomerLedgerEntry.shop_id)
        .subquery()
    )
    mismatched = session.scalars(
        select(Sale.id)
        .outerjoin(credited, and_(credited.c.sale_id == Sale.id, credited.c.shop_id == Sale.shop_id))
        .where(
            Sale.status == SaleStatus.POSTED,
            Sale.payment_type == PaymentType.CREDIT,
            func.coalesce(credited.c.net, 0) != Sale.total_amount - func.coalesce(Sale.amount_paid, 0),
            *_scoped(shop_id, Sale.shop_id),
        )
    ).all()
    findings.append(
        _found(
            "credit_sales_match_khata",
            "ERROR",
            "A posted credit sale's khata entry differs from the unpaid part of the bill.",
            list(mismatched),
        )
    )

    # 11. Customers referenced by a sale or ledger exist in the same shop (SQLite foreign keys, where enforced, cover this).
    dangling = session.scalars(
        select(Sale.id).where(
            Sale.customer_id.is_not(None),
            ~exists().where(Customer.id == Sale.customer_id, Customer.shop_id == Sale.shop_id),
            *_scoped(shop_id, Sale.shop_id),
        )
    ).all()
    findings.append(
        _found(
            "sales_reference_customers_of_the_same_shop",
            "ERROR",
            "A sale points at a customer that is not in its shop.",
            list(dangling),
        )
    )

    # 12. Foreign-key violations anywhere (SQLite: PRAGMA foreign_key_check).
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        violations = session.connection().exec_driver_sql("PRAGMA foreign_key_check").all()
        if violations:
            findings.append(
                Finding(
                    "foreign_keys",
                    "ERROR",
                    len(violations),
                    "Rows point at records that do not exist.",
                    [v[1] for v in violations[:SAMPLE] if isinstance(v[1], int)],
                )
            )
    return [f for f in findings if f is not None]


def summary(findings: list[Finding]) -> dict[str, Any]:
    return {
        "ok": not any(f.severity == "ERROR" for f in findings),
        "errors": sum(1 for f in findings if f.severity == "ERROR"),
        "warnings": sum(1 for f in findings if f.severity == "WARNING"),
        "findings": [f.__dict__ for f in findings],
    }
