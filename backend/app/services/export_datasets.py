"""What each export contains: columns and rows for products, inventory, history, purchases, sales, customers.

Rows come from the domain services (never from tables directly), so the same shop scoping and the same
stock calculation apply to exports as to the screens. The file format is handled by `export_service`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models.enums import (
    CustomerLedgerEntryType,
    InventoryTxnType,
    PaymentType,
    PromotionStatus,
    PromotionType,
    PurchaseStatus,
    SaleStatus,
)
from app.services import (
    customer_service,
    inventory_service,
    khata_service,
    price_comparison_service,
    product_service,
    promotion_calculation,
    promotion_service,
    purchase_return_service,
    purchase_service,
    quick_sale_service,
    sale_service,
    sales_report_service,
    sales_return_service,
    supplier_service,
)
from app.services.export_service import Column, ExportFile, ExportFormat, Kind, render
from app.services.inventory_service import StockStatus
from app.services.khata_service import BalanceStatus
from app.services.shop_service import get_shop, shop_today

PRODUCT_COLUMNS = [
    Column("sku", "SKU"),
    Column("name", "Product"),
    Column("brand", "Brand"),
    Column("barcode", "Barcode"),
    Column("category", "Category"),
    Column("unit", "Unit"),
    Column("supplier", "Default Supplier"),
    Column("mrp", "MRP", Kind.MONEY),
    Column("selling_price", "Selling Price", Kind.MONEY),
    Column("purchase_price", "Purchase Price", Kind.MONEY),
    Column("avg_cost", "Average Cost", Kind.MONEY),
    Column("reorder_level", "Reorder Level", Kind.QUANTITY),
    Column("current_stock", "Current Stock", Kind.QUANTITY),
    Column("stock_status", "Stock Status"),
    Column("status", "Status"),
    Column("created_at", "Created", Kind.DATETIME),
]

INVENTORY_COLUMNS = [
    Column("sku", "SKU"),
    Column("name", "Product"),
    Column("brand", "Brand"),
    Column("barcode", "Barcode"),
    Column("category", "Category"),
    Column("unit", "Unit"),
    Column("current_stock", "Current Stock", Kind.QUANTITY),
    Column("reorder_level", "Reorder Level", Kind.QUANTITY),
    Column("stock_status", "Stock Status"),
    Column("avg_cost", "Average Cost", Kind.MONEY),
    Column("status", "Status"),
]

TRANSACTION_COLUMNS = [
    Column("txn_date", "Date", Kind.DATE),
    Column("sku", "SKU"),
    Column("product", "Product"),
    Column("txn_type", "Type"),
    Column("qty_delta", "Quantity Change", Kind.QUANTITY),
    Column("balance_after", "Balance After", Kind.QUANTITY),
    Column("unit_cost", "Unit Cost", Kind.MONEY),
    Column("reason_code", "Reason"),
    Column("note", "Note"),
    Column("reference_type", "Source"),
    Column("reference_id", "Source ID", Kind.INTEGER),
    Column("purchase_no", "Purchase No"),
    Column("sale_no", "Invoice No"),
    Column("created_by", "Recorded By"),
    Column("created_at", "Recorded At", Kind.DATETIME),
]

PURCHASE_COLUMNS = [
    Column("purchase_no", "Purchase No"),
    Column("purchase_date", "Date", Kind.DATE),
    Column("supplier", "Supplier"),
    Column("invoice_no", "Supplier Invoice No"),
    Column("status", "Status"),
    Column("item_count", "Items", Kind.INTEGER),
    Column("total", "Total", Kind.MONEY),
    Column("notes", "Notes"),
    Column("created_by", "Created By"),
    Column("posted_at", "Posted At", Kind.DATETIME),
    Column("void_reason", "Void Reason"),
]

PURCHASE_ITEM_COLUMNS = [
    Column("purchase_no", "Purchase No"),
    Column("purchase_date", "Date", Kind.DATE),
    Column("supplier", "Supplier"),
    Column("invoice_no", "Supplier Invoice No"),
    Column("status", "Status"),
    Column("sku", "SKU"),
    Column("product", "Product"),
    Column("unit", "Unit"),
    Column("quantity", "Quantity", Kind.QUANTITY),
    Column("unit_cost", "Price", Kind.MONEY),
    Column("discount", "Discount", Kind.MONEY),
    Column("line_total", "Line Total", Kind.MONEY),
    Column("stock_before", "Stock Before", Kind.QUANTITY),
    Column("avg_cost_before", "Average Cost Before", Kind.MONEY),
    Column("avg_cost_after", "Average Cost After", Kind.MONEY),
    Column("purchase_total", "Purchase Total", Kind.MONEY),
]

CUSTOMER_COLUMNS = [
    Column("name", "Customer"),
    Column("phone", "Phone"),
    Column("email", "Email"),
    Column("address", "Address"),
    Column("balance", "Balance", Kind.MONEY),
    Column("outstanding", "Outstanding (Owes)", Kind.MONEY),
    Column("advance", "Advance (Paid Ahead)", Kind.MONEY),
    Column("balance_status", "Balance Status"),
    Column("entries", "Ledger Entries", Kind.INTEGER),
    Column("status", "Status"),
    Column("notes", "Notes"),
    Column("created_at", "Created", Kind.DATETIME),
]

SUPPLIER_COLUMNS = [
    Column("name", "Supplier"),
    Column("phone", "Phone"),
    Column("alternate_phone", "Alternate Phone"),
    Column("email", "Email"),
    Column("address", "Address"),
    Column("gstin", "GSTIN"),
    Column("products", "Products Supplied", Kind.INTEGER),
    Column("status", "Status"),
    Column("notes", "Notes"),
    Column("created_at", "Created", Kind.DATETIME),
]

CUSTOMER_LEDGER_COLUMNS = [
    Column("entry_date", "Date", Kind.DATE),
    Column("customer", "Customer"),
    Column("phone", "Phone"),
    Column("entry_type", "Type"),
    Column("debit", "Debit (Owed)", Kind.MONEY),
    Column("credit", "Credit (Received)", Kind.MONEY),
    Column("balance_after", "Balance After", Kind.MONEY),
    Column("payment_method", "Payment Method"),
    Column("payment_reference", "Payment Reference"),
    Column("note", "Note"),
    Column("reference_type", "Source"),
    Column("reference_id", "Source ID", Kind.INTEGER),
    Column("reference_no", "Source Invoice No"),
    Column("reverses", "Reverses Entry", Kind.INTEGER),
    Column("reversed_by", "Reversed By Entry", Kind.INTEGER),
    Column("created_by", "Recorded By"),
    Column("created_at", "Recorded At", Kind.DATETIME),
]

SALE_COLUMNS = [
    Column("invoice_no", "Invoice No"),
    Column("sale_date", "Date", Kind.DATE),
    Column("customer", "Customer"),
    Column("status", "Status"),
    Column("payment_type", "Payment"),
    Column("payment_method", "Payment Method"),
    Column("payment_reference", "Payment Reference"),
    Column("item_count", "Items", Kind.INTEGER),
    Column("subtotal", "Subtotal", Kind.MONEY),
    Column("discount", "Bill Discount", Kind.MONEY),
    Column("total", "Total", Kind.MONEY),
    Column("amount_paid", "Amount Paid", Kind.MONEY),
    Column("on_khata", "On Khata (Credit)", Kind.MONEY),
    Column("cogs", "Cost of Goods", Kind.MONEY),
    Column("profit", "Gross Profit", Kind.MONEY),
    Column("cost_known", "Cost Known"),
    Column("notes", "Notes"),
    Column("created_by", "Created By"),
    Column("posted_at", "Posted At", Kind.DATETIME),
    Column("void_reason", "Void Reason"),
]

SALE_ITEM_COLUMNS = [
    Column("invoice_no", "Invoice No"),
    Column("sale_date", "Date", Kind.DATE),
    Column("customer", "Customer"),
    Column("status", "Status"),
    Column("sku", "SKU"),
    Column("product", "Product"),
    Column("unit", "Unit"),
    Column("quantity", "Quantity", Kind.QUANTITY),
    Column("unit_price", "Price", Kind.MONEY),
    Column("mrp", "MRP", Kind.MONEY),
    Column("discount", "Discount", Kind.MONEY),
    Column("line_total", "Line Total", Kind.MONEY),
    Column("unit_cost", "Unit Cost", Kind.MONEY),
    Column("cogs", "Cost of Goods", Kind.MONEY),
    Column("profit", "Line Profit", Kind.MONEY),
    Column("sale_total", "Sale Total", Kind.MONEY),
]

QUICK_SALE_COLUMNS = [
    Column("quick_no", "Quick Sale No"),
    Column("sale_date", "Date", Kind.DATE),
    Column("customer", "Customer"),
    Column("status", "Status"),
    Column("payment_type", "Payment"),
    Column("payment_method", "Payment Method"),
    Column("payment_reference", "Payment Reference"),
    Column("gross", "Amount", Kind.MONEY),
    Column("discount", "Discount", Kind.MONEY),
    Column("total", "Total", Kind.MONEY),
    Column("amount_paid", "Amount Paid", Kind.MONEY),
    Column("on_khata", "On Khata (Credit)", Kind.MONEY),
    Column("profit", "Profit"),
    Column("note", "Note"),
    Column("created_by", "Created By"),
    Column("posted_at", "Posted At", Kind.DATETIME),
    Column("void_reason", "Void Reason"),
]

PROMOTION_COLUMNS = [
    Column("name", "Offer"),
    Column("terms", "What It Gives"),
    Column("promo_type", "Type"),
    Column("scope", "Applies To"),
    Column("status", "Status"),
    Column("coupon_code", "Coupon Code"),
    Column("audience", "For"),
    Column("priority", "Priority", Kind.INTEGER),
    Column("stackable", "Can Combine"),
    Column("starts_at", "Starts", Kind.DATETIME),
    Column("ends_at", "Ends", Kind.DATETIME),
    Column("min_cart_value", "Minimum Bill", Kind.MONEY),
    Column("min_quantity", "Minimum Quantity", Kind.QUANTITY),
    Column("max_discount", "Maximum Discount", Kind.MONEY),
    Column("usage_limit", "Usage Limit", Kind.INTEGER),
    Column("per_customer_limit", "Limit Per Customer", Kind.INTEGER),
    Column("used_count", "Times Used", Kind.INTEGER),
    Column("discount_given", "Discount Given", Kind.MONEY),
    Column("created_by", "Created By"),
    Column("description", "Description"),
]

PROMOTION_USAGE_COLUMNS = [
    Column("sale_date", "Date", Kind.DATE),
    Column("invoice_no", "Invoice No"),
    Column("sale_status", "Sale Status"),
    Column("customer", "Customer"),
    Column("name", "Offer"),
    Column("terms", "What It Gave"),
    Column("coupon_code", "Coupon Code"),
    Column("discount_amount", "Discount", Kind.MONEY),
    Column("basis", "Why It Applied"),
]

PRICE_HISTORY_COLUMNS = [
    Column("checked_at", "Checked At", Kind.DATETIME),
    Column("barcode", "Barcode"),
    Column("provider", "Source"),
    Column("product_name", "Product Name (Source)"),
    Column("brand", "Brand (Source)"),
    Column("price", "Price", Kind.MONEY),
    Column("currency", "Currency"),
    Column("location", "Location"),
    Column("observed_on", "Price Seen On", Kind.DATE),
    Column("source_url", "Source Link"),
]

SALES_SUMMARY_COLUMNS = [
    Column("day", "Date", Kind.DATE),
    Column("detailed_sales", "Detailed Sales", Kind.INTEGER),
    Column("detailed_gross", "Detailed Gross", Kind.MONEY),
    Column("detailed_discount", "Detailed Discount", Kind.MONEY),
    Column("detailed_net", "Detailed Net", Kind.MONEY),
    Column("quick_sales", "Quick Sales", Kind.INTEGER),
    Column("quick_gross", "Quick Gross", Kind.MONEY),
    Column("quick_discount", "Quick Discount", Kind.MONEY),
    Column("quick_net", "Quick Net", Kind.MONEY),
    Column("gross", "Combined Gross", Kind.MONEY),
    Column("discount", "Combined Discount", Kind.MONEY),
    Column("net", "Combined Net", Kind.MONEY),
]

DISCOUNT_REPORT_COLUMNS = [
    Column("promotion", "Offer"),
    Column("coupon_code", "Coupon Code"),
    Column("uses", "Times Used", Kind.INTEGER),
    Column("discount", "Discount Given", Kind.MONEY),
]

SALES_RETURN_COLUMNS = [
    Column("return_no", "Return No"),
    Column("return_date", "Date", Kind.DATE),
    Column("invoice_no", "Invoice No"),
    Column("customer", "Customer"),
    Column("status", "Status"),
    Column("refund_mode", "Refund By"),
    Column("items", "Items", Kind.INTEGER),
    Column("total_refund", "Refund", Kind.MONEY),
    Column("cogs", "Cost Of Goods Returned", Kind.MONEY),
    Column("reason", "Reason"),
    Column("created_by", "Created By"),
    Column("void_reason", "Void Reason"),
]

PURCHASE_RETURN_COLUMNS = [
    Column("return_no", "Return No"),
    Column("return_date", "Date", Kind.DATE),
    Column("purchase_no", "Purchase No"),
    Column("supplier", "Supplier"),
    Column("status", "Status"),
    Column("credit_mode", "Credit By"),
    Column("items", "Items", Kind.INTEGER),
    Column("total_amount", "Credit", Kind.MONEY),
    Column("reason", "Reason"),
    Column("created_by", "Created By"),
    Column("void_reason", "Void Reason"),
]

BALANCE_LABELS = {
    BalanceStatus.OUTSTANDING: "Owes",
    BalanceStatus.SETTLED: "Settled",
    BalanceStatus.ADVANCE: "Advance",
}

STATUS_LABELS = {
    StockStatus.IN_STOCK: "In Stock",
    StockStatus.LOW_STOCK: "Low Stock",
    StockStatus.OUT_OF_STOCK: "Out of Stock",
}


def _local(moment: Any, timezone: str) -> Any:
    """Stored UTC time -> the shop's local wall-clock time (spreadsheets have no timezones)."""
    return moment.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)


