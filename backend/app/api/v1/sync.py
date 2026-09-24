"""Offline sync. Operations queued on a device are applied here, in order, through the SAME services the normal screens use.

Each operation runs in its own transaction: the business change and its sync record commit together or not at all, so an operation is applied
exactly once however many times it is sent. A refusal (no stock, a changed total, a closed period) is stored as CONFLICT with the reason and is
never adjusted to fit; an invalid one is FAILED; an unexpected server error is answered RETRY and stores nothing (it is safe to send again).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.core import observability
from app.core.context import RequestContext
from app.db.session import get_session, write_transaction
from app.models.enums import SyncStatus
from app.schemas.customer import PaymentIn
from app.schemas.quick_sale import QuickSaleCreate
from app.schemas.sale import SaleCreate, SalePostIn
from app.schemas.sync import SyncBatchIn, SyncOperationIn
from app.services import authorization_service, khata_service, quick_sale_service, sale_service, sync_service
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, DomainError, ForbiddenError, InvalidInputError

router = APIRouter(prefix="/sync", tags=["offline sync"])
ReadSession = Annotated[Session, Depends(get_session)]
NEEDS = {
    "QUICK_SALE": ("QUICK_SALE_CREATE", "SALE_POST"),
    "SALE": ("SALE_CREATE", "SALE_POST"),
    "CUSTOMER_PAYMENT": ("KHATA_PAYMENT",),
}


def _split(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key) or {}
    if not isinstance(value, dict):
        raise InvalidInputError(f"'{key}' must be an object.", field=key)
    return value


def _validated(model, data: dict[str, Any], where: str):  # noqa: ANN001, ANN202
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise InvalidInputError(f"{where}.{'.'.join(str(p) for p in first['loc'])}: {first['msg']}", field=where) from None


def _expected_total(payload: dict[str, Any]) -> str | None:
    value = payload.get("expected_total")
    return None if value is None else str(value)


def _apply(session: Session, ctx: RequestContext, op_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run one operation through the existing services. Raises a DomainError for anything that means 'not applied'."""
    if op_type == "QUICK_SALE":
        create = _validated(QuickSaleCreate, _split(payload, "create"), "create")
        pay = _validated(SalePostIn, _split(payload, "payment"), "payment")
        entry = quick_sale_service.create_quick_sale(session, ctx, create.model_dump())
        sale_id = entry.sale.id
        expected = _expected_total(payload)
        if expected is not None and str(entry.sale.total_amount) != expected:
            raise ConflictError("The total is no longer what was quoted.", code="total_changed", data={"expected_total": expected, "server_total": str(entry.sale.total_amount)})
        posted = quick_sale_service.post_quick_sale(session, ctx, sale_id, amount_paid=pay.amount_paid, payment_method=pay.payment_method, payment_reference=pay.payment_reference)
        return {"quick_sale_id": sale_id, "number": posted.sale.quick_no, "total": str(posted.sale.total_amount)}
    if op_type == "SALE":
        create = _validated(SaleCreate, _split(payload, "create"), "create")
        pay = _validated(SalePostIn, _split(payload, "payment"), "payment")
        header = create.model_dump(exclude={"items"})
        view = sale_service.create_sale(session, ctx, header, [i.model_dump() for i in create.items])
        expected = _expected_total(payload)
        if expected is not None and str(view.sale.total_amount) != expected:
            raise ConflictError("The total is no longer what the customer was quoted.", code="total_changed", data={"expected_total": expected, "server_total": str(view.sale.total_amount)})
        posted = sale_service.post_sale(session, ctx, view.sale.id, amount_paid=pay.amount_paid, payment_method=pay.payment_method, payment_reference=pay.payment_reference)
        return {"sale_id": view.sale.id, "invoice_no": posted.sale.invoice_no, "total": str(posted.sale.total_amount)}
    if op_type == "CUSTOMER_PAYMENT":
        data = dict(payload)
        customer_id = data.pop("customer_id", None)
        if not isinstance(customer_id, int) or isinstance(customer_id, bool):
            raise InvalidInputError("Give the customer's id.", field="customer_id")
        pay = _validated(PaymentIn, data, "payment")
        result = khata_service.record_payment(
            session, ctx, customer_id, pay.amount, entry_date=pay.entry_date, payment_method=pay.payment_method,
            payment_reference=pay.payment_reference, note=pay.note,
        )  # fmt: skip
        return {"entry_id": result.entry.id, "customer_id": customer_id}
    raise InvalidInputError(f"Unknown operation type '{op_type}'.", field="type")


