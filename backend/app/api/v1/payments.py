"""Online payment records. A payment's status is never accepted from the browser: it comes from a verified webhook, a status check the
server makes to the provider, or (for a provider with no outside system) a person's recorded attestation."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.api.idempotency import KEY_PATTERN
from app.db.session import get_session, write_transaction
from app.models.enums import OnlinePaymentStatus
from app.schemas.integrations import (
    ConfirmIn,
    PaymentEventOut,
    PaymentIn,
    PaymentListOut,
    PaymentOut,
    RefundIn,
)
from app.services import online_payment_service
from app.services.errors import InvalidInputError

router = APIRouter(prefix="/payments", tags=["payments"])
ReadSession = Annotated[Session, Depends(get_session)]
Key = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key", description="Required: a random value per attempt; repeats of it are safe."
    ),
]


def _key(value: str | None) -> str:
    if value is None or not KEY_PATTERN.match(value):
        raise InvalidInputError(
            "Send an Idempotency-Key header of 8 to 100 letters, digits or . _ : -", field="Idempotency-Key"
        )
    return value


@router.post("", response_model=PaymentOut, status_code=201)
def create_payment(payload: PaymentIn, ctx: Ctx, idempotency_key: Key = None) -> PaymentOut:
    key = _key(idempotency_key)
    with write_transaction() as session:
        payment = online_payment_service.create(
            session, ctx, method=payload.method, purpose=payload.purpose, amount=payload.amount, idempotency_key=key, sale_id=payload.sale_id,
            quick_sale_id=payload.quick_sale_id, customer_id=payload.customer_id, order_ref=payload.order_ref, provider_txn_id=payload.provider_txn_id,
        )  # fmt: skip
        return PaymentOut.of(payment)


@router.get("", response_model=PaymentListOut)
def list_payments(
    ctx: Ctx, session: ReadSession, status: OnlinePaymentStatus | None = None, needs_review: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50, offset: Annotated[int, Query(ge=0)] = 0,
) -> PaymentListOut:  # fmt: skip
    rows, total = online_payment_service.list_payments(
        session, ctx.shop_id, status=status, needs_review=needs_review, limit=limit, offset=offset
    )
    return PaymentListOut(items=[PaymentOut.of(p) for p in rows], total=total, limit=limit, offset=offset)


@router.get("/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: int, ctx: Ctx, session: ReadSession) -> PaymentOut:
    return PaymentOut.of(online_payment_service.get(session, ctx.shop_id, payment_id))


@router.get("/{payment_id}/events", response_model=list[PaymentEventOut])
def payment_events(payment_id: int, ctx: Ctx, session: ReadSession) -> list[PaymentEventOut]:
    return [PaymentEventOut.of(e) for e in online_payment_service.events_of(session, ctx.shop_id, payment_id)]


@router.post("/{payment_id}/verify", response_model=PaymentOut)
def verify(payment_id: int, ctx: Ctx) -> PaymentOut:
    with write_transaction() as session:
        return PaymentOut.of(online_payment_service.verify(session, ctx, payment_id))


@router.post("/{payment_id}/confirm", response_model=PaymentOut)
def confirm(payment_id: int, payload: ConfirmIn, ctx: Ctx) -> PaymentOut:
    with write_transaction() as session:
        return PaymentOut.of(online_payment_service.confirm(session, ctx, payment_id, payload.note))


@router.post("/{payment_id}/cancel", response_model=PaymentOut)
def cancel(payment_id: int, ctx: Ctx) -> PaymentOut:
    with write_transaction() as session:
        return PaymentOut.of(online_payment_service.cancel(session, ctx, payment_id))


@router.post("/{payment_id}/refund", response_model=PaymentOut)
def refund(payment_id: int, payload: RefundIn, ctx: Ctx, idempotency_key: Key = None) -> PaymentOut:
    key = _key(idempotency_key)
    with write_transaction() as session:
        return PaymentOut.of(online_payment_service.refund(session, ctx, payment_id, payload.amount, key))
