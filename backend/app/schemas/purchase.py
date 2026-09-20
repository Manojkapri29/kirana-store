from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import InventoryTxnType, PurchaseStatus
from app.schemas.common import MoneyIn, Page, QuantityIn
from app.services.purchase_service import PurchaseItemView, PurchaseRow, PurchaseView

InvoiceNo = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class PurchaseItemIn(BaseModel):
    """One line. The unit is the product's own unit; `unit_id` is optional and must match it."""

    model_config = ConfigDict(extra="forbid")

    product_id: int
    quantity: QuantityIn
    unit_cost: MoneyIn  # price per unit, before the line discount
    discount: MoneyIn | None = None  # an amount off the whole line, not a percentage
    unit_id: int | None = None


class PurchaseItemUpdate(BaseModel):
    """Partial update of one line: only the fields sent change."""

    model_config = ConfigDict(extra="forbid")

    product_id: int | None = None
    quantity: QuantityIn | None = None
    unit_cost: MoneyIn | None = None
    discount: MoneyIn | None = None
    unit_id: int | None = None


class PurchaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: int
    supplier_invoice_no: InvoiceNo | None = None
    purchase_date: date | None = None  # defaults to today in the shop's timezone
    notes: Notes | None = None
    items: Annotated[list[PurchaseItemIn], Field(max_length=200)] = []


class PurchaseUpdate(BaseModel):
    """Partial update of a draft's header."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: int | None = None
    supplier_invoice_no: InvoiceNo | None = None
    purchase_date: date | None = None
    notes: Notes | None = None


class PurchaseItemsReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: Annotated[list[PurchaseItemIn], Field(max_length=200)]


class PurchaseVoidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class InventoryEffectOut(BaseModel):
    """One stock-ledger row behind a purchase line (the PURCHASE row, and a REVERSAL if it was voided)."""

    id: int
    txn_type: InventoryTxnType
    qty_delta: Decimal
    txn_date: date


class PurchaseItemOut(BaseModel):
    id: int
    product_id: int
    sku: str
    product_name: str
    unit_id: int
    unit_code: str
    unit_name: str
    unit_allows_decimal: bool
    quantity: Decimal
    unit_cost: Decimal
    discount: Decimal
    line_total: Decimal
    # Posting snapshot (null on a draft). NULL cost means "unknown".
    stock_before: Decimal | None
    stock_after: Decimal | None
    avg_cost_before: Decimal | None
    avg_cost_after: Decimal | None
    inventory_effects: list[InventoryEffectOut]

    @classmethod
    def from_view(cls, view: PurchaseItemView) -> "PurchaseItemOut":
        i = view.item
        return cls(
            id=i.id,
            product_id=i.product_id,
            sku=view.product_sku,
            product_name=view.product_name,
            unit_id=i.unit_id,
            unit_code=view.unit_code,
            unit_name=view.unit_name,
            unit_allows_decimal=view.unit_allows_decimal,
            quantity=i.quantity,
            unit_cost=i.unit_cost,
            discount=i.discount,
            line_total=i.line_total,
            stock_before=i.stock_before,
            stock_after=None if i.stock_before is None else i.stock_before + i.quantity,
            avg_cost_before=i.avg_cost_before,
            avg_cost_after=i.avg_cost_after,
            inventory_effects=[
                InventoryEffectOut(id=e.id, txn_type=e.txn_type, qty_delta=e.qty_delta, txn_date=e.txn_date)
                for e in view.effects
            ],
        )


class PurchaseOut(BaseModel):
    id: int
    purchase_no: str | None
    status: PurchaseStatus
    supplier_id: int
    supplier_name: str
    supplier_invoice_no: str | None
    purchase_date: date
    notes: str | None
    total_amount: Decimal
    item_count: int
    created_by_name: str
    created_at: datetime
    updated_at: datetime
    posted_at: datetime | None
    posted_by_name: str | None
    void_reason: str | None
    voided_at: datetime | None
    replaces_id: int | None
    replaced_by_id: int | None
    items: list[PurchaseItemOut]

    @classmethod
    def from_view(cls, view: PurchaseView) -> "PurchaseOut":
        p = view.purchase
        return cls(
            id=p.id,
            purchase_no=p.purchase_no,
            status=p.status,
            supplier_id=p.supplier_id,
            supplier_name=view.supplier_name,
            supplier_invoice_no=p.supplier_invoice_no,
            purchase_date=p.purchase_date,
            notes=p.notes,
            total_amount=p.total_amount,
            item_count=len(view.items),
            created_by_name=view.created_by_name,
            created_at=p.created_at,
            updated_at=p.updated_at,
            posted_at=p.posted_at,
            posted_by_name=view.posted_by_name,
            void_reason=p.void_reason,
            voided_at=p.voided_at,
            replaces_id=p.replaces_id,
            replaced_by_id=view.replaced_by_id,
            items=[PurchaseItemOut.from_view(i) for i in view.items],
        )


class PurchaseSummaryOut(BaseModel):
    """A purchase as one row of a list (no lines)."""

    id: int
    purchase_no: str | None
    status: PurchaseStatus
    supplier_id: int
    supplier_name: str
    supplier_invoice_no: str | None
    purchase_date: date
    total_amount: Decimal
    item_count: int
    created_by_name: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: PurchaseRow) -> "PurchaseSummaryOut":
        p = row.purchase
        return cls(
            id=p.id,
            purchase_no=p.purchase_no,
            status=p.status,
            supplier_id=p.supplier_id,
            supplier_name=row.supplier_name,
            supplier_invoice_no=p.supplier_invoice_no,
            purchase_date=p.purchase_date,
            total_amount=p.total_amount,
            item_count=row.item_count,
            created_by_name=row.created_by_name,
            created_at=p.created_at,
        )


class PurchaseListOut(Page):
    items: list[PurchaseSummaryOut]


class SupplierPurchaseTotalsOut(BaseModel):
    posted_count: int
    posted_total: Decimal
