"""File downloads (CSV / XLSX). Owner only; always limited to the caller's shop."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import OwnerCtx, feature_flag, meter_export, rate_limited
from app.api.v1.products import StatusFilter, active_flag
from app.db.session import get_session
from app.models.enums import (
    CustomerLedgerEntryType,
    DocumentStatus,
    InventoryTxnType,
    PaymentType,
    PromotionStatus,
    PromotionType,
    PurchaseStatus,
    SaleStatus,
)
from app.schemas.customer import BalanceFilter
from app.services import export_datasets
from app.services.export_service import ExportFile, ExportFormat
from app.services.inventory_service import StockStatus
from app.services.khata_service import BalanceStatus

router = APIRouter(
    prefix="/exports",
    tags=["exports"],
    dependencies=[Depends(feature_flag("exports")), Depends(rate_limited("export")), Depends(meter_export)],
)
ReadSession = Annotated[Session, Depends(get_session)]
Format = Annotated[ExportFormat, Query(alias="format", description="csv or xlsx")]


def download(file: ExportFile) -> Response:
    return Response(
        content=file.content,
        media_type=file.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "Cache-Control": "no-store",  # business data: never cached
        },
    )


@router.get("/products")
def export_products(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category_id: int | None = None,
    status: StatusFilter = StatusFilter.ACTIVE,
) -> Response:
    return download(
        export_datasets.export_products(
            session, ctx, fmt, active=active_flag(status), q=q, category_id=category_id
        )
    )


@router.get("/inventory")
def export_inventory(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category_id: int | None = None,
    status: StatusFilter = StatusFilter.ACTIVE,
    stock_status: StockStatus | None = None,
) -> Response:
    return download(
        export_datasets.export_inventory(
            session,
            ctx,
            fmt,
            q=q,
            category_id=category_id,
            active=active_flag(status),
            status=stock_status,
        )
    )


@router.get("/inventory-history")
def export_inventory_history(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    product_id: int | None = None,
    txn_type: InventoryTxnType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    return download(
        export_datasets.export_inventory_history(
            session,
            ctx,
            fmt,
            product_id=product_id,
            txn_type=txn_type,
            date_from=date_from,
            date_to=date_to,
        )
    )


PurchaseStatuses = Annotated[
    list[PurchaseStatus] | None, Query(alias="status", description="Repeat to allow several")
]


@router.get("/purchases")
def export_purchases(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    q: Annotated[str | None, Query(max_length=100)] = None,
    supplier_id: int | None = None,
    statuses: PurchaseStatuses = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    return download(
        export_datasets.export_purchases(
            session,
            ctx,
            fmt,
            q=q,
            supplier_id=supplier_id,
            statuses=statuses,
            date_from=date_from,
            date_to=date_to,
        )
    )


@router.get("/purchase-items")
def export_purchase_items(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    q: Annotated[str | None, Query(max_length=100)] = None,
    supplier_id: int | None = None,
    statuses: PurchaseStatuses = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    return download(
        export_datasets.export_purchase_items(
            session,
            ctx,
            fmt,
            q=q,
            supplier_id=supplier_id,
            statuses=statuses,
            date_from=date_from,
            date_to=date_to,
        )
    )


@router.get("/purchases/{purchase_id}")
def export_purchase_details(
    purchase_id: int, ctx: OwnerCtx, session: ReadSession, fmt: Format = ExportFormat.CSV
) -> Response:
    """One purchase with all its lines."""
    return download(export_datasets.export_purchase_details(session, ctx, fmt, purchase_id))


_BALANCE = {
    BalanceFilter.ANY: None,
    BalanceFilter.OUTSTANDING: BalanceStatus.OUTSTANDING,
    BalanceFilter.SETTLED: BalanceStatus.SETTLED,
    BalanceFilter.ADVANCE: BalanceStatus.ADVANCE,
}


@router.get("/suppliers")
def export_suppliers(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: StatusFilter = StatusFilter.ACTIVE,
) -> Response:
    """Suppliers and how many products each supplies."""
    return download(export_datasets.export_suppliers(session, ctx, fmt, active=active_flag(status), q=q))


@router.get("/customers")
def export_customers(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: StatusFilter = StatusFilter.ACTIVE,
    balance: BalanceFilter = BalanceFilter.ANY,
) -> Response:
    """Customers with what each owes (or has paid ahead)."""
    return download(
        export_datasets.export_customers(
            session, ctx, fmt, active=active_flag(status), q=q, balance=_BALANCE[balance]
        )
    )


@router.get("/customers/{customer_id}/ledger")
def export_customer_ledger(
    customer_id: int,
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    entry_type: CustomerLedgerEntryType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    """One customer's khata, oldest first, with the running balance."""
    return download(
        export_datasets.export_customer_ledger(
            session, ctx, fmt, customer_id, entry_type=entry_type, date_from=date_from, date_to=date_to
        )
    )


