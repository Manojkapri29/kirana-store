"""Price intelligence: what outside sources say a product costs. Information only, never an instruction.

Nothing here ever changes a product's MRP, selling price, purchase price or average cost, and nothing here can
stand in the way of a bill: it is a separate feature that a person asks for, and every failure of an outside
provider is reported as a status, not raised.

A check has three steps so that no database transaction is ever held open while waiting for the internet:

  1. `prepare`     (read)   the plan check, the local product, what the shop saved, which providers to ask;
  2. `fetch_live`  (no DB)  ask those providers, each with a timeout, in parallel;
  3. `finish`      (write)  count the check against the plan, save what was learned, and build the answer.

Each provider is treated on its own: one that is not configured, switched off, slow, limiting requests or
broken simply shows that status, and the others still answer. When a provider cannot be reached the
shop's last saved prices from it are shown, marked stale.

Matching (a price is only ever shown next to a product it could really be for):
  1. exact barcode (a UPC-A and its EAN-13 twin count as the same)      EXACT MATCH
  2. exact SKU (for providers that report one)                          EXACT MATCH
  3. exact name + brand + pack size, ignoring case and punctuation      EXACT MATCH
  4. similar name (and the same pack size, when both are known)         POSSIBLE MATCH (lower confidence)
  Anything less similar is dropped. A different pack size is never a match.

Location is a city, state or market the person types. There is no GPS. A price from that place is listed first
and marked; if nothing matches, or none was given, the result says plainly that location was NOT applied.
Prices are shown in the currency the source reported; they are never converted or compared across currencies.
"""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import PriceObservation, Product
from app.models.enums import EventSeverity
from app.services import entitlement_service, price_providers, system_event_service
from app.services.errors import InvalidInputError, NotFoundError
from app.services.price_providers import Identity, PriceProvider, ProviderUnavailable, Quote
from app.services.product_lookup_service import (  # noqa: F401  (pack_size_of stays importable from here)
    barcode_variants,
    name_key,
    normalize_name,
    pack_size_of,
)

FEATURE = "price_intelligence"
SHOP_CURRENCY = "INR"  # the currency every price in this shop is in (see app.core.locale)
MAX_QUOTES = 20
FUZZY_THRESHOLD = 0.75
NO_LOCATION_NOTE = "Location was not applied: no city, state or market was given."
NO_PRICE_NOTE = (
    "No outside price was found for this barcode. That is common: these sources have limited coverage, "
    "especially in India. Your own prices are not affected."
)


class MatchType(StrEnum):
    EXACT = "EXACT"
    POSSIBLE = "POSSIBLE"


class ProviderState(StrEnum):
    LIVE = "LIVE"  # asked just now and answered
    CACHED = "CACHED"  # the shop's saved copy is recent, so it was not asked again
    STALE = "STALE"  # could not be reached; the shop's older saved copy is shown
    NO_DATA = "NO_DATA"  # answered: it has nothing for this barcode
    NOT_CONFIGURED = "NOT_CONFIGURED"  # needs a key that this server does not have
    DISABLED = "DISABLED"  # outside lookups are switched off on this server
    UNAVAILABLE = "UNAVAILABLE"  # could not be reached and nothing was saved


@dataclass(frozen=True)
class LocationHint:
    city: str | None = None
    state: str | None = None
    market: str | None = None

    @property
    def given(self) -> bool:
        return any((self.city, self.state, self.market))

    def words(self) -> list[str]:
        return [normalize_name(v) for v in (self.city, self.state, self.market) if v and normalize_name(v)]


@dataclass(frozen=True)
class LocalRef:
    id: int
    name: str
    sku: str
    barcode: str | None
    brand: str | None
    selling_price: Decimal
    mrp: Decimal | None


@dataclass(frozen=True)
class Match:
    kind: MatchType
    basis: str  # barcode | sku | name_brand_pack | similar_name
    confidence: int  # 0-100


@dataclass(frozen=True)
class ResultQuote:
    quote: Quote
    match: Match
    matched: LocalRef | None
    location_match: bool | None  # None when no location was given
    stale: bool
    currency_matches_shop: bool
    difference: Decimal | None  # price minus our selling price; only when both are in the same currency


@dataclass(frozen=True)
class ProviderReport:
    name: str
    label: str
    state: ProviderState
    message: str
    checked_at: datetime | None = None


