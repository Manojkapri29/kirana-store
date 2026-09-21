from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import InventoryTxnType, PaymentMethod, PaymentType, SaleStatus
from app.schemas.common import MoneyIn, Page, QuantityIn
from app.schemas.promotion import AppliedPromotionOut, CouponOut, NotAppliedOut
from app.services.sale_service import Preview, SaleItemView, SaleRow, SaleView

Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
CouponCode = Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]


class SaleItemIn(BaseModel):
    """One line. The unit is the product's own unit. Leave `unit_price` out to use the product's price."""

    model_config = ConfigDict(extra="forbid")

    product_id: int
    quantity: QuantityIn
    unit_price: MoneyIn | None = None
    discount: MoneyIn | None = None  # an amount off this line, not a percentage


class SaleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int | None = None
    sale_date: date | None = None  # defaults to today in the shop's timezone
    notes: Notes | None = None
    discount: MoneyIn | None = None  # an amount off the whole bill
    coupon_code: CouponCode | None = None  # offers are worked out by the server, never sent
    items: Annotated[list[SaleItemIn], Field(max_length=200)] = []


class SaleUpdate(BaseModel):
    """Partial update of a draft's header. Send `customer_id: null` to remove the customer."""

    model_config = ConfigDict(extra="forbid")

    customer_id: int | None = None
    sale_date: date | None = None
    notes: Notes | None = None
    discount: MoneyIn | None = None
    coupon_code: CouponCode | None = None  # send null (or "") to remove the coupon


class SaleItemsReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: Annotated[list[SaleItemIn], Field(max_length=200)]


class SalePostIn(BaseModel):
    """How the customer pays. Leave `amount_paid` out to mean "paid in full"; less than the total makes it a
    credit sale (a customer is then required). More than the total is refused."""

    model_config = ConfigDict(extra="forbid")

    amount_paid: MoneyIn | None = None
    payment_method: PaymentMethod | None = None
    payment_reference: Reference | None = None


class SaleVoidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class CalculateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discount: MoneyIn | None = None
    amount_paid: MoneyIn | None = None  # to preview how much of the bill would go on the khata
    customer_id: int | None = None  # some offers depend on the customer
    coupon_code: CouponCode | None = None
    items: Annotated[list[SaleItemIn], Field(max_length=200)] = []


class InventoryEffectOut(BaseModel):
    """One stock-ledger row behind a sale line (the SALE row, and a REVERSAL if it was voided)."""

    id: int
    txn_type: InventoryTxnType
    qty_delta: Decimal
    txn_date: date


class SaleItemOut(BaseModel):
    id: int
    product_id: int
    sku: str
    product_name: str
    unit_id: int
    unit_code: str
    unit_name: str
    unit_allows_decimal: bool
    quantity: Decimal
    unit_price: Decimal
    mrp: Decimal | None
    discount: Decimal
    gross: Decimal  # quantity x price, before the discount
    line_total: Decimal  # the line after the cashier's own discount
    promotion_discount: Decimal  # this line's share of what offers took off the bill
    net_total: Decimal  # line_total less promotion_discount: what the line really earned
    # Cost snapshot at posting. Null means unknown (never 0), and then the profit is unknown too.
    unit_cost: Decimal | None
    cogs_amount: Decimal | None
    profit: Decimal | None
    inventory_effects: list[InventoryEffectOut]

    @classmethod
    def from_view(cls, view: SaleItemView) -> "SaleItemOut":
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
            unit_price=i.unit_price,
            mrp=i.mrp,
            discount=i.discount,
            gross=i.line_total + i.discount,
            line_total=i.line_total,
            promotion_discount=i.promotion_discount,
            net_total=i.line_total - i.promotion_discount,
            unit_cost=i.unit_cost,
            cogs_amount=i.cogs_amount,
            profit=view.profit,
            inventory_effects=[
                InventoryEffectOut(id=e.id, txn_type=e.txn_type, qty_delta=e.qty_delta, txn_date=e.txn_date)
                for e in view.effects
            ],
        )


