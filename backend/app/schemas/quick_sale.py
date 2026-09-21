from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models.enums import PaymentMethod, PaymentType, SaleStatus
from app.schemas.common import MoneyIn, Page
from app.services.quick_sale_service import NOT_AVAILABLE, QuickSaleRow, QuickSaleView

Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class QuickSaleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gross_amount: MoneyIn  # what was taken, before any discount
    discount: MoneyIn | None = None  # one amount off the whole entry (there are no product lines)
    sale_date: date | None = None  # defaults to today in the shop's timezone
    customer_id: int | None = None
    note: Note | None = None


class QuickSaleUpdate(BaseModel):
    """Partial update of a draft. Send `customer_id: null` to remove the customer."""

    model_config = ConfigDict(extra="forbid")

    gross_amount: MoneyIn | None = None
    discount: MoneyIn | None = None
    sale_date: date | None = None
    customer_id: int | None = None
    note: Note | None = None


class QuickSaleOut(BaseModel):
    id: int
    quick_no: str | None
    status: SaleStatus
    customer_id: int | None
    customer_name: str | None
    sale_date: date
    gross_amount: Decimal
    discount: Decimal
    total_amount: Decimal  # the net amount: gross less the discount
    payment_type: PaymentType | None
    amount_paid: Decimal | None
    credit_amount: Decimal  # the part on the customer's khata
    payment_method: PaymentMethod | None
    payment_reference: str | None
    note: str | None
    created_by_name: str
    created_at: datetime
    updated_at: datetime
    posted_at: datetime | None
    posted_by_name: str | None
    void_reason: str | None
    voided_at: datetime | None
    # A quick sale is money only: it never has a cost or a product-level profit. Always "Not Available".
    gross_profit: None = None
    profit_label: str = NOT_AVAILABLE

    @classmethod
    def from_view(cls, view: QuickSaleView) -> "QuickSaleOut":
        s = view.sale
        return cls(
            id=s.id,
            quick_no=s.quick_no,
            status=s.status,
            customer_id=s.customer_id,
            customer_name=view.customer_name,
            sale_date=s.sale_date,
            gross_amount=s.gross_amount,
            discount=s.discount,
            total_amount=s.total_amount,
            payment_type=s.payment_type,
            amount_paid=s.amount_paid,
            credit_amount=view.credit_amount,
            payment_method=s.payment_method,
            payment_reference=s.payment_reference,
            note=s.note,
            created_by_name=view.created_by_name,
            created_at=s.created_at,
            updated_at=s.updated_at,
            posted_at=s.posted_at,
            posted_by_name=view.posted_by_name,
            void_reason=s.void_reason,
            voided_at=s.voided_at,
        )


class QuickSaleSummaryOut(BaseModel):
    id: int
    quick_no: str | None
    status: SaleStatus
    customer_id: int | None
    customer_name: str | None
    sale_date: date
    gross_amount: Decimal
    discount: Decimal
    total_amount: Decimal
    amount_paid: Decimal | None
    payment_type: PaymentType | None
    payment_method: PaymentMethod | None
    created_by_name: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: QuickSaleRow) -> "QuickSaleSummaryOut":
        s = row.sale
        return cls(
            id=s.id,
            quick_no=s.quick_no,
            status=s.status,
            customer_id=s.customer_id,
            customer_name=row.customer_name,
            sale_date=s.sale_date,
            gross_amount=s.gross_amount,
            discount=s.discount,
            total_amount=s.total_amount,
            amount_paid=s.amount_paid,
            payment_type=s.payment_type,
            payment_method=s.payment_method,
            created_by_name=row.created_by_name,
            created_at=s.created_at,
        )


class QuickSaleListOut(Page):
    items: list[QuickSaleSummaryOut]
