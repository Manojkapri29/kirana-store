"""Online payments: evidence of money that moved through an outside provider, with a strict, provider-neutral lifecycle.

    CREATED -> PENDING -> AUTHORIZED -> CAPTURED -> (PARTIALLY_REFUNDED ->) REFUNDED        FAILED and CANCELLED are final

What this is NOT: a ledger. A payment for a sale (SALE_PAYMENT) is evidence that a sale already on the books was paid this way; it posts nothing.
A payment on a customer's khata (KHATA_PAYMENT) is recorded ONCE, through `khata_service`, at the moment it is captured, and the
resulting entry id is stored on the payment, so a repeated webhook, a re-check or a retry cannot record it twice. Finance reads those
entries where they already live: no second financial record exists.

Trust. The browser can never say a payment succeeded. A status changes only (a) from a signature-verified webhook, (b) from a status check
this server makes to the provider, or (c) when an authorised person attests it for a provider with no outside system (`manual`), which
is recorded as such. An out-of-order or repeated event is IGNORED; an event that contradicts the record (an amount that does not match, a
capture after a failure) is not applied and flags the payment for REVIEW instead of guessing.

Retries. Creating and refunding at a provider are UNSAFE: tried once, with an idempotency key passed on. If the outcome is unknown the
payment is flagged for review and checked with a status check (SAFE) rather than repeated.
"""

import re
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.integrations.base import SAFE, UNSAFE, ProviderError, ProviderPayment
from app.models import Customer, OnlinePayment, OnlinePaymentEvent, QuickSale, Sale
from app.models.enums import (
    OnlinePaymentMethod,
    OnlinePaymentPurpose,
    OnlinePaymentStatus,
    PaymentMethod,
    SaleStatus,
    UserRole,
)
from app.services import integration_service, khata_service, notification_service
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

S = OnlinePaymentStatus
ALLOWED: dict[S, set[S]] = {
    S.CREATED: {S.PENDING, S.AUTHORIZED, S.CAPTURED, S.FAILED, S.CANCELLED},
    S.PENDING: {S.AUTHORIZED, S.CAPTURED, S.FAILED, S.CANCELLED},
    S.AUTHORIZED: {S.CAPTURED, S.FAILED, S.CANCELLED},
    S.CAPTURED: {S.REFUNDED, S.PARTIALLY_REFUNDED},
    S.PARTIALLY_REFUNDED: {S.REFUNDED, S.PARTIALLY_REFUNDED},
    S.FAILED: set(),
    S.CANCELLED: set(),
    S.REFUNDED: set(),
}
_KHATA_METHOD = {OnlinePaymentMethod.UPI: PaymentMethod.UPI, OnlinePaymentMethod.COD: PaymentMethod.CASH}
CENT = Decimal("0.01")
NOT_CONFIGURED = "Provider Not Configured"


def _amount(value: object, field: str = "amount") -> Decimal:
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, Decimal | int | str):
        raise InvalidInputError("Enter the amount as a number, for example 250.50.", field=field)
    text = str(value)
    if isinstance(value, str) and not re.fullmatch(r"\d{1,12}(\.\d{1,2})?", text):
        raise InvalidInputError("Enter the amount as a number, for example 250.50.", field=field)
    try:
        amount = Decimal(text)
        if amount.as_tuple().exponent > 0:  # 1E+3 and the like
            raise ArithmeticError
    except ArithmeticError as exc:
        raise InvalidInputError("Enter the amount as a number, for example 250.50.", field=field) from exc
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(CENT):
        raise InvalidInputError("The amount must be more than zero with at most two decimals.", field=field)
    return amount


def get(session: Session, shop_id: int, payment_id: int, *, lock: bool = False) -> OnlinePayment:
    query = select(OnlinePayment).where(OnlinePayment.shop_id == shop_id, OnlinePayment.id == payment_id)
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise NotFoundError("Payment not found")
    return row


