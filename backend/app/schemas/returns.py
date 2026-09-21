from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import DocumentStatus, RefundMode, SupplierCreditMode
from app.schemas.common import Page, QuantityIn
from app.services import purchase_return_service as prs
from app.services import sales_return_service as srs

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class FieldProblem(BaseModel):
    field: str
    message: str


# --- Sales returns ---------------------------------------------------------------------------------


class SalesReturnItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sale_item_id: int
    quantity: QuantityIn


class SalesReturnCalculateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sale_id: int
    refund_mode: RefundMode | None = None
    items: Annotated[list[SalesReturnItemIn], Field(max_length=200)] = []


class SalesReturnCreate(BaseModel):
    """Goods coming back from a completed sale. The refund is worked out by the server, never sent."""

    model_config = ConfigDict(extra="forbid")

    sale_id: int
    refund_mode: RefundMode
    return_date: date | None = None  # defaults to today in the shop's timezone
    reason: Note | None = None
    items: Annotated[list[SalesReturnItemIn], Field(min_length=1, max_length=200)]


class VoidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class SalesReturnPreviewLineOut(BaseModel):
    sale_item_id: int | None
    product_name: str | None
    sku: str | None
    unit_code: str | None
    sold: Decimal | None
    already_returned: Decimal | None
    returnable: Decimal | None
    quantity: Decimal | None
    refund: Decimal | None
    errors: list[FieldProblem]


class SalesReturnPreviewOut(BaseModel):
    """What a return would refund, exactly as posting will work it out. Nothing is saved."""

    sale_id: int
    lines: list[SalesReturnPreviewLineOut]
    total_refund: Decimal
    cash_refundable: Decimal  # the most that can be given back as cash or UPI for this sale
    khata_allowed: bool
    errors: list[FieldProblem]

    @classmethod
    def from_preview(cls, p: srs.Preview) -> "SalesReturnPreviewOut":
        lines = [
            SalesReturnPreviewLineOut(
                **{**line.__dict__, "errors": [FieldProblem(field=f, message=m) for f, m in line.errors]}
            )
            for line in p.lines
        ]
        return cls(
            sale_id=p.sale_id,
            lines=lines,
            total_refund=p.total_refund,
            cash_refundable=p.cash_refundable,
            khata_allowed=p.khata_allowed,
            errors=[FieldProblem(field=f, message=m) for f, m in p.errors],
        )


class SalesReturnItemOut(BaseModel):
    id: int
    sale_item_id: int
    product_id: int
    sku: str
    product_name: str
    unit_code: str
    sold_quantity: Decimal
    quantity: Decimal
    refund_amount: Decimal
    unit_cost: Decimal | None
    cogs_amount: Decimal | None


class SalesReturnOut(BaseModel):
    id: int
    return_no: str
    status: DocumentStatus
    sale_id: int
    invoice_no: str
    customer_id: int | None
    customer_name: str | None
    return_date: date
    refund_mode: RefundMode
    total_refund: Decimal
    reason: str | None
    cogs_total: Decimal | None  # cost of the goods that came back; null if any cost was unknown
    created_by_name: str
    created_at: datetime
    void_reason: str | None
    voided_at: datetime | None
    items: list[SalesReturnItemOut]

    @classmethod
    def from_view(cls, v: srs.SalesReturnView) -> "SalesReturnOut":
        r = v.ret
        return cls(
            id=r.id,
            return_no=r.return_no,
            status=r.status,
            sale_id=r.sale_id,
            invoice_no=v.invoice_no,
            customer_id=v.customer_id,
            customer_name=v.customer_name,
            return_date=r.return_date,
            refund_mode=r.refund_mode,
            total_refund=r.total_refund,
            reason=r.reason,
            cogs_total=v.cogs_total,
            created_by_name=v.created_by_name,
            created_at=r.created_at,
            void_reason=r.void_reason,
            voided_at=r.voided_at,
            items=[
                SalesReturnItemOut(
                    id=i.item.id,
                    sale_item_id=i.item.sale_item_id,
                    product_id=i.item.product_id,
                    sku=i.sku,
                    product_name=i.product_name,
                    unit_code=i.unit_code,
                    sold_quantity=i.sold_quantity,
                    quantity=i.item.quantity,
                    refund_amount=i.item.refund_amount,
                    unit_cost=i.item.unit_cost,
                    cogs_amount=i.item.cogs_amount,
                )
                for i in v.items
            ],
        )