@dataclass(frozen=True)
class LocationOutcome:
    requested: LocationHint | None
    applied: bool
    note: str


@dataclass(frozen=True)
class PriceResult:
    barcode: str
    local: LocalRef | None
    quotes: list[ResultQuote]
    providers: list[ProviderReport]
    location: LocationOutcome
    identity: Identity | None  # what a provider says the barcode is (never used to create a product)
    notes: list[str]


@dataclass
class _Plan:
    provider: PriceProvider
    state: ProviderState | None  # decided without asking (NOT_CONFIGURED, DISABLED, CACHED), else None
    cached: list[Quote] = field(default_factory=list)
    cached_at: datetime | None = None

    @property
    def needs_live(self) -> bool:
        return self.state is None


@dataclass
class Prepared:
    shop_id: int
    barcode: str
    local: LocalRef | None
    location: LocationHint | None
    plans: list[_Plan]
    settings: Settings
    now: datetime

    @property
    def needs_live(self) -> bool:
        return any(plan.needs_live for plan in self.plans)


@dataclass
class Fetched:
    outcomes: dict[str, price_providers.Outcome] = field(default_factory=dict)
    problems: dict[str, str] = field(default_factory=dict)


# --- Matching ------------------------------------------------------------------------------------


def _label(brand: str | None, name: str | None) -> str:
    return name_key(f"{brand or ''} {name or ''}")


def match_quote(local: LocalRef | None, query_barcode: str, quote: Quote) -> Match | None:
    """How well this price fits the product being asked about, or None if it does not fit at all."""
    twin = set(barcode_variants(quote.barcode))
    if local is None:
        # Asked by barcode alone: a price reported for that very barcode is an exact match.
        return Match(MatchType.EXACT, "barcode", 100) if twin & set(barcode_variants(query_barcode)) else None
    if local.barcode and twin & set(barcode_variants(local.barcode)):
        return Match(MatchType.EXACT, "barcode", 100)
    if quote.sku and quote.sku.strip().upper() == local.sku.upper():
        return Match(MatchType.EXACT, "sku", 98)

    ours, theirs = pack_size_of(local.name), pack_size_of(quote.pack_text, quote.product_name)
    if ours and theirs and ours != theirs:
        return None  # a different pack size is a different product to compare against
    if not quote.product_name:
        return None
    same_name = name_key(quote.product_name) == name_key(local.name)
    same_brand = bool(
        local.brand and quote.brand and normalize_name(local.brand) == normalize_name(quote.brand)
    )
    if same_name and same_brand and ours and theirs:
        return Match(MatchType.EXACT, "name_brand_pack", 90)
    ratio = SequenceMatcher(
        None, _label(local.brand, local.name), _label(quote.brand, quote.product_name)
    ).ratio()
    if ratio >= FUZZY_THRESHOLD:
        return Match(MatchType.POSSIBLE, "similar_name", min(70, int(ratio * 70)))
    return None


def _location_matches(quote: Quote, hint: LocationHint) -> bool:
    haystack = normalize_name(quote.location_text or "")
    return bool(haystack) and any(word in haystack for word in hint.words())


def apply_location(
    quotes: list[ResultQuote], hint: LocationHint | None
) -> tuple[list[ResultQuote], LocationOutcome]:
    """Mark the prices from the requested place and list them first; says so when it could not be used."""
    if hint is None or not hint.given:
        return quotes, LocationOutcome(None, False, NO_LOCATION_NOTE)
    marked = [
        ResultQuote(**{**q.__dict__, "location_match": _location_matches(q.quote, hint)}) for q in quotes
    ]
    where = ", ".join(v for v in (hint.market, hint.city, hint.state) if v)
    if any(q.location_match for q in marked):
        return marked, LocationOutcome(hint, True, f"Prices seen in {where} are listed first.")
    return marked, LocationOutcome(
        hint,
        False,
        f"Location was not applied: none of the prices found is from {where}. "
        "Showing prices from other places.",
    )


# --- Step 1: prepare ---------------------------------------------------------------------------


def _clean_barcode(raw: str) -> str:
    code = "".join(raw.split())
    if not price_providers.digits_only(code):
        raise InvalidInputError("Enter a barcode of 6 to 14 digits.", field="barcode")
    return code


def _local_ref(product: Product) -> LocalRef:
    return LocalRef(
        product.id,
        product.name,
        product.sku,
        product.barcode,
        product.brand,
        product.selling_price,
        product.mrp,
    )


