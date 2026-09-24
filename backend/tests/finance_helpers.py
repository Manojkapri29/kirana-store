"""Builders for finance tests. They insert POSTED source documents directly (the finance layer only READS
them), satisfying every table constraint, so a test states exactly the money it expects."""

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count

from app.models import (
    Purchase,
    PurchaseReturn,
    QuickSale,
    Sale,
    SaleItem,
    SalesReturn,
    SalesReturnItem,
)
from app.models.enums import (
    DocumentStatus,
    PaymentMethod,
    PaymentType,
    PurchaseStatus,
    RefundMode,
    SaleStatus,
    SupplierCreditMode,
)
from tests import factories

_n = count(1)
D = Decimal


def _stamp() -> datetime:
    return datetime.now(UTC)


def make_sale(
    session,
    tenant,
    total,
    *,
    day: date,
    paid=None,
    method=PaymentMethod.CASH,
    customer_id=None,
    cogs=None,
    unit_cost="__auto__",
):  # noqa: ANN001
    """A POSTED detailed sale of one line. `paid=None` = paid in full; `cogs=None` = the cost is UNKNOWN
    (never zero); pass a Decimal for a known cost."""
    total = D(total)
    n = next(_n)
    product = factories.make_product(session, tenant.shop, tenant.category, name=f"P{n}", sku=f"SKU-F{n}")
    paid_amount = total if paid is None else D(paid)
    sale = Sale(
        shop_id=tenant.shop.id, invoice_no=f"INV/T/{n:04d}", status=SaleStatus.POSTED, sale_date=day,
        customer_id=customer_id, subtotal=total, total_amount=total,
        payment_type=PaymentType.PAID if paid_amount == total else PaymentType.CREDIT, amount_paid=paid_amount,
        payment_method=method if paid_amount > 0 else None, posted_at=_stamp(), posted_by=tenant.user.id,
        created_by=tenant.user.id,
    )  # fmt: skip
    session.add(sale)
    session.flush()
    session.add(
        SaleItem(
            shop_id=tenant.shop.id,
            sale_id=sale.id,
            product_id=product.id,
            unit_id=product.unit_id,
            quantity=D(1),
            unit_price=total,
            line_total=total,
            unit_cost=None if cogs is None else D(cogs),
            cogs_amount=None if cogs is None else D(cogs),
        )
    )
    session.flush()
    return sale


def make_quick_sale(
    session, tenant, total, *, day: date, paid=None, method=PaymentMethod.CASH, customer_id=None
):  # noqa: ANN001
    total = D(total)
    n = next(_n)
    paid_amount = total if paid is None else D(paid)
    sale = QuickSale(
        shop_id=tenant.shop.id, quick_no=f"QS/T/{n:04d}", status=SaleStatus.POSTED, sale_date=day,
        gross_amount=total, discount=D(0), total_amount=total, customer_id=customer_id,
        payment_type=PaymentType.PAID if paid_amount == total else PaymentType.CREDIT, amount_paid=paid_amount,
        payment_method=method if paid_amount > 0 else None, posted_at=_stamp(), posted_by=tenant.user.id,
        created_by=tenant.user.id,
    )  # fmt: skip
    session.add(sale)
    session.flush()
    return sale


def make_purchase(session, tenant, supplier, total, *, day: date, paid=0, method=None):  # noqa: ANN001
    n = next(_n)
    paid = D(paid)
    purchase = Purchase(
        shop_id=tenant.shop.id, supplier_id=supplier.id, purchase_no=f"PUR/T/{n:04d}", purchase_date=day,
        total_amount=D(total), amount_paid=paid, payment_method=method if paid > 0 else None,
        status=PurchaseStatus.POSTED, posted_at=_stamp(), posted_by=tenant.user.id, created_by=tenant.user.id,
    )  # fmt: skip
    session.add(purchase)
    session.flush()
    return purchase


def make_purchase_return(
    session, tenant, purchase, total, *, day: date, mode=SupplierCreditMode.SUPPLIER_CREDIT
):  # noqa: ANN001
    n = next(_n)
    ret = PurchaseReturn(
        shop_id=tenant.shop.id, return_no=f"PRT/T/{n:04d}", purchase_id=purchase.id, return_date=day,
        credit_mode=mode, total_amount=D(total), status=DocumentStatus.POSTED, created_by=tenant.user.id,
    )  # fmt: skip
    session.add(ret)
    session.flush()
    return ret


def make_sales_return(session, tenant, sale, refund, *, day: date, mode=RefundMode.CASH, cogs=None):  # noqa: ANN001
    n = next(_n)
    ret = SalesReturn(
        shop_id=tenant.shop.id, return_no=f"SRT/T/{n:04d}", sale_id=sale.id, return_date=day, refund_mode=mode,
        total_refund=D(refund), status=DocumentStatus.POSTED, created_by=tenant.user.id,
    )  # fmt: skip
    session.add(ret)
    session.flush()
    item = session.query(SaleItem).filter_by(sale_id=sale.id).first()
    session.add(
        SalesReturnItem(
            shop_id=tenant.shop.id,
            sales_return_id=ret.id,
            sale_item_id=item.id,
            product_id=item.product_id,
            quantity=D(1),
            refund_amount=D(refund),
            unit_cost=None if cogs is None else D(cogs),
            cogs_amount=None if cogs is None else D(cogs),
        )
    )
    session.flush()
    return ret