class SalesReturnRowOut(BaseModel):
    id: int
    return_no: str
    status: DocumentStatus
    sale_id: int
    invoice_no: str
    customer_name: str | None
    return_date: date
    refund_mode: RefundMode
    total_refund: Decimal
    item_count: int

    @classmethod
    def from_row(cls, row: srs.ReturnRow) -> "SalesReturnRowOut":
        r = row.ret
        return cls(
            id=r.id,
            return_no=r.return_no,
            status=r.status,
            sale_id=r.sale_id,
            invoice_no=row.invoice_no,
            customer_name=row.customer_name,
            return_date=r.return_date,
            refund_mode=r.refund_mode,
            total_refund=r.total_refund,
            item_count=row.item_count,
        )


class SalesReturnListOut(Page):
    items: list[SalesReturnRowOut]


# --- Purchase returns ------------------------------------------------------------------------------


class PurchaseReturnItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_item_id: int
    quantity: QuantityIn


class PurchaseReturnCalculateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_id: int
    items: Annotated[list[PurchaseReturnItemIn], Field(max_length=200)] = []


class PurchaseReturnCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_id: int
    credit_mode: SupplierCreditMode
    return_date: date | None = None
    reason: Note | None = None
    items: Annotated[list[PurchaseReturnItemIn], Field(min_length=1, max_length=200)]


class PurchaseReturnPreviewLineOut(BaseModel):
    purchase_item_id: int | None
    product_name: str | None
    sku: str | None
    unit_code: str | None
    bought: Decimal | None
    already_returned: Decimal | None
    returnable: Decimal | None
    in_stock: Decimal | None  # goods that were already sold cannot be sent back
    quantity: Decimal | None
    credit: Decimal | None
    errors: list[FieldProblem]


class PurchaseReturnPreviewOut(BaseModel):
    purchase_id: int
    lines: list[PurchaseReturnPreviewLineOut]
    total_credit: Decimal

    @classmethod
    def from_preview(cls, p: prs.Preview) -> "PurchaseReturnPreviewOut":
        lines = [
            PurchaseReturnPreviewLineOut(
                **{**line.__dict__, "errors": [FieldProblem(field=f, message=m) for f, m in line.errors]}
            )
            for line in p.lines
        ]
        return cls(purchase_id=p.purchase_id, lines=lines, total_credit=p.total_credit)


class PurchaseReturnItemOut(BaseModel):
    id: int
    purchase_item_id: int
    product_id: int
    sku: str
    product_name: str
    unit_code: str
    bought_quantity: Decimal
    quantity: Decimal
    unit_cost: Decimal
    line_total: Decimal


class PurchaseReturnOut(BaseModel):
    id: int
    return_no: str
    status: DocumentStatus
    purchase_id: int
    purchase_no: str
    supplier_id: int
    supplier_name: str
    return_date: date
    credit_mode: SupplierCreditMode
    total_amount: Decimal
    reason: str | None
    created_by_name: str
    created_at: datetime
    void_reason: str | None
    voided_at: datetime | None
    items: list[PurchaseReturnItemOut]

    @classmethod
    def from_view(cls, v: prs.PurchaseReturnView) -> "PurchaseReturnOut":
        r = v.ret
        return cls(
            id=r.id,
            return_no=r.return_no,
            status=r.status,
            purchase_id=r.purchase_id,
            purchase_no=v.purchase_no,
            supplier_id=v.supplier_id,
            supplier_name=v.supplier_name,
            return_date=r.return_date,
            credit_mode=r.credit_mode,
            total_amount=r.total_amount,
            reason=r.reason,
            created_by_name=v.created_by_name,
            created_at=r.created_at,
            void_reason=r.void_reason,
            voided_at=r.voided_at,
            items=[
                PurchaseReturnItemOut(
                    id=i.item.id,
                    purchase_item_id=i.item.purchase_item_id,
                    product_id=i.item.product_id,
                    sku=i.sku,
                    product_name=i.product_name,
                    unit_code=i.unit_code,
                    bought_quantity=i.bought_quantity,
                    quantity=i.item.quantity,
                    unit_cost=i.item.unit_cost,
                    line_total=i.item.line_total,
                )
                for i in v.items
            ],
        )


class PurchaseReturnRowOut(BaseModel):
    id: int
    return_no: str
    status: DocumentStatus
    purchase_id: int
    purchase_no: str
    supplier_name: str
    return_date: date
    credit_mode: SupplierCreditMode
    total_amount: Decimal
    item_count: int

    @classmethod
    def from_row(cls, row: prs.ReturnRow) -> "PurchaseReturnRowOut":
        r = row.ret
        return cls(
            id=r.id,
            return_no=r.return_no,
            status=r.status,
            purchase_id=r.purchase_id,
            purchase_no=row.purchase_no,
            supplier_name=row.supplier_name,
            return_date=r.return_date,
            credit_mode=r.credit_mode,
            total_amount=r.total_amount,
            item_count=row.item_count,
        )


class PurchaseReturnListOut(Page):
    items: list[PurchaseReturnRowOut]