def _load_cache(
    session: Session, shop_id: int, barcode: str, provider: str
) -> tuple[list[Quote], datetime | None]:
    """The newest batch of saved prices for this barcode from this provider."""
    latest = session.scalar(
        select(func.max(PriceObservation.checked_at)).where(
            PriceObservation.shop_id == shop_id,
            PriceObservation.barcode == barcode,
            PriceObservation.provider == provider,
        )
    )
    if latest is None:
        return [], None
    rows = session.scalars(
        select(PriceObservation)
        .where(
            PriceObservation.shop_id == shop_id,
            PriceObservation.barcode == barcode,
            PriceObservation.provider == provider,
            PriceObservation.checked_at == latest,
        )
        .order_by(PriceObservation.id)
    )
    return [_quote_of(r) for r in rows], latest


def _quote_of(row: PriceObservation) -> Quote:
    return Quote(
        provider=row.provider,
        barcode=row.barcode,
        price=row.price,
        currency=row.currency,
        product_name=row.product_name,
        brand=row.brand,
        pack_text=row.pack_text,
        location_text=row.location_text,
        city=row.city,
        source_url=row.source_url,
        observed_on=row.observed_on,
        checked_at=row.checked_at,
    )


def prepare(
    session: Session,
    shop_id: int,
    *,
    product_id: int | None = None,
    barcode: str | None = None,
    location: LocationHint | None = None,
    settings: Settings | None = None,
    providers: Sequence[PriceProvider] | None = None,
) -> Prepared:
    """Read-only. Refuses (403) a plan without price intelligence or one that has used its month's checks."""
    entitlement_service.require_feature(session, shop_id, FEATURE)
    settings = settings or get_settings()
    local: LocalRef | None = None
    if product_id is not None:
        product = session.scalar(select(Product).where(Product.shop_id == shop_id, Product.id == product_id))
        if product is None:
            raise NotFoundError("Product not found")  # also the answer for another shop's product
        local = _local_ref(product)
    raw = barcode if barcode and barcode.strip() else (local.barcode if local else None)
    if not raw:
        raise InvalidInputError(
            "This product has no barcode. Enter one to look up a price." if local else "Enter a barcode.",
            field="barcode",
        )
    code = _clean_barcode(raw)
    if local is None:  # asked by barcode alone: use our own product with that barcode, if we have one
        product = session.scalar(
            select(Product).where(Product.shop_id == shop_id, Product.barcode.in_(barcode_variants(code)))
        )
        local = _local_ref(product) if product else None

    now = utc_now()
    ttl = timedelta(hours=settings.price_cache_ttl_hours)
    plans: list[_Plan] = []
    for provider in providers if providers is not None else price_providers.all_providers():
        cached, cached_at = (
            _load_cache(session, shop_id, code, provider.name) if provider.gives_prices else ([], None)
        )
        if not settings.external_lookups_enabled:
            plans.append(_Plan(provider, ProviderState.DISABLED, cached, cached_at))
        elif not provider.is_configured(settings):
            plans.append(_Plan(provider, ProviderState.NOT_CONFIGURED, cached, cached_at))
        elif cached_at is not None and ttl > timedelta(0) and now - cached_at < ttl:
            plans.append(_Plan(provider, ProviderState.CACHED, cached, cached_at))
        else:
            plans.append(_Plan(provider, None, cached, cached_at))
    if not any(plan.needs_live and plan.provider.gives_prices for plan in plans):
        # Nothing that gives prices needs asking, so do not ask a source that only identifies a product.
        for plan in plans:
            if plan.needs_live and not plan.provider.gives_prices:
                plan.state = ProviderState.CACHED
    prepared = Prepared(shop_id, code, local, location, plans, settings, now)
    if prepared.needs_live:
        used = entitlement_service.get_usage(session, shop_id, entitlement_service.METRIC_PRICE_LOOKUPS)
        entitlement_service.check_limit(session, shop_id, "max_price_lookups_per_month", used)
    return prepared


# --- Step 2: fetch (no database) -----------------------------------------------------------------


