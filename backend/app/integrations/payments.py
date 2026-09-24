"""Payment provider adapters that need no vendor.

* `manual`: cash on delivery, or money a person confirms received (a UPI screenshot, cash at the door). No outside system is
  involved, so nothing can verify it: only an authorised person can move it forward, and that is recorded as a MANUAL attestation.
* `generic_webhook`: for a gateway (or a small relay) that can POST the signed format below. The payment is created at the gateway (a
  payment link, a QR) and LINKED here by its transaction id; every later state comes ONLY from a verified webhook. Status checks and
  refunds are not possible from here, so they are refused honestly (a refund is made at the gateway and arrives as a webhook).

Signed format. Headers: `X-Kirana-Timestamp` (unix seconds) and `X-Kirana-Signature` = hex HMAC-SHA256 of `"<timestamp>." + <raw body>`
with the shared secret. The timestamp must be within five minutes. Body JSON: `event_id`, `type` (`payment.status`), `txn_id`, `status`
(a payment status), `amount`, optional `refunded` and `failure_code`. Amounts are decimal rupees as text with at most two places.
"""

import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal, InvalidOperation

from app.integrations.base import PaymentProvider, ProviderError, ProviderPayment, WebhookNotice
from app.models.enums import OnlinePaymentStatus

TOLERANCE_SECONDS = 300
_STATUSES = {s.value for s in OnlinePaymentStatus}


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def _money(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, float | bool):
        raise ProviderError("invalid_payload", retryable=False)
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ProviderError("invalid_payload", retryable=False) from exc
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
        raise ProviderError("invalid_payload", retryable=False)
    return amount


def verify_and_parse(
    headers: dict[str, str], body: bytes, secret: str, *, now: float | None = None
) -> WebhookNotice:
    lower = {k.lower(): v for k, v in headers.items()}
    timestamp, signature = lower.get("x-kirana-timestamp", ""), lower.get("x-kirana-signature", "")
    if not timestamp.isdigit() or not signature:
        raise ProviderError("invalid_signature", retryable=False)
    if abs((now if now is not None else time.time()) - int(timestamp)) > TOLERANCE_SECONDS:
        raise ProviderError("stale_timestamp", retryable=False)
    if not hmac.compare_digest(sign(secret, timestamp, body), signature.lower()):
        raise ProviderError("invalid_signature", retryable=False)
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ProviderError("invalid_payload", retryable=False) from exc
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("event_id"), str)
        or not data["event_id"]
        or len(data["event_id"]) > 120
    ):
        raise ProviderError("invalid_payload", retryable=False)
    kind = str(data.get("type", ""))[:60]
    payment = None
    if kind == "payment.status":
        txn, status = data.get("txn_id"), data.get("status")
        if not isinstance(txn, str) or not txn or status not in _STATUSES:
            raise ProviderError("invalid_payload", retryable=False)
        payment = ProviderPayment(
            txn,
            str(status),
            _money(data.get("amount"), "amount"),
            _money(data.get("refunded"), "refunded"),
            str(data.get("failure_code") or "")[:60] or None,
        )
    return WebhookNotice(data["event_id"], kind or "unknown", payment)


class ManualPaymentProvider:
    name = "manual"
    external = False

    def create(
        self, *, amount: Decimal, method: str, reference: str, idempotency_key: str, timeout: float
    ) -> ProviderPayment:
        return ProviderPayment(f"manual-{uuid.uuid4().hex[:16]}", OnlinePaymentStatus.PENDING.value, amount)

    def fetch_status(self, txn_id: str, *, timeout: float) -> ProviderPayment:
        raise ProviderError("status_check_not_supported", retryable=False)

    def refund(
        self, txn_id: str, *, amount: Decimal, idempotency_key: str, timeout: float
    ) -> ProviderPayment:
        return ProviderPayment(
            txn_id, OnlinePaymentStatus.REFUNDED.value, None, amount
        )  # cash handed back by a person

    def parse_webhook(self, headers: dict[str, str], body: bytes, *, secret: str) -> WebhookNotice:
        raise ProviderError("webhooks_not_supported", retryable=False)


class GenericWebhookProvider:
    name = "generic_webhook"
    external = True

    def create(
        self, *, amount: Decimal, method: str, reference: str, idempotency_key: str, timeout: float
    ) -> ProviderPayment:
        if not reference:
            raise ProviderError("transaction_id_required", retryable=False)
        return ProviderPayment(reference, OnlinePaymentStatus.PENDING.value, amount)

    def fetch_status(self, txn_id: str, *, timeout: float) -> ProviderPayment:
        raise ProviderError("status_check_not_supported", retryable=False)

    def refund(
        self, txn_id: str, *, amount: Decimal, idempotency_key: str, timeout: float
    ) -> ProviderPayment:
        raise ProviderError("refund_at_provider", retryable=False)

    def parse_webhook(self, headers: dict[str, str], body: bytes, *, secret: str) -> WebhookNotice:
        return verify_and_parse(headers, body, secret)


PROVIDERS: dict[str, type] = {"manual": ManualPaymentProvider, "generic_webhook": GenericWebhookProvider}
_ = PaymentProvider  # the interface these adapters implement
