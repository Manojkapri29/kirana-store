"""What every adapter shares: errors, secret handling, outbound-URL safety and the result types of the provider interfaces.

Secrets. An integration stores the NAME of an environment variable, never its value. `resolve_secret` reads only variables that begin
with `KIRANA_INTEGRATION_`, so a configured name can never be used to read the database URL, the signing key or any other setting.
A secret is returned to the adapter that needs it and nowhere else: it is not logged, not written to a row and not put in an error.

Retry. Adapters raise `ProviderError(code, retryable=...)`. Only a failure that cannot have had an effect (could not connect, timed
out before sending, HTTP 429/5xx on a message) is marked retryable. Whether a call may be repeated automatically is decided by the
caller from the operation (`SAFE` reads and status checks) and never by the adapter alone.
"""

import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from urllib.parse import urlparse

from app.core.config import get_settings

SECRET_PREFIX = "KIRANA_INTEGRATION_"
_REF = re.compile(r"^KIRANA_INTEGRATION_[A-Z0-9_]{1,70}$")
SAFE = "SAFE"  # reads and status checks: may be repeated
UNSAFE = "UNSAFE"  # anything that moves money, sends a message or creates a record: never repeated blindly


class ProviderError(Exception):
    """A provider could not do what was asked. `code` is safe to show; `retryable` says a later attempt may succeed."""

    def __init__(self, code: str, *, retryable: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class CredentialsMissing(ProviderError):
    def __init__(self, code: str = "credentials_missing") -> None:
        super().__init__(code, retryable=False)


def valid_ref(ref: str | None) -> bool:
    return bool(ref) and bool(_REF.match(ref or ""))


def resolve_secret(ref: str | None) -> str | None:
    """The value of the environment variable named `ref`, or None. Only `KIRANA_INTEGRATION_*` names are ever read."""
    if not valid_ref(ref):
        return None
    value = os.environ.get(ref or "", "").strip()
    return value or None


def has_secret(ref: str | None) -> bool:
    return resolve_secret(ref) is not None


def mask_recipient(value: str) -> str:
    """`ravi@example.com` -> `r***@example.com`; `+919876543210` -> `+91******3210`. Enough to recognise, not to reuse."""
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:1]}***@{domain}"
    digits = value.strip()
    return f"{digits[:3]}{'*' * max(len(digits) - 7, 3)}{digits[-4:]}" if len(digits) > 7 else "***"


def clean_header(value: str, limit: int = 200) -> str:
    """No CR or LF may reach a mail header or an HTTP header (header injection)."""
    return re.sub(r"[\r\n\x00]+", " ", value).strip()[:limit]


def safe_outbound_url(url: str, *, resolve: bool = True) -> str:
    """Refuse a URL the server must not call: not https (in production), credentials in the URL, or a host that resolves to a private,
    loopback, link-local or metadata address (server-side request forgery). In development and tests plain http to localhost is allowed."""
    settings = get_settings()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ProviderError("invalid_url", retryable=False)
    local_ok = not settings.is_production and parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not local_ok:
        raise ProviderError("url_must_be_https", retryable=False)
    if local_ok or not resolve:
        return url
    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
        }
    except OSError as exc:
        raise ProviderError("host_not_found", retryable=True) from exc
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ProviderError("url_not_allowed", retryable=False)
    return url


# --- Messaging (email, SMS, WhatsApp, push) -----------------------------------------------------------------------


class MessageProvider(Protocol):
    name: str

    def send(self, *, recipient: str | None, title: str, message: str, timeout: float) -> str | None:
        """Deliver one message (returns the provider's message id if it gives one) or raise `ProviderError`."""

    def check(self, *, timeout: float) -> None:
        """Verify the connection and credentials WITHOUT sending anything, or raise `ProviderError`."""


# --- Payments -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderPayment:
    """What a provider says about one payment. Amounts are integer paise-exact `Decimal` rupees (two places)."""

    txn_id: str
    status: str  # an OnlinePaymentStatus value
    amount: Decimal | None = None
    refunded: Decimal | None = None
    failure_code: str | None = None


@dataclass(frozen=True)
class WebhookNotice:
    """A verified, parsed webhook: the provider's own event id, its type and, for payment events, what it says about the payment."""

    event_id: str
    event_type: str
    payment: ProviderPayment | None = None


class PaymentProvider(Protocol):
    name: str
    external: bool  # False = no outside system is involved (a person attests): its status can only change by a person

    def create(
        self, *, amount: Decimal, method: str, reference: str, idempotency_key: str, timeout: float
    ) -> ProviderPayment:
        """UNSAFE. Create the payment at the provider. Must pass `idempotency_key` on so a repeat cannot create a second one."""

    def fetch_status(self, txn_id: str, *, timeout: float) -> ProviderPayment:
        """SAFE. Ask the provider for the current state of a payment."""

    def refund(
        self, txn_id: str, *, amount: Decimal, idempotency_key: str, timeout: float
    ) -> ProviderPayment:
        """UNSAFE. Refund part or all of a captured payment."""

    def parse_webhook(self, headers: dict[str, str], body: bytes, *, secret: str) -> WebhookNotice:
        """Verify the signature (and timestamp) and parse the event, or raise `ProviderError('invalid_signature', retryable=False)`."""


# --- Storage and location -----------------------------------------------------------------------------------------


class LocationProvider(Protocol):
    name: str

    def geocode(self, address: str, *, timeout: float) -> tuple[Decimal, Decimal] | None: ...