def fetch_live(prepared: Prepared) -> Fetched:
    """Ask every provider that needs asking, in parallel, each with its own timeout. Never raises."""
    todo = [plan for plan in prepared.plans if plan.needs_live]
    fetched = Fetched()
    if not todo:
        return fetched

    def ask(plan: _Plan) -> tuple[str, price_providers.Outcome | str]:
        name = plan.provider.name
        try:
            return name, plan.provider.lookup(prepared.barcode, prepared.settings, price_providers.http_get)
        except ProviderUnavailable as exc:
            price_providers.log.warning("provider %s unavailable: %s", name, exc.reason)
            return name, exc.reason
        except Exception as exc:  # a provider bug must never reach a person as an error
            price_providers.log.warning("provider %s failed: %s", name, type(exc).__name__)
            return name, "sent something unexpected"

    with ThreadPoolExecutor(max_workers=len(todo)) as pool:
        for name, result in pool.map(ask, todo):
            if isinstance(result, str):
                fetched.problems[name] = result
            else:
                fetched.outcomes[name] = result
    return fetched


# --- Step 3: finish (write) ------------------------------------------------------------------------


def _save(session: Session, shop_id: int, quotes: Sequence[Quote], checked_at: datetime) -> list[Quote]:
    saved: list[Quote] = []
    for q in quotes:
        session.add(
            PriceObservation(
                shop_id=shop_id,
                barcode=q.barcode,
                provider=q.provider,
                product_name=q.product_name,
                brand=q.brand,
                pack_text=q.pack_text,
                price=q.price,
                currency=q.currency,
                location_text=q.location_text,
                city=q.city,
                source_url=q.source_url,
                observed_on=q.observed_on,
                checked_at=checked_at,
            )
        )
        saved.append(Quote(**{**q.__dict__, "checked_at": checked_at}))
    session.flush()
    return saved


def _report(plan: _Plan, state: ProviderState, message: str, at: datetime | None = None) -> ProviderReport:
    return ProviderReport(plan.provider.name, plan.provider.label, state, message, at)


def finish(session: Session, ctx: RequestContext, prepared: Prepared, fetched: Fetched) -> PriceResult:
    """Count the check, save what was learned and build the answer. Never changes a product."""
    answered = [name for name in fetched.outcomes]
    if answered:
        entitlement_service.use_metered(session, ctx.shop_id, entitlement_service.METRIC_PRICE_LOOKUPS)

    quotes: list[tuple[Quote, bool]] = []  # (quote, stale)
    reports: list[ProviderReport] = []
    identity: Identity | None = None
    for plan in prepared.plans:
        name, label = plan.provider.name, plan.provider.label
        if plan.state is ProviderState.NOT_CONFIGURED:
            reports.append(_report(plan, plan.state, f"{label} is not set up on this server (no API key)."))
        elif plan.state is ProviderState.DISABLED:
            reports.append(_report(plan, plan.state, "Outside lookups are switched off on this server."))
        elif plan.state is ProviderState.CACHED and not plan.provider.gives_prices:
            reports.append(_report(plan, plan.state, "Not asked: the saved prices were recent."))
        elif plan.state is ProviderState.CACHED:
            reports.append(
                _report(
                    plan,
                    plan.state,
                    "Showing the recent saved copy.",
                    plan.cached_at,
                )
            )
            quotes += [(q, False) for q in plan.cached]
        elif name in fetched.outcomes:
            outcome = fetched.outcomes[name]
            identity = identity or outcome.identity
            saved = _save(session, ctx.shop_id, outcome.quotes, prepared.now) if outcome.quotes else []
            quotes += [(q, False) for q in saved]
            if plan.provider.gives_prices:
                state = ProviderState.LIVE if saved else ProviderState.NO_DATA
                reports.append(
                    _report(
                        plan,
                        state,
                        "Checked just now." if saved else "Has no price for it.",
                        prepared.now,
                    )
                )
            else:
                found = outcome.identity is not None
                reports.append(
                    _report(plan, ProviderState.LIVE if found else ProviderState.NO_DATA,
                            "Identified it." if found else "Does not know this barcode.", prepared.now)
                )  # fmt: skip
        else:
            reason = fetched.problems.get(name, "could not be reached")
            # For the operators: an outside price service is failing. Never affects the bill.
            system_event_service.record(
                session,
                category="integration",
                severity=EventSeverity.WARNING,
                source=f"price:{name}",
                code="unavailable",
                message=f"{plan.provider.label} {reason}.",
                shop_id=ctx.shop_id,
            )
            if plan.cached:
                quotes += [(q, True) for q in plan.cached]
                reports.append(
                    _report(
                        plan,
                        ProviderState.STALE,
                        f"{label} {reason}. Showing the last saved.",
                        plan.cached_at,
                    )
                )
            else:
                reports.append(_report(plan, ProviderState.UNAVAILABLE, f"{label} {reason}."))

    local = prepared.local
    results: list[ResultQuote] = []
    for quote, stale in quotes:
        match = match_quote(local, prepared.barcode, quote)
        if match is None:
            continue
        same_currency = quote.currency == SHOP_CURRENCY
        results.append(
            ResultQuote(
                quote=quote,
                match=match,
                matched=local,
                location_match=None,
                stale=stale,
                currency_matches_shop=same_currency,
                difference=quote.price - local.selling_price if local and same_currency else None,
            )
        )
    results, location = apply_location(results, prepared.location)
    results.sort(
        key=lambda r: (
            r.match.kind is not MatchType.EXACT,
            r.location_match is not True,
            -(r.quote.observed_on.toordinal() if r.quote.observed_on else 0),
            r.quote.price,
        )
    )
    notes = [] if results else [NO_PRICE_NOTE]
    if any(not r.currency_matches_shop for r in results):
        notes.append(f"Some prices are not in {SHOP_CURRENCY}. They are shown as reported, not converted.")
    return PriceResult(prepared.barcode, local, results[:MAX_QUOTES], reports, location, identity, notes)