def _run_one(ctx: RequestContext, device_id: str | None, op: SyncOperationIn) -> dict[str, Any]:
    if op.type not in sync_service.OP_TYPES:
        raise InvalidInputError(f"Type must be one of: {', '.join(sync_service.OP_TYPES)}.", field="type")
    if not sync_service.valid_op_id(op.client_op_id):
        raise InvalidInputError("The operation id must be 8 to 64 letters, digits or - _ . :", field="client_op_id")
    authorization_service.require(ctx, *NEEDS[op.type])
    # 1. Already seen? Answer from the record and do nothing else. (A retried CONFLICT is re-run only through /retry.)
    with write_transaction() as session:
        existing = sync_service.find(session, ctx.shop_id, op.client_op_id)
        if existing is not None:
            return sync_service.describe(existing, duplicate=True)
    # 2. Apply it. The business change and the SYNCED record commit together.
    try:
        with write_transaction() as session:
            result = _apply(session, ctx, op.type, op.payload)
            row = sync_service.record(session, ctx, client_op_id=op.client_op_id, device_id=device_id, op_type=op.type, payload=op.payload, status=SyncStatus.SYNCED, result=result, client_created_at=op.created_at)
            record_audit(session, ctx, entity_type="sync_operation", entity_id=row.id, action="offline_operation_synced", after={"type": op.type, "device": device_id, "client_created_at": str(op.created_at) if op.created_at else None})
            return sync_service.describe(row)
    except DomainError as error:
        status = SyncStatus.CONFLICT if isinstance(error, ConflictError) else SyncStatus.FAILED
        with write_transaction() as session:  # the business transaction rolled back; keep the refusal so nobody loses it
            row = sync_service.record(
                session, ctx, client_op_id=op.client_op_id, device_id=device_id, op_type=op.type, payload=op.payload, status=status,
                result=error.data if isinstance(error, ConflictError) and error.data else None, error_code=error.code or error.__class__.__name__, message=error.message, client_created_at=op.created_at,
            )  # fmt: skip
            return sync_service.describe(row)
    except Exception:  # noqa: BLE001
        observability.log_event("sync", "an offline operation could not be applied", level=40)
        return {"client_op_id": op.client_op_id, "type": op.type, "status": "RETRY", "result": None, "error_code": "server_error", "message": "The server could not apply this right now. It is safe to send it again.", "duplicate": False}


@router.post("/operations")
def sync_operations(payload: SyncBatchIn, ctx: Ctx) -> dict:
    """Apply queued operations in order. One operation's problem never blocks the next. A repeated id returns its stored answer."""
    results = []
    for op in payload.operations:
        try:
            results.append(_run_one(ctx, payload.device_id, op))
        except ForbiddenError as error:
            results.append({"client_op_id": op.client_op_id, "type": op.type, "status": "FAILED", "result": None, "error_code": "permission_denied", "message": error.message, "duplicate": False})
        except InvalidInputError as error:
            results.append({"client_op_id": op.client_op_id, "type": op.type, "status": "FAILED", "result": None, "error_code": "invalid", "message": error.message, "duplicate": False})
    return {"results": results}


@router.get("/operations")
def list_operations(ctx: Ctx, session: ReadSession, status: SyncStatus | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> dict:
    statuses = (status,) if status else (SyncStatus.CONFLICT, SyncStatus.FAILED)
    rows = sync_service.list_open(session, ctx.shop_id, statuses=statuses, limit=limit)
    return {"items": [{**sync_service.describe(r), "payload": r.payload} for r in rows]}


@router.post("/operations/{client_op_id}/retry")
def retry_operation(client_op_id: str, ctx: Ctx) -> dict:
    """Try a CONFLICT again with exactly the payload it had (stock may have arrived, the period may have been reopened). Never a different payload."""
    with write_transaction() as session:
        row = sync_service.get(session, ctx.shop_id, client_op_id)
        if row.status is not SyncStatus.CONFLICT:
            raise ConflictError("Only a conflict can be retried.")
        op_type, payload, device_id, created = row.op_type, dict(row.payload), row.device_id, row.client_created_at
        authorization_service.require(ctx, *NEEDS[op_type])
    try:
        with write_transaction() as session:
            result = _apply(session, ctx, op_type, payload)
            row = sync_service.record(session, ctx, client_op_id=client_op_id, device_id=device_id, op_type=op_type, payload=payload, status=SyncStatus.SYNCED, result=result, client_created_at=created)
            record_audit(session, ctx, entity_type="sync_operation", entity_id=row.id, action="offline_operation_synced_on_retry", after={"type": op_type})
            return sync_service.describe(row)
    except DomainError as error:
        with write_transaction() as session:
            row = sync_service.record(
                session, ctx, client_op_id=client_op_id, device_id=device_id, op_type=op_type, payload=payload,
                status=SyncStatus.CONFLICT if isinstance(error, ConflictError) else SyncStatus.FAILED,
                result=error.data if isinstance(error, ConflictError) and error.data else None, error_code=error.code or error.__class__.__name__, message=error.message, client_created_at=created,
            )  # fmt: skip
            return sync_service.describe(row)


@router.post("/operations/{client_op_id}/discard")
def discard_operation(client_op_id: str, ctx: Ctx) -> dict:
    with write_transaction() as session:
        row = sync_service.get(session, ctx.shop_id, client_op_id)
        authorization_service.require(ctx, *NEEDS[row.op_type])
        row = sync_service.discard(session, ctx, client_op_id)
        record_audit(session, ctx, entity_type="sync_operation", entity_id=row.id, action="offline_operation_discarded", after={"type": row.op_type})
        return sync_service.describe(row)


@router.get("/snapshot/products")
def snapshot_products(ctx: Ctx, session: ReadSession) -> dict:
    return sync_service.product_snapshot(session, ctx.shop_id)


@router.get("/snapshot/customers")
def snapshot_customers(ctx: Ctx, session: ReadSession) -> dict:
    return sync_service.customer_snapshot(session, ctx.shop_id)

