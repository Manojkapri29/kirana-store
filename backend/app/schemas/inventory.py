from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models.enums import AdjustmentReason, InventoryTxnType, StockReferenceType
from app.schemas.common import MoneyIn, Page, QuantityIn
from app.services.inventory_service import InventoryRow, StockStatus, TransactionRow
from app.services.product_service import ProductView


class InventoryItemOut(BaseModel):
    product_id: int
    sku: str
    name: str
    brand: str | None
    barcode: str | None
    category_name: str
    unit_code: str
    unit_name: str
    allows_decimal: bool
    current_stock: Decimal
    reorder_level: Decimal
    avg_cost: Decimal | None
    status: StockStatus
    is_active: bool

    @classmethod
    def from_row(cls, row: InventoryRow) -> "InventoryItemOut":
        return cls(**row.__dict__)


class InventoryListOut(Page):
    items: list[InventoryItemOut]


class StockOut(BaseModel):
    """Current stock of one product, derived from the ledger."""

    product_id: int
    sku: str
    name: str
    unit_code: str
    unit_name: str
    allows_decimal: bool
    current_stock: Decimal
    reorder_level: Decimal
    status: StockStatus
    is_active: bool

    @classmethod
    def from_view(cls, view: ProductView) -> "StockOut":
        p = view.product
        return cls(
            product_id=p.id,
            sku=p.sku,
            name=p.name,
            unit_code=view.unit.code,
            unit_name=view.unit.name,
            allows_decimal=view.unit.allows_decimal,
            current_stock=view.current_stock,
            reorder_level=p.reorder_level,
            status=view.stock_status,
            is_active=p.is_active,
        )


class TransactionOut(BaseModel):
    id: int
    product_id: int
    sku: str
    product_name: str
    txn_type: InventoryTxnType
    qty_delta: Decimal
    balance_after: Decimal
    unit_cost: Decimal | None
    txn_date: date
    reference_type: StockReferenceType | None
    reference_id: int | None
    reason_code: AdjustmentReason | None
    note: str | None
    created_by_name: str
    created_at: datetime
    purchase_id: int | None = None  # set when the row came from a purchase line
    purchase_no: str | None = None

    @classmethod
    def from_row(cls, row: TransactionRow) -> "TransactionOut":
        return cls(**row.__dict__)


class TransactionListOut(Page):
    items: list[TransactionOut]


class OpeningStockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int
    quantity: QuantityIn
    unit_cost: MoneyIn | None = None
    txn_date: date | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class OpeningStockOut(BaseModel):
    transaction: TransactionOut
    stock: StockOut