class SaleOut(BaseModel):
    id: int
    invoice_no: str | None
    status: SaleStatus
    customer_id: int | None
    customer_name: str | None
    sale_date: date
    notes: str | None
    subtotal: Decimal
    discount: Decimal  # the cashier's own bill discount
    promotion_discount: Decimal  # what offers and coupons took off (see `promotions`)
    coupon_code: str | None
    total_amount: Decimal
    payment_type: PaymentType | None
    amount_paid: Decimal | None
    credit_amount: Decimal  # the part on the customer's khata
    payment_method: PaymentMethod | None
    payment_reference: str | None
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
    # Cost of goods sold and gross profit. Null unless the sale is posted AND every line's cost was known.
    cogs_total: Decimal | None
    gross_profit: Decimal | None
    lines_without_cost: int
    warnings: list[str]
    # Which offers the bill got: the frozen snapshot once posted, the current worth while a draft.
    promotions: list[AppliedPromotionOut]
    promotions_out_of_date: bool  # a draft whose offers are worth something else now than when last saved
    items: list[SaleItemOut]

    @classmethod
    def from_view(cls, view: SaleView) -> "SaleOut":
        s = view.sale
        return cls(
            id=s.id,
            invoice_no=s.invoice_no,
            status=s.status,
            customer_id=s.customer_id,
            customer_name=view.customer_name,
            sale_date=s.sale_date,
            notes=s.notes,
            subtotal=s.subtotal,
            discount=s.discount,
            promotion_discount=s.promotion_discount,
            coupon_code=s.coupon_code,
            total_amount=s.total_amount,
            payment_type=s.payment_type,
            amount_paid=s.amount_paid,
            credit_amount=view.credit_amount,
            payment_method=s.payment_method,
            payment_reference=s.payment_reference,
            item_count=len(view.items),
            created_by_name=view.created_by_name,
            created_at=s.created_at,
            updated_at=s.updated_at,
            posted_at=s.posted_at,
            posted_by_name=view.posted_by_name,
            void_reason=s.void_reason,
            voided_at=s.voided_at,
            replaces_id=s.replaces_id,
            replaced_by_id=view.replaced_by_id,
            cogs_total=view.cogs_total,
            gross_profit=view.gross_profit,
            lines_without_cost=view.lines_without_cost,
            warnings=view.warnings,
            promotions=[AppliedPromotionOut.from_applied(a) for a in view.promotions],
            promotions_out_of_date=view.promotions_out_of_date,
            items=[SaleItemOut.from_view(i) for i in view.items],
        )


class SaleSummaryOut(BaseModel):
    """A sale as one row of a list (no lines)."""

    id: int
    invoice_no: str | None
    status: SaleStatus
    customer_id: int | None
    customer_name: str | None
    sale_date: date
    total_amount: Decimal
    amount_paid: Decimal | None
    payment_type: PaymentType | None
    payment_method: PaymentMethod | None
    item_count: int
    created_by_name: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: SaleRow) -> "SaleSummaryOut":
        s = row.sale
        return cls(
            id=s.id,
            invoice_no=s.invoice_no,
            status=s.status,
            customer_id=s.customer_id,
            customer_name=row.customer_name,
            sale_date=s.sale_date,
            total_amount=s.total_amount,
            amount_paid=s.amount_paid,
            payment_type=s.payment_type,
            payment_method=s.payment_method,
            item_count=row.item_count,
            created_by_name=row.created_by_name,
            created_at=s.created_at,
        )


class SaleListOut(Page):
    items: list[SaleSummaryOut]


class FieldProblem(BaseModel):
    field: str
    message: str


class PreviewLineOut(BaseModel):
    product_id: int | None
    quantity: Decimal | None
    unit_price: Decimal | None
    discount: Decimal
    gross: Decimal | None
    line_total: Decimal | None
    promotion_discount: Decimal  # this line's share of the offers
    available: Decimal | None  # stock on hand right now
    short: bool  # more is wanted than is on hand: fine for a draft, but it cannot be posted
    errors: list[FieldProblem]


class PreviewOut(BaseModel):
    """The priced cart, exactly as posting would compute it. Nothing is saved."""

    lines: list[PreviewLineOut]
    subtotal: Decimal
    discount: Decimal  # the cashier's own bill discount
    promotion_discount: Decimal  # what offers and coupons take off
    total: Decimal
    promotions: list[AppliedPromotionOut]  # the offers applied, with the reason
    not_applied: list[NotAppliedOut]  # offers that were considered but do not apply, with the reason
    coupon: CouponOut | None  # what happened to the coupon code that was entered
    payment_type: PaymentType
    paid: Decimal
    credit: Decimal
    errors: list[FieldProblem]
    warnings: list[str]

    @classmethod
    def from_preview(cls, p: Preview) -> "PreviewOut":
        return cls(
            lines=[
                PreviewLineOut(
                    **{**line.__dict__, "errors": [FieldProblem(field=f, message=m) for f, m in line.errors]}
                )
                for line in p.lines
            ],
            subtotal=p.subtotal,
            discount=p.discount,
            promotion_discount=p.promotion_discount,
            total=p.total,
            promotions=[AppliedPromotionOut.from_applied(a) for a in p.applied_promotions],
            not_applied=[
                NotAppliedOut(promotion_id=n.promotion_id, name=n.name, reason=n.reason)
                for n in p.not_applied_promotions
            ],
            coupon=None
            if p.coupon is None
            else CouponOut(code=p.coupon.code, applied=p.coupon.applied, message=p.coupon.message),
            payment_type=p.payment_type,
            paid=p.paid,
            credit=p.credit,
            errors=[FieldProblem(field=f, message=m) for f, m in p.errors],
            warnings=p.warnings,
        )