@dataclass(frozen=True)
class _Dataset:
    name: str
    columns: list[Column]
    rows: list[dict[str, Any]]


def _products(session: Session, ctx: RequestContext, *, active: bool | None, **filters: Any) -> _Dataset:
    shop = get_shop(session, ctx.shop_id)
    views, _ = product_service.list_products(session, ctx.shop_id, active=active, limit=None, **filters)
    rows = [
        {
            "sku": v.product.sku,
            "name": v.product.name,
            "brand": v.product.brand,
            "barcode": v.product.barcode,
            "category": v.category_name,
            "unit": v.unit.code,
            "supplier": v.supplier_name,
            "mrp": v.product.mrp,
            "selling_price": v.product.selling_price,
            "purchase_price": v.product.purchase_price,
            "avg_cost": v.product.avg_cost,
            "reorder_level": v.product.reorder_level,
            "current_stock": v.current_stock,
            "stock_status": STATUS_LABELS[v.stock_status],
            "status": "Active" if v.product.is_active else "Inactive",
            "created_at": _local(v.product.created_at, shop.timezone),
        }
        for v in views
    ]
    return _Dataset("products", PRODUCT_COLUMNS, rows)


def _inventory(session: Session, ctx: RequestContext, **filters: Any) -> _Dataset:
    items, _ = inventory_service.list_inventory(session, ctx.shop_id, limit=None, **filters)
    rows = [
        {
            "sku": i.sku,
            "name": i.name,
            "brand": i.brand,
            "barcode": i.barcode,
            "category": i.category_name,
            "unit": i.unit_code,
            "current_stock": i.current_stock,
            "reorder_level": i.reorder_level,
            "stock_status": STATUS_LABELS[i.status],
            "avg_cost": i.avg_cost,
            "status": "Active" if i.is_active else "Inactive",
        }
        for i in items
    ]
    return _Dataset("inventory", INVENTORY_COLUMNS, rows)