def list_payments(
    session: Session, shop_id: int, *, status: OnlinePaymentStatus | None = None, needs_review: bool | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[OnlinePayment], int]:  # fmt: skip
    from sqlalchemy import func

    cond = [OnlinePayment.shop_id == shop_id]
    if status is not None:
        cond.append(OnlinePayment.status == status)
    if needs_review is not None:
        cond.append(OnlinePayment.needs_review.is_(needs_review))
    total = session.scalar(select(func.count()).select_from(OnlinePayment).where(*cond)) or 0
    rows = session.scalars(
        select(OnlinePayment)
        .where(*cond)
        .order_by(OnlinePayment.id.desc())
        .limit(min(limit, 200))
        .offset(offset)
    )
    return list(rows), total


def events_of(session: Session, shop_id: int, payment_id: int) -> list[OnlinePaymentEvent]:
    get(session, shop_id, payment_id)
    return list(
        session.scalars(
            select(OnlinePaymentEvent)
            .where(OnlinePaymentEvent.shop_id == shop_id, OnlinePaymentEvent.payment_id == payment_id)
            .order_by(OnlinePaymentEvent.id)
        )
    )


def _event(
    session: Session,
    p: OnlinePayment,
    to: str,
    source: str,
    *,
    from_status: str | None,
    provider_event_id: str | None = None,
    note: str | None = None,
) -> None:
    session.add(
        OnlinePaymentEvent(
            shop_id=p.shop_id,
            payment_id=p.id,
            from_status=from_status,
            to_status=to,
            source=source,
            provider_event_id=provider_event_id,
            note=note,
        )
    )


def _review(
    session: Session, p: OnlinePayment, reason: str, source: str, provider_event_id: str | None
) -> str:
    p.needs_review, p.review_reason = True, reason[:200]
    _event(
        session,
        p,
        p.status.value,
        source,
        from_status=p.status.value,
        provider_event_id=provider_event_id,
        note=f"REVIEW: {reason}"[:200],
    )
    return "REVIEW"


# --- Creating -----------------------------------------------------------------------------------------------------


def _check_links(
    session: Session, shop_id: int, sale_id: int | None, quick_sale_id: int | None, customer_id: int | None
) -> None:
    if (
        sale_id is not None
        and session.scalar(
            select(Sale.id).where(
                Sale.shop_id == shop_id, Sale.id == sale_id, Sale.status == SaleStatus.POSTED
            )
        )
        is None
    ):
        raise NotFoundError("Sale not found")
    if (
        quick_sale_id is not None
        and session.scalar(
            select(QuickSale.id).where(
                QuickSale.shop_id == shop_id,
                QuickSale.id == quick_sale_id,
                QuickSale.status == SaleStatus.POSTED,
            )
        )
        is None
    ):
        raise NotFoundError("Quick sale not found")
    if (
        customer_id is not None
        and session.scalar(select(Customer.id).where(Customer.shop_id == shop_id, Customer.id == customer_id))
        is None
    ):
        raise NotFoundError("Customer not found")


