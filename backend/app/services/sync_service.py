"""Offline synchronisation bookkeeping and read snapshots.

A device that had no connection queues operations (a sale, a quick sale, a customer payment) with a client-generated id. When it reconnects it
sends them, in order, to `POST /sync/operations`. This module owns the record of each one; the business work itself is done by the EXISTING
services (`sale_service`, `quick_sale_service`, `khata_service`) called from the API layer in the same transaction, so there is no second
inventory, sales or khata engine here.

    SYNCED     applied, exactly once. The row commits WITH the business change; a repeat of the same id returns this answer and does nothing.
    CONFLICT   the server refused to apply it as queued (not enough stock, the total is no longer what the customer was quoted, a closed
               financial period...). Nothing was applied and nothing was altered: a person decides (retry later, or discard).
    FAILED     invalid or not permitted. Nothing was applied.
    DISCARDED  a person dropped a conflict or a failure.

Financial conflicts are never guessed at: the server does not pick a different price, quantity or date to make a queued sale fit.
Snapshots give a device something to work from while offline; they carry NO balances and no cost or profit figures, and they are labelled with the
server time they were taken, so the screen can say "last synced".
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import Customer, Product, SyncOperation
from app.models.enums import SyncStatus
from app.services import inventory_service
from app.services.errors import ConflictError, NotFoundError

OP_TYPES = ("QUICK_SALE", "SALE", "CUSTOMER_PAYMENT")
MAX_BATCH = 50
MAX_SNAPSHOT_ROWS = 5000
SAFE_ID = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:")


def valid_op_id(value: str) -> bool:
    return 8 <= len(value) <= 64 and set(value) <= SAFE_ID


def find(session: Session, shop_id: int, client_op_id: str, *, lock: bool = False) -> SyncOperation | None:
    query = select(SyncOperation).where(SyncOperation.shop_id == shop_id, SyncOperation.client_op_id == client_op_id)
    return session.scalar(query.with_for_update() if lock else query)


def record(
    session: Session, ctx: RequestContext, *, client_op_id: str, device_id: str | None, op_type: str, payload: dict[str, Any], status: SyncStatus,
    result: dict[str, Any] | None = None, error_code: str | None = None, message: str | None = None, client_created_at: datetime | None = None,
) -> SyncOperation:  # fmt: skip
    """Write (or, for a retried conflict, update) the row of one operation."""
    row = find(session, ctx.shop_id, client_op_id, lock=True)
    if row is None:
        row = SyncOperation(shop_id=ctx.shop_id, client_op_id=client_op_id, user_id=ctx.user_id, op_type=op_type, payload=payload, status=status)
        session.add(row)
    else:
        row.attempts += 1
    row.device_id = (device_id or None) and device_id[:64]
    row.status, row.result, row.error_code = status, result, (error_code or None) and error_code[:60]
    row.message = (message or None) and message[:300]
    row.client_created_at = client_created_at
    if status in (SyncStatus.SYNCED, SyncStatus.DISCARDED):
        row.resolved_at = utc_now()
    session.flush()
    return row


def describe(row: SyncOperation, *, duplicate: bool = False) -> dict[str, Any]:
    return {
        "client_op_id": row.client_op_id, "type": row.op_type, "status": row.status.value, "result": row.result, "error_code": row.error_code,
        "message": row.message, "duplicate": duplicate, "attempts": row.attempts, "received_at": row.created_at,
    }  # fmt: skip


def list_open(session: Session, shop_id: int, *, statuses: tuple[SyncStatus, ...] = (SyncStatus.CONFLICT, SyncStatus.FAILED), limit: int = 100) -> list[SyncOperation]:
    return list(session.scalars(select(SyncOperation).where(SyncOperation.shop_id == shop_id, SyncOperation.status.in_(statuses)).order_by(SyncOperation.id.desc()).limit(min(limit, 500))))


def get(session: Session, shop_id: int, client_op_id: str) -> SyncOperation:
    row = find(session, shop_id, client_op_id)
    if row is None:
        raise NotFoundError("Operation not found")
    return row


def discard(session: Session, ctx: RequestContext, client_op_id: str) -> SyncOperation:
    row = find(session, ctx.shop_id, client_op_id, lock=True)
    if row is None:
        raise NotFoundError("Operation not found")
    if row.status not in (SyncStatus.CONFLICT, SyncStatus.FAILED):
        raise ConflictError("Only a conflict or a failed operation can be discarded: a synced one has already happened.")
    row.status, row.resolved_at = SyncStatus.DISCARDED, utc_now()
    return row


# --- Snapshots ----------------------------------------------------------------------------------------------------


def product_snapshot(session: Session, shop_id: int) -> dict[str, Any]:
    """Active products with their selling price and the stock the ledger shows NOW. The device labels it "as of" the returned time: it is
    not live, and a sale made offline is still checked against the real stock when it syncs."""
    rows, total = inventory_service.list_inventory(session, shop_id, active=True, limit=MAX_SNAPSHOT_ROWS)
    prices = {pid: price for pid, price in session.execute(select(Product.id, Product.selling_price).where(Product.shop_id == shop_id, Product.is_active.is_(True)))}
    items = [
        {
            "id": r.product_id, "name": r.name, "sku": r.sku, "barcode": r.barcode, "brand": r.brand, "category": r.category_name,
            "unit": r.unit_code, "allows_decimal": r.allows_decimal, "selling_price": _money(prices.get(r.product_id)), "stock": _qty(r.current_stock),
        }
        for r in rows
    ]  # fmt: skip
    return {"as_of": utc_now(), "items": items, "total": total, "truncated": total > len(items)}


def customer_snapshot(session: Session, shop_id: int) -> dict[str, Any]:
    """Customer names and phone numbers for lookup: no balance, no ledger, no notes."""
    rows = session.execute(select(Customer.id, Customer.name, Customer.phone).where(Customer.shop_id == shop_id, Customer.is_active.is_(True)).order_by(Customer.name).limit(MAX_SNAPSHOT_ROWS + 1)).all()
    return {"as_of": utc_now(), "items": [{"id": i, "name": n, "phone": p} for i, n, p in rows[:MAX_SNAPSHOT_ROWS]], "truncated": len(rows) > MAX_SNAPSHOT_ROWS}


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _qty(value: Decimal) -> str:
    return format(value, "f")

