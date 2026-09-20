from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.schemas.common import MoneyIn, Page, QuantityIn
from app.services.inventory_service import StockStatus
from app.services.product_service import ProductView, SaveResult

Sku = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]
ProductName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Brand = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
Barcode = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]


class ProductCreate(BaseModel):
    """Everything a new product can be created with.

    `extra="forbid"`: unknown fields are errors. That is how `current_stock` and `avg_cost` (not
    editable: stock lives in the ledger, average cost is calculated) are refused rather than ignored.
    """

    model_config = ConfigDict(extra="forbid")

    sku: Sku
    name: ProductName
    brand: Brand | None = None
    category_id: int
    unit_id: int
    default_supplier_id: int | None = None
    reorder_level: QuantityIn = Decimal("0")
    mrp: MoneyIn | None = None
    selling_price: MoneyIn
    purchase_price: MoneyIn | None = None
    barcode: Barcode | None = None
    # Optional starting stock, recorded in the same transaction as an OPENING ledger row.
    opening_stock: QuantityIn | None = None
    opening_stock_cost: MoneyIn | None = None


class ProductUpdate(BaseModel):
    """Partial update: only the fields sent are changed. Send `null` to clear an optional field."""

    model_config = ConfigDict(extra="forbid")

    sku: Sku | None = None
    name: ProductName | None = None
    brand: Brand | None = None
    category_id: int | None = None
    unit_id: int | None = None
    default_supplier_id: int | None = None
    reorder_level: QuantityIn | None = None
    mrp: MoneyIn | None = None
    selling_price: MoneyIn | None = None
    purchase_price: MoneyIn | None = None
    barcode: Barcode | None = None


class ProductOut(BaseModel):
    id: int
    sku: str
    name: str
    brand: str | None
    barcode: str | None
    category_id: int
    category_name: str
    unit_id: int
    unit_code: str
    unit_name: str
    unit_allows_decimal: bool
    default_supplier_id: int | None
    default_supplier_name: str | None
    reorder_level: Decimal
    mrp: Decimal | None
    selling_price: Decimal
    purchase_price: Decimal | None
    avg_cost: Decimal | None
    is_active: bool
    current_stock: Decimal  # derived from the inventory ledger; there is no such column on products
    stock_status: StockStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_view(cls, view: ProductView) -> "ProductOut":
        p = view.product
        return cls(
            id=p.id,
            sku=p.sku,
            name=p.name,
            brand=p.brand,
            barcode=p.barcode,
            category_id=p.category_id,
            category_name=view.category_name,
            unit_id=p.unit_id,
            unit_code=view.unit.code,
            unit_name=view.unit.name,
            unit_allows_decimal=view.unit.allows_decimal,
            default_supplier_id=p.default_supplier_id,
            default_supplier_name=view.supplier_name,
            reorder_level=p.reorder_level,
            mrp=p.mrp,
            selling_price=p.selling_price,
            purchase_price=p.purchase_price,
            avg_cost=p.avg_cost,
            is_active=p.is_active,
            current_stock=view.current_stock,
            stock_status=view.stock_status,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


class ProductSaved(BaseModel):
    product: ProductOut
    warnings: list[str]

    @classmethod
    def from_result(cls, result: SaveResult) -> "ProductSaved":
        return cls(product=ProductOut.from_view(result.view), warnings=result.warnings)


class ProductListOut(Page):
    items: list[ProductOut]