SaleStatuses = Annotated[
    list[SaleStatus] | None, Query(alias="status", description="Repeat to allow several")
]


@router.get("/sales")
def export_sales(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    q: Annotated[str | None, Query(max_length=100)] = None,
    customer_id: int | None = None,
    statuses: SaleStatuses = None,
    payment_type: PaymentType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    """One row per sale, with payment, cost of goods and gross profit (empty where cost is unknown)."""
    return download(
        export_datasets.export_sales(
            session,
            ctx,
            fmt,
            q=q,
            customer_id=customer_id,
            statuses=statuses,
            payment_type=payment_type,
            date_from=date_from,
            date_to=date_to,
        )
    )


@router.get("/sale-items")
def export_sale_items(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    q: Annotated[str | None, Query(max_length=100)] = None,
    customer_id: int | None = None,
    statuses: SaleStatuses = None,
    payment_type: PaymentType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    """One row per sold line, with the cost snapshot and line profit."""
    return download(
        export_datasets.export_sale_items(
            session,
            ctx,
            fmt,
            q=q,
            customer_id=customer_id,
            statuses=statuses,
            payment_type=payment_type,
            date_from=date_from,
            date_to=date_to,
        )
    )


@router.get("/sales/{sale_id}")
def export_sale_details(
    sale_id: int, ctx: OwnerCtx, session: ReadSession, fmt: Format = ExportFormat.CSV
) -> Response:
    """One sale with all its lines."""
    return download(export_datasets.export_sale_details(session, ctx, fmt, sale_id))


@router.get("/quick-sales")
def export_quick_sales(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    q: Annotated[str | None, Query(max_length=100)] = None,
    customer_id: int | None = None,
    statuses: SaleStatuses = None,
    payment_type: PaymentType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    """One row per quick sale. The profit column always says "Not Available"."""
    return download(
        export_datasets.export_quick_sales(
            session,
            ctx,
            fmt,
            q=q,
            customer_id=customer_id,
            statuses=statuses,
            payment_type=payment_type,
            date_from=date_from,
            date_to=date_to,
        )
    )


@router.get("/promotions")
def export_promotions(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[list[PromotionStatus] | None, Query()] = None,
    promo_type: PromotionType | None = None,
    coupon_only: bool | None = None,
) -> Response:
    """One row per offer, with how many sales used it and what it gave away."""
    return download(
        export_datasets.export_promotions(
            session, ctx, fmt, q=q, statuses=status, promo_type=promo_type, coupon_only=coupon_only
        )
    )


@router.get("/promotion-usage")
def export_promotion_usage(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    promotion_id: int | None = None,
    coupon_only: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    """One row per use of an offer on a sale. `coupon_only=true` gives the coupon usage report."""
    return download(
        export_datasets.export_promotion_usage(
            session,
            ctx,
            fmt,
            promotion_id=promotion_id,
            coupon_only=coupon_only,
            date_from=date_from,
            date_to=date_to,
        )
    )


@router.get("/price-history")
def export_price_history(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    barcode: Annotated[str | None, Query(max_length=50)] = None,
    provider: Annotated[str | None, Query(max_length=30)] = None,
) -> Response:
    """One row per outside price saved by a price check (needs the plan's price intelligence)."""
    return download(
        export_datasets.export_price_history(session, ctx, fmt, barcode=barcode, provider=provider)
    )


@router.get("/sales-summary")
def export_sales_summary(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    """One row per day with Detailed, Quick and Combined gross, discount and net sales."""
    return download(
        export_datasets.export_sales_summary(session, ctx, fmt, date_from=date_from, date_to=date_to)
    )


@router.get("/discount-report")
def export_discount_report(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    """Discount given by offer and coupon over a period (needs the plan's advanced reports)."""
    return download(
        export_datasets.export_discount_report(session, ctx, fmt, date_from=date_from, date_to=date_to)
    )


@router.get("/sales-returns")
def export_sales_returns(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[list[DocumentStatus] | None, Query()] = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    """One row per sales return."""
    return download(
        export_datasets.export_sales_returns(
            session, ctx, fmt, q=q, statuses=status, date_from=date_from, date_to=date_to
        )
    )


@router.get("/purchase-returns")
def export_purchase_returns(
    ctx: OwnerCtx,
    session: ReadSession,
    fmt: Format = ExportFormat.CSV,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[list[DocumentStatus] | None, Query()] = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    """One row per purchase return."""
    return download(
        export_datasets.export_purchase_returns(
            session, ctx, fmt, q=q, statuses=status, date_from=date_from, date_to=date_to
        )
    )