def _transactions(session: Session, ctx: RequestContext, **filters: Any) -> _Dataset:
    shop = get_shop(session, ctx.shop_id)
    items, _ = inventory_service.list_transactions(
        session, ctx.shop_id, newest_first=False, limit=None, **filters
    )
    rows = [
        {
            "txn_date": t.txn_date,
            "sku": t.sku,
            "product": t.product_name,
            "txn_type": t.txn_type.value,
            "qty_delta": t.qty_delta,
            "balance_after": t.balance_after,
            "unit_cost": t.unit_cost,
            "reason_code": t.reason_code.value if t.reason_code else None,
            "note": t.note,
            "reference_type": t.reference_type.value if t.reference_type else None,
            "reference_id": t.reference_id,
            "purchase_no": t.purchase_no,
            "sale_no": t.sale_no,
            "created_by": t.created_by_name,
            "created_at": _local(t.created_at, shop.timezone),
        }
        for t in items
    ]
    return _Dataset("inventory_history", TRANSACTION_COLUMNS, rows)


def _purchases(session: Session, ctx: RequestContext, **filters: Any) -> _Dataset:
    shop = get_shop(session, ctx.shop_id)
    items, _ = purchase_service.list_purchases(session, ctx.shop_id, limit=None, **filters)
    rows = [
        {
            "purchase_no": r.purchase.purchase_no,
            "purchase_date": r.purchase.purchase_date,
            "supplier": r.supplier_name,
            "invoice_no": r.purchase.supplier_invoice_no,
            "status": r.purchase.status.value.title(),
            "item_count": r.item_count,
            "total": r.purchase.total_amount,
            "notes": r.purchase.notes,
            "created_by": r.created_by_name,
            "posted_at": None
            if r.purchase.posted_at is None
            else _local(r.purchase.posted_at, shop.timezone),
            "void_reason": r.purchase.void_reason,
        }
        for r in items
    ]
    return _Dataset("purchases", PURCHASE_COLUMNS, rows)