def create(
    session: Session, ctx: RequestContext, *, method: OnlinePaymentMethod, purpose: OnlinePaymentPurpose, amount: Any, idempotency_key: str,
    sale_id: int | None = None, quick_sale_id: int | None = None, customer_id: int | None = None, order_ref: str | None = None,
    provider_txn_id: str | None = None,
) -> OnlinePayment:  # fmt: skip
    """Start a payment. Repeating the call with the same idempotency key returns the same payment and calls the provider only once."""
    value = _amount(amount)
    fingerprint = (method, purpose, value, sale_id, quick_sale_id, customer_id)
    existing = session.scalar(
        select(OnlinePayment).where(
            OnlinePayment.shop_id == ctx.shop_id, OnlinePayment.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if (
            existing.method,
            existing.purpose,
            existing.amount,
            existing.sale_id,
            existing.quick_sale_id,
            existing.customer_id,
        ) != fingerprint:
            raise ConflictError(
                "This request key was already used for a different payment.", code="idempotency_key_reused"
            )
        return existing
    if purpose is OnlinePaymentPurpose.KHATA_PAYMENT and customer_id is None:
        raise InvalidInputError("A khata payment needs the customer.", field="customer_id")
    if purpose is OnlinePaymentPurpose.SALE_PAYMENT:
        if (sale_id is None) == (quick_sale_id is None):
            raise InvalidInputError("Give the sale or the quick sale this payment is for.", field="sale_id")
        if sale_id is not None:
            total = session.scalar(
                select(Sale.total_amount).where(Sale.shop_id == ctx.shop_id, Sale.id == sale_id)
            )
            if total is not None and value > total:
                raise InvalidInputError("The payment is more than the sale total.", field="amount")
    found = integration_service.payment_provider_for(session, ctx.shop_id)
    if found is None:
        raise ConflictError(NOT_CONFIGURED, code="provider_not_configured")
    _check_links(session, ctx.shop_id, sale_id, quick_sale_id, customer_id)
    row, provider = found
    if provider.external is False and method not in (OnlinePaymentMethod.COD, OnlinePaymentMethod.UPI):
        raise InvalidInputError("Manual confirmation covers cash on delivery and UPI only.", field="method")
    payment = OnlinePayment(
        shop_id=ctx.shop_id, provider=provider.name, method=method, purpose=purpose, amount=value, sale_id=sale_id, quick_sale_id=quick_sale_id,
        customer_id=customer_id, order_ref=(order_ref or None) and order_ref[:60], idempotency_key=idempotency_key, created_by=ctx.user_id,
    )  # fmt: skip
    session.add(payment)
    session.flush()
    _event(session, payment, S.CREATED.value, "SYSTEM", from_status=None)
    try:
        result = integration_service.call(
            session, row, provider.name, "create_payment",
            lambda: provider.create(amount=value, method=method.value, reference=provider_txn_id or f"pay-{payment.id}", idempotency_key=f"{ctx.shop_id}:{idempotency_key}", timeout=15.0),
            kind=UNSAFE,
        )  # fmt: skip
    except ProviderError as error:
        if error.retryable or error.code.endswith("unknown_outcome"):
            payment.failure_code = error.code
            _review(
                session,
                payment,
                "The provider's answer is unknown: check its status before creating another payment.",
                "SYSTEM",
                None,
            )
        else:
            _move(session, payment, S.FAILED, "SYSTEM", note=error.code)
            payment.failure_code = error.code
        record_audit(
            session,
            ctx,
            entity_type="online_payment",
            entity_id=payment.id,
            action="online_payment_create_failed",
            after={"code": error.code},
        )
        return payment
    payment.provider_txn_id = result.txn_id
    _move(session, payment, S(result.status), "SYSTEM", note="created")
    record_audit(
        session,
        ctx,
        entity_type="online_payment",
        entity_id=payment.id,
        action="online_payment_created",
        after={"provider": provider.name, "amount": str(value), "purpose": purpose.value},
    )
    return payment


# --- Applying a status --------------------------------------------------------------------------------------------


def _move(
    session: Session,
    p: OnlinePayment,
    to: S,
    source: str,
    *,
    provider_event_id: str | None = None,
    note: str | None = None,
) -> None:
    before = p.status
    p.status = to
    _event(
        session, p, to.value, source, from_status=before.value, provider_event_id=provider_event_id, note=note
    )


def _apply_to_khata(session: Session, p: OnlinePayment) -> None:
    """Record a captured khata payment exactly once. A problem is contained and flagged: the money DID arrive, so the payment stays CAPTURED."""
    if (
        p.purpose is not OnlinePaymentPurpose.KHATA_PAYMENT
        or p.khata_entry_id is not None
        or p.customer_id is None
    ):
        return
    ctx = RequestContext(
        shop_id=p.shop_id, user_id=p.created_by, role=UserRole.STAFF, permissions=frozenset()
    )
    try:
        with session.begin_nested():
            result = khata_service.record_payment(
                session, ctx, p.customer_id, p.amount, payment_method=_KHATA_METHOD.get(p.method, PaymentMethod.OTHER),
                payment_reference=f"online-payment:{p.id}", note="Online payment",
            )  # fmt: skip
            p.khata_entry_id = result.entry.id
    except Exception:  # noqa: BLE001
        p.needs_review, p.review_reason = (
            True,
            "The money arrived but could not be recorded on the customer's khata: record it there by hand.",
        )


def apply_status(
    session: Session, p: OnlinePayment, seen: ProviderPayment, *, source: str, provider_event_id: str | None = None, attested: bool = False
) -> str:  # fmt: skip
    """Move a payment to what the provider (or an authorised person) says. Returns APPLIED, IGNORED or REVIEW. Never raises for a bad event."""
    new = S(seen.status)
    if new is p.status and not (
        new in (S.PARTIALLY_REFUNDED,) and seen.refunded is not None and seen.refunded != p.refunded_amount
    ):
        return "IGNORED"  # the same thing again (a redelivery)
    if new not in ALLOWED[p.status]:
        if p.status in (S.FAILED, S.CANCELLED) and new in (S.CAPTURED, S.AUTHORIZED):
            return _review(
                session,
                p,
                f"The provider reports {new.value} for a payment recorded as {p.status.value}.",
                source,
                provider_event_id,
            )
        _event(
            session,
            p,
            p.status.value,
            source,
            from_status=p.status.value,
            provider_event_id=provider_event_id,
            note=f"ignored out-of-order {new.value}",
        )
        return "IGNORED"
    if new in (S.CAPTURED, S.AUTHORIZED) and not attested:
        if seen.amount is None or seen.amount != p.amount:
            return _review(
                session,
                p,
                f"The provider's amount ({seen.amount}) does not match the payment ({p.amount}).",
                source,
                provider_event_id,
            )
    if new in (S.REFUNDED, S.PARTIALLY_REFUNDED):
        refunded = seen.refunded if seen.refunded is not None else (p.amount if new is S.REFUNDED else None)
        if refunded is None or refunded <= p.refunded_amount or refunded > p.amount:
            return _review(
                session,
                p,
                "The refunded amount reported does not fit the payment.",
                source,
                provider_event_id,
            )
        new = S.REFUNDED if refunded == p.amount else S.PARTIALLY_REFUNDED
        p.refunded_amount = refunded
        if p.khata_entry_id is not None and khata_service.entry_is_live(session, p.shop_id, p.khata_entry_id):
            p.needs_review, p.review_reason = (
                True,
                "Refunded at the provider but still recorded as paid on the customer's khata: reverse that entry in the ledger.",
            )
    _move(session, p, new, source, provider_event_id=provider_event_id)
    if new is S.CAPTURED:
        p.captured_at = utc_now()
        _apply_to_khata(session, p)
        notification_service.emit_safely(
            session, p.shop_id, "PAYMENT_RECEIVED", title="Online payment received", message="An online payment was confirmed.",
            dedupe_key=f"online_payment:{p.id}:captured", entity_type="online_payment", entity_id=p.id,
        )  # fmt: skip
    if new is S.FAILED:
        p.failure_code = seen.failure_code or p.failure_code
    session.flush()
    return "APPLIED"


# --- Acting on a payment ------------------------------------------------------------------------------------------


def verify(session: Session, ctx: RequestContext, payment_id: int) -> OnlinePayment:
    """Ask the provider for the payment's state (a safe check, repeated if it fails transiently) and apply the answer."""
    p = get(session, ctx.shop_id, payment_id, lock=True)
    found = integration_service.payment_provider_for(session, ctx.shop_id)
    if found is None:
        raise ConflictError(NOT_CONFIGURED, code="provider_not_configured")
    row, provider = found
    if not p.provider_txn_id or p.provider != provider.name:
        raise ConflictError("This payment has no transaction at the current provider to check.")
    try:
        seen = integration_service.call(
            session,
            row,
            provider.name,
            "fetch_status",
            lambda: provider.fetch_status(p.provider_txn_id or "", timeout=15.0),
            kind=SAFE,
        )
    except ProviderError as error:
        if error.code == "status_check_not_supported":
            raise ConflictError(
                "This provider cannot be asked for a payment's status: it reports changes by webhook only.",
                code="status_check_not_supported",
            ) from error
        raise ConflictError(
            "The provider could not be reached. Try again shortly.", code="provider_unavailable"
        ) from error
    apply_status(session, p, seen, source="VERIFY")
    return p


def confirm(session: Session, ctx: RequestContext, payment_id: int, note: str | None = None) -> OnlinePayment:
    """A person attests that the money arrived, for a provider with no outside system (`manual`). Never for an external provider."""
    p = get(session, ctx.shop_id, payment_id, lock=True)
    found = integration_service.payment_provider_for(session, ctx.shop_id)
    if found is None or found[1].name != p.provider:
        raise ConflictError(NOT_CONFIGURED, code="provider_not_configured")
    if found[1].external:
        raise ConflictError(
            "Only the provider can confirm this payment: it arrives by webhook.", code="external_provider"
        )
    outcome = apply_status(
        session,
        p,
        ProviderPayment(p.provider_txn_id or "", S.CAPTURED.value, p.amount),
        source="MANUAL",
        attested=True,
    )
    if outcome != "APPLIED":
        raise ConflictError(f"A payment that is {p.status.value.lower()} cannot be confirmed.")
    record_audit(
        session,
        ctx,
        entity_type="online_payment",
        entity_id=p.id,
        action="online_payment_confirmed_manually",
        after={"note": (note or "")[:200]},
    )
    return p


def cancel(session: Session, ctx: RequestContext, payment_id: int) -> OnlinePayment:
    p = get(session, ctx.shop_id, payment_id, lock=True)
    found = integration_service.payment_provider_for(session, ctx.shop_id)
    if found is not None and found[1].external and found[1].name == p.provider:
        raise ConflictError(
            "Cancel it at the provider: the change arrives by webhook.", code="external_provider"
        )
    if S.CANCELLED not in ALLOWED[p.status]:
        raise ConflictError(f"A payment that is {p.status.value.lower()} cannot be cancelled.")
    _move(session, p, S.CANCELLED, "MANUAL")
    record_audit(
        session, ctx, entity_type="online_payment", entity_id=p.id, action="online_payment_cancelled"
    )
    return p


def refund(
    session: Session, ctx: RequestContext, payment_id: int, amount: Any, idempotency_key: str
) -> OnlinePayment:
    """Refund part or all of a captured payment, once per idempotency key. The customer's khata is never changed here."""
    value = _amount(amount)
    p = get(session, ctx.shop_id, payment_id, lock=True)
    token = f"refund:{idempotency_key}"
    if session.scalar(
        select(OnlinePaymentEvent.id).where(
            OnlinePaymentEvent.shop_id == ctx.shop_id,
            OnlinePaymentEvent.payment_id == p.id,
            OnlinePaymentEvent.provider_event_id == token,
        )
    ):
        return p  # this refund was already made: repeating the request changes nothing
    if p.status not in (S.CAPTURED, S.PARTIALLY_REFUNDED):
        raise ConflictError("Only a captured payment can be refunded.")
    if value > p.amount - p.refunded_amount:
        raise InvalidInputError("The refund is more than what is left of the payment.", field="amount")
    if p.khata_entry_id is not None and khata_service.entry_is_live(session, ctx.shop_id, p.khata_entry_id):
        raise ConflictError(
            "This payment was recorded on the customer's khata: reverse that entry in the ledger first, then refund.",
            code="khata_entry_live",
        )
    found = integration_service.payment_provider_for(session, ctx.shop_id)
    if found is None or found[1].name != p.provider:
        raise ConflictError(NOT_CONFIGURED, code="provider_not_configured")
    row, provider = found
    try:
        result = integration_service.call(
            session,
            row,
            provider.name,
            "refund",
            lambda: provider.refund(
                p.provider_txn_id or "",
                amount=value,
                idempotency_key=f"{ctx.shop_id}:{p.id}:{idempotency_key}",
                timeout=15.0,
            ),
            kind=UNSAFE,
        )
    except ProviderError as error:
        if error.code == "refund_at_provider":
            raise ConflictError(
                "This provider refunds are made at the provider; the result arrives by webhook.",
                code="refund_at_provider",
            ) from error
        _review(
            session,
            p,
            "A refund was attempted and its outcome is unknown: check with the provider before trying again.",
            "REFUND",
            token,
        )
        return p
    p.refunded_amount = p.refunded_amount + value
    _move(
        session,
        p,
        S.REFUNDED if p.refunded_amount == p.amount else S.PARTIALLY_REFUNDED,
        "REFUND",
        provider_event_id=token,
        note=result.status,
    )
    record_audit(
        session,
        ctx,
        entity_type="online_payment",
        entity_id=p.id,
        action="online_payment_refunded",
        after={"amount": str(value)},
    )
    return p
