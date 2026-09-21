"""File downloads (CSV / XLSX). Owner only; always limited to the caller's shop."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import OwnerCtx
from app.api.v1.products import StatusFilter, active_flag
from app.db.session import get_session
from app.models.enums import (
    CustomerLedgerEntryType,
    InventoryTxnType,
    PaymentType,
    PurchaseStatus,
    SaleStatus,
)
from app.schemas.customer import BalanceFilter
from app.services import export_datasets
from app.services.export_service import ExportFile, ExportFormat
from app.services.inventory_service import StockStatus
from app.services.khata_service import BalanceStatus

router = APIRouter(prefix="/exports", tags=["exports"])
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