def _purchase_items(
    session: Session, ctx: RequestContext, *, name: str = "purchase_items", **filters: Any
) -> _Dataset:
    lines = purchase_service.list_item_rows(session, ctx.shop_id, **filters)
    rows = [
        {
            "purchase_no": r.purchase.purchase_no,
            "purchase_date": r.purchase.purchase_date,
            "supplier": r.supplier_name,
            "invoice_no": r.purchase.supplier_invoice_no,
            "status": r.purchase.status.value.title(),
            "sku": r.product_sku,
            "product": r.product_name,
            "unit": r.unit_code,
            "quantity": r.item.quantity,
            "unit_cost": r.item.unit_cost,
            "discount": r.item.discount,
            "line_total": r.item.line_total,
            "stock_before": r.item.stock_before,
            "avg_cost_before": r.item.avg_cost_before,
            "avg_cost_after": r.item.avg_cost_after,
            "purchase_total": r.purchase.total_amount,
        }
        for r in lines
    ]
    return _Dataset(name, PURCHASE_ITEM_COLUMNS, rows)


def _sales(session: Session, ctx: RequestContext, **filters: Any) -> _Dataset:
    shop = get_shop(session, ctx.shop_id)
    items, _ = sale_service.list_sales(session, ctx.shop_id, limit=None, **filters)
    rows = []
    for r in items:
        s = r.sale
        posted = s.invoice_no is not None
        rows.append(
            {
                "invoice_no": s.invoice_no,
                "sale_date": s.sale_date,
                "customer": r.customer_name,
                "status": s.status.value.title(),
                "payment_type": None if s.payment_type is None else s.payment_type.value.title(),
                "payment_method": None if s.payment_method is None else s.payment_method.value,
                "payment_reference": s.payment_reference,
                "item_count": r.item_count,
                "subtotal": s.subtotal,
                "discount": s.discount,
                "total": s.total_amount,
                "amount_paid": s.amount_paid,
                "on_khata": None
                if s.amount_paid is None
                else max(s.total_amount - s.amount_paid, Decimal("0.00")),
                "cogs": r.cogs_total,  # empty when unknown, never 0
                "profit": r.gross_profit,
                "cost_known": (None if not posted else ("Yes" if r.unknown_cost_lines == 0 else "No")),
                "notes": s.notes,
                "created_by": r.created_by_name,
                "posted_at": None if s.posted_at is None else _local(s.posted_at, shop.timezone),
                "void_reason": s.void_reason,
            }
        )
    return _Dataset("sales", SALE_COLUMNS, rows)


def _sale_items(
    session: Session, ctx: RequestContext, *, name: str = "sale_items", **filters: Any
) -> _Dataset:
    lines = sale_service.list_item_rows(session, ctx.shop_id, **filters)
    rows = [
        {
            "invoice_no": r.sale.invoice_no,
            "sale_date": r.sale.sale_date,
            "customer": r.customer_name,
            "status": r.sale.status.value.title(),
            "sku": r.product_sku,
            "product": r.product_name,
            "unit": r.unit_code,
            "quantity": r.item.quantity,
            "unit_price": r.item.unit_price,
            "mrp": r.item.mrp,
            "discount": r.item.discount,
            "line_total": r.item.line_total,
            "unit_cost": r.item.unit_cost,  # empty when unknown, never 0
            "cogs": r.item.cogs_amount,
            "profit": None if r.item.cogs_amount is None else r.item.line_total - r.item.cogs_amount,
            "sale_total": r.sale.total_amount,
        }
        for r in lines
    ]
    return _Dataset(name, SALE_ITEM_COLUMNS, rows)


def _quick_sales(session: Session, ctx: RequestContext, **filters: Any) -> _Dataset:
    shop = get_shop(session, ctx.shop_id)
    items, _ = quick_sale_service.list_quick_sales(session, ctx.shop_id, limit=None, **filters)
    rows = []
    for r in items:
        s = r.sale
        rows.append(
            {
                "quick_no": s.quick_no,
                "sale_date": s.sale_date,
                "customer": r.customer_name,
                "status": s.status.value.title(),
                "payment_type": None if s.payment_type is None else s.payment_type.value.title(),
                "payment_method": None if s.payment_method is None else s.payment_method.value,
                "payment_reference": s.payment_reference,
                "gross": s.gross_amount,
                "discount": s.discount,
                "total": s.total_amount,
                "amount_paid": s.amount_paid,
                "on_khata": None
                if s.amount_paid is None
                else max(s.total_amount - s.amount_paid, Decimal("0.00")),
                "profit": quick_sale_service.NOT_AVAILABLE,  # money only: there is no cost, so no profit
                "note": s.note,
                "created_by": r.created_by_name,
                "posted_at": None if s.posted_at is None else _local(s.posted_at, shop.timezone),
                "void_reason": s.void_reason,
            }
        )
    return _Dataset("quick_sales", QUICK_SALE_COLUMNS, rows)


def _promotions(session: Session, ctx: RequestContext, **filters: Any) -> _Dataset:
    shop = get_shop(session, ctx.shop_id)
    views, _ = promotion_service.list_promotions(session, ctx.shop_id, limit=None, **filters)
    rows = []
    for v in views:
        p = v.promotion
        rows.append(
            {
                "name": p.name,
                "terms": promotion_calculation.describe_terms(promotion_service.rule_of(p)),
                "promo_type": p.promo_type.value.replace("_", " ").title(),
                "scope": p.scope.value.title(),
                "status": v.effective_status.value.title(),
                "coupon_code": p.coupon_code,
                "audience": p.audience.value.replace("_", " ").title(),
                "priority": p.priority,
                "stackable": "Yes" if p.stackable else "No",
                "starts_at": _local(p.starts_at, shop.timezone) if p.starts_at else None,
                "ends_at": _local(p.ends_at, shop.timezone) if p.ends_at else None,
                "min_cart_value": p.min_cart_value,
                "min_quantity": p.min_quantity,
                "max_discount": p.max_discount,
                "usage_limit": p.usage_limit,
                "per_customer_limit": p.per_customer_limit,
                "used_count": v.used_count,
                "discount_given": v.discount_given,
                "created_by": v.created_by_name,
                "description": p.description,
            }
        )
    return _Dataset("promotions", PROMOTION_COLUMNS, rows)


