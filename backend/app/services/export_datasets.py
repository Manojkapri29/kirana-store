"""What each export contains: columns and rows for products, inventory, stock history and purchases.

Rows come from the domain services (never from tables directly), so the same shop scoping and the same
stock calculation apply to exports as to the screens. The file format is handled by `export_service`.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models.enums import InventoryTxnType, PurchaseStatus
from app.services import inventory_service, product_service, purchase_service
from app.services.export_service import Column, ExportFile, ExportFormat, Kind, render
from app.services.inventory_service import StockStatus
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
