"""External product and price providers behind one interface. Backend only; a provider is optional.

Adding a provider (for example a future market-price feed) means writing one class with `name`, `label`,
`is_configured` and `lookup`, and listing it in `all_providers`. Nothing else changes.

What each provider really gives (checked against its own documentation when this was written):

* Open Food Facts   product identity by barcode (name, brand, pack size). Free, no key, needs an identifying
                    User-Agent, about 15 product reads a minute per IP. It has NO prices.
* Open Prices       crowdsourced shelf prices by barcode with the shop or city they were seen in. Free, no
                    key, open data. Coverage depends on contributors, so many products, especially in India,
                    have none.
* UPCitemdb         product details and online offers by barcode. Needs a paid key for real use (the free
                    trial is about 100 requests a day). Offers are online-shop prices, mostly in other
                    currencies, and the documentation does not promise coverage of India.

No provider is guaranteed to have a price for a product. Prices are only ever information for a person.

Safety: only the fixed HTTPS hosts below are ever called, the barcode is digits only, every call has a
timeout and a size cap, and a failure becomes `ProviderUnavailable` (never an exception the caller must
handle for a bill to work). The API key is sent in a header, and is never logged or put in a message.
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import quote as url_quote

from app.core.config import Settings

log = logging.getLogger("app.external")

MAX_BODY_BYTES = 1_000_000
OFF_HOST = "https://world.openfoodfacts.org"
PRICES_HOST = "https://prices.openfoodfacts.org"
UPC_HOST = "https://api.upcitemdb.com"


class ProviderUnavailable(Exception):
    """The provider could not answer (network, timeout, rate limit, bad reply). Safe to show `reason`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Quote:
    """One price somebody saw for one product, as a provider reported it. Informational only."""

    provider: str
    barcode: str
    price: Decimal
    currency: str  # three letters, as reported. Never converted.
    product_name: str | None = None
    brand: str | None = None
    pack_text: str | None = None  # "500 g", when the provider says so
    location_text: str | None = None  # "Tesco Extra, Cardiff, United Kingdom" or "online: example.com"
    city: str | None = None
    source_url: str | None = None
    observed_on: date | None = None
    checked_at: datetime | None = None  # when WE asked (set by the service)
    sku: str | None = None  # for a provider that reports the seller's own SKU (not stored)


@dataclass(frozen=True)
class Identity:
    """What a provider says a barcode is."""

    barcode: str
    name: str | None
    brand: str | None
    pack_text: str | None


@dataclass(frozen=True)
class Outcome:
    quotes: list[Quote] = field(default_factory=list)
    identity: Identity | None = None


HttpGet = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]