def _promotion_usage(session: Session, ctx: RequestContext, **filters: Any) -> _Dataset:
    rows = [
        {
            "sale_date": r.sale.sale_date,
            "invoice_no": r.sale.invoice_no,
            "sale_status": r.sale.status.value.title(),
            "customer": r.customer_name,
            "name": r.use.name,
            "terms": r.use.terms,
            "coupon_code": r.use.coupon_code,
            "discount_amount": r.use.discount_amount,
            "basis": r.use.basis,
        }
        for r in promotion_service.list_usage(session, ctx.shop_id, **filters)
    ]
    return _Dataset("promotion_usage", PROMOTION_USAGE_COLUMNS, rows)


def _price_history(session: Session, ctx: RequestContext, **filters: Any) -> _Dataset:
    shop = get_shop(session, ctx.shop_id)
    observations, _ = price_comparison_service.list_history(session, ctx.shop_id, limit=None, **filters)
    rows = [
        {
            "checked_at": _local(o.checked_at, shop.timezone),
            "barcode": o.barcode,
            "provider": o.provider,
            "product_name": o.product_name,
            "brand": o.brand,
            "price": o.price,
            "currency": o.currency,
            "location": o.location_text,
            "observed_on": o.observed_on,
            "source_url": o.source_url,
        }
        for o in observations
    ]
    return _Dataset("price_history", PRICE_HISTORY_COLUMNS, rows)


def _sales_summary(session: Session, ctx: RequestContext, **filters: Any) -> _Dataset:
    summary = sales_report_service.sales_summary(session, ctx.shop_id, **filters)
    rows = [
        {
            "day": d.day,
            "detailed_sales": d.detailed.sales_count,
            "detailed_gross": d.detailed.gross,
            "detailed_discount": d.detailed.discount,
            "detailed_net": d.detailed.net,
            "quick_sales": d.quick.sales_count,
            "quick_gross": d.quick.gross,
            "quick_discount": d.quick.discount,
            "quick_net": d.quick.net,
            "gross": d.combined.gross,
            "discount": d.combined.discount,
            "net": d.combined.net,
        }
        for d in summary.days
    ]
    return _Dataset("sales_summary", SALES_SUMMARY_COLUMNS, rows)


def _discount_report(session: Session, ctx: RequestContext, **filters: Any) -> _Dataset:
    report = sales_report_service.discount_report(session, ctx.shop_id, **filters)
    coupons = {c.code: c for c in report.by_coupon}
    rows = [
        {"promotion": p.name, "coupon_code": None, "uses": p.uses, "discount": p.discount}
        for p in report.by_promotion
    ]
    rows += [
        {"promotion": "Coupon", "coupon_code": c.code, "uses": c.uses, "discount": c.discount}
        for c in coupons.values()
    ]
    return _Dataset("discount_report", DISCOUNT_REPORT_COLUMNS, rows)


def _sales_returns(session: Session, ctx: RequestContext, **filters: Any) -> _Dataset:
    rows = []
    listed, _ = sales_return_service.list_returns(session, ctx.shop_id, limit=None, **filters)
    for row in listed:
        view = sales_return_service.get_view(session, ctx.shop_id, row.ret.id)
        rows.append(
            {
                "return_no": row.ret.return_no,
                "return_date": row.ret.return_date,
                "invoice_no": row.invoice_no,
                "customer": row.customer_name,
                "status": row.ret.status.value.title(),
                "refund_mode": row.ret.refund_mode.value.title(),
                "items": row.item_count,
                "total_refund": row.ret.total_refund,
                "cogs": view.cogs_total,
                "reason": row.ret.reason,
                "created_by": view.created_by_name,
                "void_reason": row.ret.void_reason,
            }
        )
    return _Dataset("sales_returns", SALES_RETURN_COLUMNS, rows)


def _purchase_returns(session: Session, ctx: RequestContext, **filters: Any) -> _Dataset:
    rows = []
    listed, _ = purchase_return_service.list_returns(session, ctx.shop_id, limit=None, **filters)
    for row in listed:
        view = purchase_return_service.get_view(session, ctx.shop_id, row.ret.id)
        rows.append(
            {
                "return_no": row.ret.return_no,
                "return_date": row.ret.return_date,
                "purchase_no": row.purchase_no,
                "supplier": row.supplier_name,
                "status": row.ret.status.value.title(),
                "credit_mode": row.ret.credit_mode.value.replace("_", " ").title(),
                "items": row.item_count,
                "total_amount": row.ret.total_amount,
                "reason": row.ret.reason,
                "created_by": view.created_by_name,
                "void_reason": row.ret.void_reason,
            }
        )
    return _Dataset("purchase_returns", PURCHASE_RETURN_COLUMNS, rows)


def _customers(session: Session, ctx: RequestContext, *, active: bool | None, **filters: Any) -> _Dataset:
    shop = get_shop(session, ctx.shop_id)
    accounts, _ = khata_service.list_accounts(session, ctx.shop_id, active=active, limit=None, **filters)
    rows = [
        {
            "name": a.customer.name,
            "phone": a.customer.phone,
            "email": a.customer.email,
            "address": a.customer.address,
            "balance": a.balance,
            "outstanding": a.outstanding,
            "advance": a.advance,
            "balance_status": BALANCE_LABELS[a.status],
            "entries": a.entry_count,
            "status": "Active" if a.customer.is_active else "Inactive",
            "notes": a.customer.notes,
            "created_at": _local(a.customer.created_at, shop.timezone),
        }
        for a in accounts
    ]
    return _Dataset("customers", CUSTOMER_COLUMNS, rows)