# --- Provider status and history -------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    label: str
    gives_prices: bool
    configured: bool  # true or false only: nothing about the key itself is ever shown
    enabled: bool


def provider_statuses(settings: Settings | None = None) -> list[ProviderStatus]:
    settings = settings or get_settings()
    return [
        ProviderStatus(
            p.name, p.label, p.gives_prices, p.is_configured(settings), settings.external_lookups_enabled
        )
        for p in price_providers.all_providers()
    ]


def list_history(
    session: Session,
    shop_id: int,
    *,
    barcode: str | None = None,
    provider: str | None = None,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[PriceObservation], int]:
    """Every price this shop has saved, newest check first. Needs the plan's price intelligence."""
    entitlement_service.require_feature(session, shop_id, FEATURE)
    conditions = [PriceObservation.shop_id == shop_id]
    if barcode and barcode.strip():
        conditions.append(PriceObservation.barcode.in_(barcode_variants("".join(barcode.split()))))
    if provider:
        conditions.append(PriceObservation.provider == provider)
    total = session.scalar(select(func.count()).select_from(PriceObservation).where(*conditions)) or 0
    query = (
        select(PriceObservation)
        .where(*conditions)
        .order_by(PriceObservation.checked_at.desc(), PriceObservation.id)
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query)), total


@dataclass(frozen=True)
class SavedQuote:
    quote: Quote
    match: Match


@dataclass(frozen=True)
class SavedComparison:
    product: LocalRef
    quotes: list[SavedQuote]  # newest check first; only prices that fit this product
    checked_at: datetime | None
    has_barcode: bool


def saved_comparison(session: Session, shop_id: int, product_id: int) -> SavedComparison:
    """The prices already SAVED for one product, matched against it. Reads the shop's own history only: it
    never
    asks an outside source, so it is instant, free, and cannot fail because a provider is down. Needs the
    plan's
    price intelligence like every price feature. Information only: nothing here changes a price."""
    entitlement_service.require_feature(session, shop_id, FEATURE)
    product = session.scalar(select(Product).where(Product.shop_id == shop_id, Product.id == product_id))
    if product is None:
        raise NotFoundError("Product not found")
    local = _local_ref(product)
    if not product.barcode:
        return SavedComparison(local, [], None, False)
    rows = list(
        session.scalars(
            select(PriceObservation)
            .where(
                PriceObservation.shop_id == shop_id,
                PriceObservation.barcode.in_(barcode_variants(product.barcode)),
            )
            .order_by(PriceObservation.checked_at.desc(), PriceObservation.id)
        )
    )
    # Only the newest batch per provider: older checks of the same source are superseded, not extra prices.
    newest: dict[str, datetime] = {}
    for row in rows:
        newest.setdefault(row.provider, row.checked_at)
    quotes: list[SavedQuote] = []
    for row in rows:
        if row.checked_at != newest[row.provider]:
            continue
        quote = _quote_of(row)
        match = match_quote(local, product.barcode, quote)
        if match is not None:
            quotes.append(SavedQuote(quote, match))
    return SavedComparison(local, quotes, max(newest.values()) if newest else None, True)