def http_get(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    """The only place the program talks to the internet. HTTPS only; a body larger than the cap is refused."""
    if not url.startswith("https://"):
        raise ProviderUnavailable("insecure address refused")
    request = urllib.request.Request(url, headers=dict(headers), method="GET")  # noqa: S310  (https only, fixed hosts)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read(MAX_BODY_BYTES + 1)
            status = response.status
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderUnavailable("could not be reached in time") from None
    if len(body) > MAX_BODY_BYTES:
        raise ProviderUnavailable("sent an unexpectedly large reply")
    return status, body


def _json(status: int, body: bytes) -> Any:
    if status == 429:
        raise ProviderUnavailable("is limiting requests right now")
    if status in (401, 403):
        raise ProviderUnavailable("refused the request (check the account or key)")
    if status >= 500:
        raise ProviderUnavailable("is having problems")
    if status == 404:
        return None
    if status != 200:
        raise ProviderUnavailable(f"answered with an unexpected status ({status})")
    try:
        return json.loads(body)
    except ValueError:
        raise ProviderUnavailable("sent a reply that could not be read") from None


class Throttle:
    """At most `calls` in any `seconds`. Protects a provider's fair-use limit for the whole server."""

    def __init__(self, calls: int, seconds: float) -> None:
        self.calls, self.seconds = calls, seconds
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._times and now - self._times[0] > self.seconds:
                self._times.popleft()
            if len(self._times) >= self.calls:
                return False
            self._times.append(now)
            return True


def digits_only(barcode: str) -> bool:
    return barcode.isascii() and barcode.isdigit() and 6 <= len(barcode) <= 14


def _money(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number <= 0 or number >= Decimal(10) ** 10:
        return None
    return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit] or None


def _currency(value: Any) -> str | None:
    if isinstance(value, str) and len(value.strip()) == 3 and value.strip().isalpha():
        return value.strip().upper()
    return None


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class PriceProvider(Protocol):
    name: str
    label: str
    gives_prices: bool

    def is_configured(self, settings: Settings) -> bool: ...

    def lookup(self, barcode: str, settings: Settings, get: HttpGet) -> Outcome: ...


# --- Open Food Facts: who is this barcode? (no prices) ------------------------------------------------


def parse_open_food_facts(barcode: str, payload: Any) -> Outcome:
    product = payload.get("product") if isinstance(payload, dict) else None
    if not isinstance(product, dict) or payload.get("status") == 0:
        return Outcome()
    return Outcome(
        identity=Identity(
            barcode=barcode,
            name=_text(product.get("product_name"), 200),
            brand=_text((product.get("brands") or "").split(",")[0], 100),
            pack_text=_text(product.get("quantity"), 50),
        )
    )


class OpenFoodFacts:
    name = "open_food_facts"
    label = "Open Food Facts"
    gives_prices = False
    _throttle = Throttle(10, 60)  # their limit is 15 product reads a minute per IP; stay under it

    def is_configured(self, settings: Settings) -> bool:
        return True

    def lookup(self, barcode: str, settings: Settings, get: HttpGet) -> Outcome:
        if not self._throttle.allow():
            raise ProviderUnavailable("is being asked too often; try again in a minute")
        url = f"{OFF_HOST}/api/v2/product/{url_quote(barcode)}.json?fields=code,product_name,brands,quantity"
        headers = {"User-Agent": settings.off_user_agent, "Accept": "application/json"}
        status, body = get(url, headers, settings.external_timeout_seconds)
        return parse_open_food_facts(barcode, _json(status, body))


# --- Open Prices: crowdsourced shelf prices ---------------------------------------------------------


def parse_open_prices(barcode: str, payload: Any) -> Outcome:
    items = payload.get("items") if isinstance(payload, dict) else None
    quotes: list[Quote] = []
    identity: Identity | None = None
    for row in items or []:
        if not isinstance(row, dict):
            continue
        product = row.get("product") if isinstance(row.get("product"), dict) else row
        location = row.get("location") if isinstance(row.get("location"), dict) else row
        if identity is None and _text(product.get("product_name"), 200):
            quantity, unit = product.get("product_quantity"), product.get("product_quantity_unit")
            identity = Identity(
                barcode=barcode,
                name=_text(product.get("product_name"), 200),
                brand=_text((product.get("brands") or "").split(",")[0], 100),
                pack_text=_text(f"{quantity} {unit}", 50) if quantity and unit else None,
            )
        price, currency = _money(row.get("price")), _currency(row.get("currency"))
        if price is None or currency is None:
            continue
        city = _text(location.get("osm_address_city"), 80)
        country = _text(location.get("osm_address_country"), 80)
        place = _text(location.get("osm_name"), 100)
        where = ", ".join(part for part in (place, city, country) if part) or None
        quotes.append(
            Quote(
                provider="open_prices",
                barcode=barcode,
                price=price,
                currency=currency,
                product_name=identity.name if identity else None,
                brand=identity.brand if identity else None,
                pack_text=identity.pack_text if identity else None,
                location_text=where,
                city=city,
                source_url=f"{PRICES_HOST}/products/{url_quote(barcode)}",
                observed_on=_day(row.get("date")),
            )
        )
    return Outcome(quotes, identity)


class OpenPrices:
    name = "open_prices"
    label = "Open Prices"
    gives_prices = True

    def is_configured(self, settings: Settings) -> bool:
        return True

    def lookup(self, barcode: str, settings: Settings, get: HttpGet) -> Outcome:
        url = f"{PRICES_HOST}/api/v1/prices?product_code={url_quote(barcode)}&size=50&order_by=-date"
        headers = {"User-Agent": settings.off_user_agent, "Accept": "application/json"}
        status, body = get(url, headers, settings.external_timeout_seconds)
        return parse_open_prices(barcode, _json(status, body))


# --- UPCitemdb: online offers (needs a key) ---------------------------------------------------------


def parse_upcitemdb(barcode: str, payload: Any) -> Outcome:
    items = payload.get("items") if isinstance(payload, dict) else None
    if not items or not isinstance(items[0], dict):
        return Outcome()
    item = items[0]
    identity = Identity(
        barcode=barcode,
        name=_text(item.get("title"), 200),
        brand=_text(item.get("brand"), 100),
        pack_text=_text(item.get("size"), 50),
    )
    quotes: list[Quote] = []
    for offer in item.get("offers") or []:
        if not isinstance(offer, dict):
            continue
        price, currency = _money(offer.get("price")), _currency(offer.get("currency"))
        if price is None or currency is None:  # no price or no currency: never guess either
            continue
        domain = _text(offer.get("domain"), 100)
        merchant = _text(offer.get("merchant"), 60) or domain
        stamp = offer.get("updated_t")
        seen = datetime.fromtimestamp(stamp, tz=UTC).date() if isinstance(stamp, int) and stamp > 0 else None
        quotes.append(
            Quote(
                provider="upcitemdb",
                barcode=barcode,
                price=price,
                currency=currency,
                product_name=identity.name,
                brand=identity.brand,
                pack_text=identity.pack_text,
                location_text=f"online: {merchant}" if merchant else "online",
                source_url=f"https://{domain}" if domain else None,
                observed_on=seen,
            )
        )
    return Outcome(quotes, identity)


class UpcItemDb:
    name = "upcitemdb"
    label = "UPCitemdb"
    gives_prices = True

    def is_configured(self, settings: Settings) -> bool:
        return settings.upcitemdb_api_key is not None

    def lookup(self, barcode: str, settings: Settings, get: HttpGet) -> Outcome:
        key = settings.upcitemdb_api_key
        if key is None:
            raise ProviderUnavailable("is not configured")
        url = f"{UPC_HOST}/prod/v1/lookup?upc={url_quote(barcode)}"
        headers = {"user_key": key.get_secret_value(), "key_type": "3scale", "Accept": "application/json"}
        status, body = get(url, headers, settings.external_timeout_seconds)
        return parse_upcitemdb(barcode, _json(status, body))


def all_providers() -> list[PriceProvider]:
    return [OpenFoodFacts(), OpenPrices(), UpcItemDb()]