def _suppliers(session: Session, ctx: RequestContext, *, active: bool | None, q: str | None) -> _Dataset:
    shop = get_shop(session, ctx.shop_id)
    views, _ = supplier_service.list_suppliers(session, ctx.shop_id, q=q, active=active, limit=None)
    rows = [
        {
            "name": v.supplier.name,
            "phone": v.supplier.phone,
            "alternate_phone": v.supplier.alternate_phone,
            "email": v.supplier.email,
            "address": v.supplier.address,
            "gstin": v.supplier.gstin,
            "products": v.product_count,
            "status": "Active" if v.supplier.is_active else "Inactive",
            "notes": v.supplier.notes,
            "created_at": _local(v.supplier.created_at, shop.timezone),
        }
        for v in views
    ]
    return _Dataset("suppliers", SUPPLIER_COLUMNS, rows)


def _customer_ledger(session: Session, ctx: RequestContext, customer_id: int, **filters: Any) -> _Dataset:
    shop = get_shop(session, ctx.shop_id)
    customer = customer_service.get_customer(session, ctx.shop_id, customer_id)  # 404 for another shop's
    entries, _ = khata_service.get_customer_ledger(
        session, ctx.shop_id, customer_id, newest_first=False, limit=None, **filters
    )
    rows = [
        {
            "entry_date": e.entry_date,
            "customer": customer.name,
            "phone": customer.phone,
            "entry_type": e.entry_type.value,
            "debit": e.amount_delta if e.amount_delta > 0 else None,  # the customer owes more
            "credit": -e.amount_delta if e.amount_delta < 0 else None,  # the customer owes less
            "balance_after": e.balance_after,
            "payment_method": e.payment_method.value if e.payment_method else None,
            "payment_reference": e.payment_reference,
            "note": e.note,
            "reference_type": e.reference_type.value if e.reference_type else None,
            "reference_id": e.reference_id,
            "reference_no": e.reference_no,
            "reverses": e.reverses_entry_id,
            "reversed_by": e.reversed_by_entry_id,
            "created_by": e.created_by_name,
            "created_at": _local(e.created_at, shop.timezone),
        }
        for e in entries
    ]
    return _Dataset(f"customer_{customer_id}_ledger", CUSTOMER_LEDGER_COLUMNS, rows)


def _file(session: Session, ctx: RequestContext, dataset: _Dataset, fmt: ExportFormat) -> ExportFile:
    today = shop_today(get_shop(session, ctx.shop_id))
    return render(fmt, name=dataset.name, columns=dataset.columns, rows=dataset.rows, on_date=today)


def export_products(
    session: Session, ctx: RequestContext, fmt: ExportFormat, *, active: bool | None = True, **filters: Any
) -> ExportFile:
    return _file(session, ctx, _products(session, ctx, active=active, **filters), fmt)


def export_inventory(session: Session, ctx: RequestContext, fmt: ExportFormat, **filters: Any) -> ExportFile:
    return _file(session, ctx, _inventory(session, ctx, **filters), fmt)


def export_inventory_history(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    product_id: int | None = None,
    txn_type: InventoryTxnType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    dataset = _transactions(
        session, ctx, product_id=product_id, txn_type=txn_type, date_from=date_from, date_to=date_to
    )
    return _file(session, ctx, dataset, fmt)


def export_purchases(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    q: str | None = None,
    supplier_id: int | None = None,
    statuses: list[PurchaseStatus] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    dataset = _purchases(
        session, ctx, q=q, supplier_id=supplier_id, statuses=statuses, date_from=date_from, date_to=date_to
    )
    return _file(session, ctx, dataset, fmt)


def export_purchase_items(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    q: str | None = None,
    supplier_id: int | None = None,
    statuses: list[PurchaseStatus] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    dataset = _purchase_items(
        session, ctx, q=q, supplier_id=supplier_id, statuses=statuses, date_from=date_from, date_to=date_to
    )
    return _file(session, ctx, dataset, fmt)


def export_purchase_details(
    session: Session, ctx: RequestContext, fmt: ExportFormat, purchase_id: int
) -> ExportFile:
    """One purchase with all its lines: the header fields repeat on every row, as spreadsheets prefer."""
    purchase_service.get_purchase_view(session, ctx.shop_id, purchase_id)  # 404 for another shop's purchase
    dataset = _purchase_items(session, ctx, name=f"purchase_{purchase_id}", purchase_id=purchase_id)
    return _file(session, ctx, dataset, fmt)


def export_customers(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    active: bool | None = True,
    q: str | None = None,
    balance: BalanceStatus | None = None,
) -> ExportFile:
    return _file(session, ctx, _customers(session, ctx, active=active, q=q, balance=balance), fmt)


def export_suppliers(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    active: bool | None = True,
    q: str | None = None,
) -> ExportFile:
    return _file(session, ctx, _suppliers(session, ctx, active=active, q=q), fmt)


def export_customer_ledger(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    customer_id: int,
    *,
    entry_type: CustomerLedgerEntryType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    """One customer's khata, oldest first, with debit and credit columns and the running balance."""
    dataset = _customer_ledger(
        session, ctx, customer_id, entry_type=entry_type, date_from=date_from, date_to=date_to
    )
    return _file(session, ctx, dataset, fmt)


def export_sales(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    q: str | None = None,
    customer_id: int | None = None,
    statuses: list[SaleStatus] | None = None,
    payment_type: PaymentType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    dataset = _sales(
        session,
        ctx,
        q=q,
        customer_id=customer_id,
        statuses=statuses,
        payment_type=payment_type,
        date_from=date_from,
        date_to=date_to,
    )
    return _file(session, ctx, dataset, fmt)


def export_sale_items(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    q: str | None = None,
    customer_id: int | None = None,
    statuses: list[SaleStatus] | None = None,
    payment_type: PaymentType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    dataset = _sale_items(
        session,
        ctx,
        q=q,
        customer_id=customer_id,
        statuses=statuses,
        payment_type=payment_type,
        date_from=date_from,
        date_to=date_to,
    )
    return _file(session, ctx, dataset, fmt)


def export_sale_details(session: Session, ctx: RequestContext, fmt: ExportFormat, sale_id: int) -> ExportFile:
    """One sale with all its lines: the header fields repeat on every row, as spreadsheets prefer."""
    sale_service.get_sale_view(session, ctx.shop_id, sale_id)  # 404 for another shop's sale
    dataset = _sale_items(session, ctx, name=f"sale_{sale_id}", sale_id=sale_id)
    return _file(session, ctx, dataset, fmt)


def export_quick_sales(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    q: str | None = None,
    customer_id: int | None = None,
    statuses: list[SaleStatus] | None = None,
    payment_type: PaymentType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    dataset = _quick_sales(
        session,
        ctx,
        q=q,
        customer_id=customer_id,
        statuses=statuses,
        payment_type=payment_type,
        date_from=date_from,
        date_to=date_to,
    )
    return _file(session, ctx, dataset, fmt)


def export_promotions(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    q: str | None = None,
    statuses: list[PromotionStatus] | None = None,
    promo_type: PromotionType | None = None,
    coupon_only: bool | None = None,
) -> ExportFile:
    dataset = _promotions(
        session, ctx, q=q, statuses=statuses, promo_type=promo_type, coupon_only=coupon_only
    )
    return _file(session, ctx, dataset, fmt)


def export_promotion_usage(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    promotion_id: int | None = None,
    coupon_only: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    """Every use of an offer on a sale, from the frozen snapshots (a voided sale shows as Void)."""
    dataset = _promotion_usage(
        session,
        ctx,
        promotion_id=promotion_id,
        coupon_only=coupon_only,
        date_from=date_from,
        date_to=date_to,
    )
    return _file(session, ctx, dataset, fmt)


def export_price_history(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    barcode: str | None = None,
    provider: str | None = None,
) -> ExportFile:
    """Every outside price this shop has saved from price checks. Information, not the shop's own prices."""
    return _file(session, ctx, _price_history(session, ctx, barcode=barcode, provider=provider), fmt)


def export_sales_summary(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    """One row per day: Detailed, Quick and Combined gross, discount and net."""
    dataset = _sales_summary(session, ctx, date_from=date_from, date_to=date_to)
    return _file(session, ctx, dataset, fmt)


def export_discount_report(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    """Discount given by offer and by coupon (needs the plan's advanced reports)."""
    dataset = _discount_report(session, ctx, date_from=date_from, date_to=date_to)
    return _file(session, ctx, dataset, fmt)


def export_sales_returns(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    q: str | None = None,
    statuses: list[Any] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    """One row per sales return, with the refund and the cost of the goods that came back."""
    dataset = _sales_returns(session, ctx, q=q, statuses=statuses, date_from=date_from, date_to=date_to)
    return _file(session, ctx, dataset, fmt)


def export_purchase_returns(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    q: str | None = None,
    statuses: list[Any] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    """One row per purchase return."""
    dataset = _purchase_returns(session, ctx, q=q, statuses=statuses, date_from=date_from, date_to=date_to)
    return _file(session, ctx, dataset, fmt)


# --- Phase 13: intelligence, planning, stock counting ------------------------------------------------


REORDER_COLUMNS = [
    Column("name", "Product"), Column("sku", "SKU"), Column("current_stock", "Current Stock", Kind.QUANTITY),
    Column("reorder_level", "Reorder Level", Kind.QUANTITY),
    Column("sold_recently", "Sold Recently", Kind.QUANTITY),
    Column("window_days", "Window (days)", Kind.INTEGER),
    Column("days_of_cover", "Days of Cover", Kind.QUANTITY),
    Column("suggested_quantity", "Suggested Quantity", Kind.QUANTITY), Column("supplier_name", "Supplier"),
    Column("unit_cost_used", "Unit Cost Used", Kind.MONEY), Column("cost_basis", "Cost Basis"),
    Column("estimated_cost", "Estimated Cost", Kind.MONEY), Column("reasons", "Reasons"),
]  # fmt: skip

AGING_COLUMNS = [
    Column("name", "Product"), Column("sku", "SKU"), Column("current_stock", "Current Stock", Kind.QUANTITY),
    Column("days_since_last_inbound", "Days Since Last Stock In", Kind.INTEGER),
    Column("stock_value", "Stock Value", Kind.MONEY),
]  # fmt: skip

SUPPLIER_ANALYTICS_COLUMNS = [
    Column("name", "Supplier"), Column("purchase_count", "Purchases", Kind.INTEGER),
    Column("total_value", "Total Value", Kind.MONEY),
    Column("average_purchase_value", "Average Purchase Value", Kind.MONEY),
    Column("supplied_product_count", "Products Supplied", Kind.INTEGER),
    Column("first_purchase_date", "First Purchase", Kind.DATE),
    Column("last_purchase_date", "Last Purchase", Kind.DATE),
    Column("delivery_performance_note", "Delivery Performance"),
]  # fmt: skip

CUSTOMER_ANALYTICS_COLUMNS = [
    Column("name", "Customer"), Column("detailed_sale_count", "Detailed Sales", Kind.INTEGER),
    Column("quick_sale_count", "Quick Sales", Kind.INTEGER),
    Column("total_purchases", "Total Purchases", Kind.MONEY),
    Column("average_transaction_value", "Average Transaction", Kind.MONEY),
    Column("last_purchase", "Last Purchase", Kind.DATE),
    Column("outstanding", "Outstanding", Kind.MONEY), Column("last_payment_date", "Last Payment", Kind.DATE),
    Column("segments", "Segments"),
]  # fmt: skip

STOCK_COUNT_COLUMNS = [
    Column("name", "Product"), Column("sku", "SKU"), Column("expected_quantity", "Expected", Kind.QUANTITY),
    Column("counted_quantity", "Counted", Kind.QUANTITY), Column("variance", "Variance", Kind.QUANTITY),
    Column("variance_value", "Variance Value", Kind.MONEY), Column("note", "Note"),
]  # fmt: skip


def export_reorder_recommendations(
    session: Session,
    ctx: RequestContext,
    fmt: ExportFormat,
    *,
    window_days: int | None = None,
    cover_days: int | None = None,
) -> ExportFile:
    """Today's reorder recommendations (see `ai_insights_service.reorder_recommendations`): a suggestion,
    never a purchase."""
    from app.services import ai_insights_service

    today = shop_today(get_shop(session, ctx.shop_id))
    kwargs: dict[str, Any] = {}
    if window_days is not None:
        kwargs["window_days"] = window_days
    if cover_days is not None:
        kwargs["cover_days"] = cover_days
    rows = ai_insights_service.reorder_recommendations(session, ctx.shop_id, today, **kwargs)
    data = [{**r.__dict__, "reasons": "; ".join(r.reasons)} for r in rows]
    return _file(session, ctx, _Dataset("reorder_recommendations", REORDER_COLUMNS, data), fmt)


def export_stock_aging(session: Session, ctx: RequestContext, fmt: ExportFormat) -> ExportFile:
    """See `inventory_intelligence_service.stock_aging`: an estimate from the ledger, not batch tracking."""
    from app.services import inventory_intelligence_service

    today = shop_today(get_shop(session, ctx.shop_id))
    rows = [r.__dict__ for r in inventory_intelligence_service.stock_aging(session, ctx.shop_id, today)]
    return _file(session, ctx, _Dataset("stock_aging", AGING_COLUMNS, rows), fmt)


def export_supplier_analytics(session: Session, ctx: RequestContext, fmt: ExportFormat) -> ExportFile:
    from app.services import supplier_intelligence_service

    rows = [
        a.__dict__ for a in supplier_intelligence_service.list_analytics(session, ctx.shop_id, limit=None)
    ]
    return _file(session, ctx, _Dataset("supplier_analytics", SUPPLIER_ANALYTICS_COLUMNS, rows), fmt)


def export_customer_analytics(session: Session, ctx: RequestContext, fmt: ExportFormat) -> ExportFile:
    from app.services import customer_intelligence_service

    today = shop_today(get_shop(session, ctx.shop_id))
    rows = []
    for a in customer_intelligence_service.list_analytics(session, ctx.shop_id, today, limit=None):
        row = dict(a.__dict__)
        row["segments"] = ", ".join(s.value for s in a.segments)
        rows.append(row)
    return _file(session, ctx, _Dataset("customer_analytics", CUSTOMER_ANALYTICS_COLUMNS, rows), fmt)


def export_stock_count(session: Session, ctx: RequestContext, fmt: ExportFormat, count_id: int) -> ExportFile:
    """One stock count's lines: expected, counted, variance and its value where cost is known."""
    from app.services import stock_count_service

    stock_count_service.get(session, ctx.shop_id, count_id)  # 404 for another shop's count
    rows = [i.__dict__ for i in stock_count_service.get_items(session, ctx.shop_id, count_id)]
    return _file(session, ctx, _Dataset(f"stock_count_{count_id}", STOCK_COUNT_COLUMNS, rows), fmt)


# --- Phase 14: CRM, loyalty, campaigns ----------------------------------------------------------------

CUSTOMER_GROUP_MEMBER_COLUMNS = [
    Column("customer_id", "Customer ID"), Column("name", "Customer"), Column("phone", "Phone"),
]  # fmt: skip

LOYALTY_LEDGER_COLUMNS = [
    Column("entry_date", "Date", Kind.DATE), Column("entry_type", "Type"),
    Column("points_delta", "Points", Kind.INTEGER), Column("reference_type", "Source"),
    Column("note", "Note"),
]  # fmt: skip

CAMPAIGN_SEND_COLUMNS = [
    Column("customer_id", "Customer ID"), Column("channel", "Channel"), Column("status", "Outcome"),
    Column("detail", "Detail"),
]  # fmt: skip


def export_customer_group(
    session: Session, ctx: RequestContext, fmt: ExportFormat, group_id: int
) -> ExportFile:
    """A customer group's current membership: who is in it, right now."""
    from app.models import Customer
    from app.services import crm_segment_service

    membership = crm_segment_service.members_of(
        session, ctx.shop_id, group_id
    )  # 404 for another shop's group
    customers = {
        c.id: c
        for c in session.scalars(
            select(Customer).where(Customer.shop_id == ctx.shop_id, Customer.id.in_(membership.customer_ids))
        )
    }
    rows = [
        {"customer_id": cid, "name": customers[cid].name, "phone": customers[cid].phone}
        for cid in membership.customer_ids
        if cid in customers
    ]
    return _file(
        session, ctx, _Dataset(f"customer_group_{group_id}", CUSTOMER_GROUP_MEMBER_COLUMNS, rows), fmt
    )


def export_loyalty_ledger(
    session: Session, ctx: RequestContext, fmt: ExportFormat, customer_id: int
) -> ExportFile:
    """One customer's full loyalty history, oldest rule intact: the ledger, exactly as recorded."""
    from app.services import loyalty_service

    rows_data, _ = loyalty_service.list_ledger(session, ctx.shop_id, customer_id, limit=None)
    rows = [
        {
            "entry_date": r.entry_date, "entry_type": r.entry_type.value, "points_delta": r.points_delta,
            "reference_type": r.reference_type, "note": r.note,
        }
        for r in rows_data
    ]  # fmt: skip
    return _file(session, ctx, _Dataset(f"loyalty_ledger_{customer_id}", LOYALTY_LEDGER_COLUMNS, rows), fmt)


def export_campaign_sends(
    session: Session, ctx: RequestContext, fmt: ExportFormat, campaign_id: int
) -> ExportFile:
    """One campaign's per-customer send outcomes, honest as recorded (mostly "Not Configured" until a real
    provider is set up)."""
    from app.services import campaign_service

    sends = campaign_service.send_outcomes(
        session, ctx.shop_id, campaign_id
    )  # 404 for another shop's campaign
    rows = [
        {
            "customer_id": s.customer_id, "channel": s.channel.value, "status": s.status.value,
            "detail": s.detail,
        }
        for s in sends
    ]  # fmt: skip
    return _file(session, ctx, _Dataset(f"campaign_sends_{campaign_id}", CAMPAIGN_SEND_COLUMNS, rows), fmt)
