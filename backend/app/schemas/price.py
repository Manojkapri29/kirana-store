from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.schemas.common import Page
from app.services.price_comparison_service import (
    LocalRef,
    MatchType,
    PriceResult,
    ProviderReport,
    ProviderState,
    ProviderStatus,
    ResultQuote,
)

Place = Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]


class PriceCheckIn(BaseModel):
    """Ask for outside prices for a product (`product_id`) or a barcode. Location is optional text (a city, a
    state or a market): there is no GPS and no location is ever sent to a provider."""

    model_config = ConfigDict(extra="forbid")

    product_id: int | None = None
    barcode: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] | None = None
    city: Place | None = None
    state: Place | None = None
    market: Place | None = None


class MatchedProductOut(BaseModel):
    id: int
    name: str
    sku: str
    barcode: str | None
    selling_price: Decimal
    mrp: Decimal | None

    @classmethod
    def from_ref(cls, ref: LocalRef) -> "MatchedProductOut":
        return cls(
            id=ref.id,
            name=ref.name,
            sku=ref.sku,
            barcode=ref.barcode,
            selling_price=ref.selling_price,
            mrp=ref.mrp,
        )


class PriceQuoteOut(BaseModel):
    """One outside price. Information only: it never changes any price or cost in the shop."""

    product_name: str | None
    matched_product: MatchedProductOut | None
    barcode: str
    price: Decimal
    currency: str  # as the source reported it, never converted
    source: str  # provider name, e.g. "open_prices"
    source_label: str
    source_url: str | None
    location: str | None  # where the price was seen: a shop, city or website
    location_matched: bool | None  # null when no location was requested
    observed_on: date | None  # when the source saw the price
    checked_at: datetime | None  # when this shop last asked
    match_type: MatchType  # EXACT or POSSIBLE
    match_label: str  # "EXACT MATCH" or "POSSIBLE MATCH"
    match_basis: str  # barcode | sku | name_brand_pack | similar_name
    confidence: int  # 0-100
    stale: bool  # a saved copy shown because the source could not be reached
    currency_matches_shop: bool
    difference: Decimal | None  # price minus our selling price, only when both are in the same currency

    @classmethod
    def from_result(cls, r: ResultQuote, labels: dict[str, str]) -> "PriceQuoteOut":
        q = r.quote
        return cls(
            product_name=q.product_name,
            matched_product=None if r.matched is None else MatchedProductOut.from_ref(r.matched),
            barcode=q.barcode,
            price=q.price,
            currency=q.currency,
            source=q.provider,
            source_label=labels.get(q.provider, q.provider),
            source_url=q.source_url,
            location=q.location_text,
            location_matched=r.location_match,
            observed_on=q.observed_on,
            checked_at=q.checked_at,
            match_type=r.match.kind,
            match_label=f"{r.match.kind.value} MATCH",
            match_basis=r.match.basis,
            confidence=r.match.confidence,
            stale=r.stale,
            currency_matches_shop=r.currency_matches_shop,
            difference=r.difference,
        )


class ProviderReportOut(BaseModel):
    name: str
    label: str
    state: ProviderState
    message: str
    checked_at: datetime | None

    @classmethod
    def from_report(cls, r: ProviderReport) -> "ProviderReportOut":
        return cls(name=r.name, label=r.label, state=r.state, message=r.message, checked_at=r.checked_at)


class LocationOut(BaseModel):
    city: str | None
    state: str | None
    market: str | None
    applied: bool  # false says the location did not affect the result
    note: str


class IdentityOut(BaseModel):
    name: str | None
    brand: str | None
    pack_text: str | None


class PriceResultOut(BaseModel):
    barcode: str
    product: MatchedProductOut | None
    quotes: list[PriceQuoteOut]
    providers: list[ProviderReportOut]
    location: LocationOut
    identified_as: IdentityOut | None  # what a source says the barcode is; never used to create a product
    notes: list[str]
    changes_prices: bool = False  # always false: checking a price never changes any price or cost

    @classmethod
    def from_result(cls, r: PriceResult) -> "PriceResultOut":
        labels = {p.name: p.label for p in r.providers}
        hint = r.location.requested
        return cls(
            barcode=r.barcode,
            product=None if r.local is None else MatchedProductOut.from_ref(r.local),
            quotes=[PriceQuoteOut.from_result(q, labels) for q in r.quotes],
            providers=[ProviderReportOut.from_report(p) for p in r.providers],
            location=LocationOut(
                city=hint.city if hint else None,
                state=hint.state if hint else None,
                market=hint.market if hint else None,
                applied=r.location.applied,
                note=r.location.note,
            ),
            identified_as=None
            if r.identity is None
            else IdentityOut(name=r.identity.name, brand=r.identity.brand, pack_text=r.identity.pack_text),
            notes=r.notes,
        )


class ProviderStatusOut(BaseModel):
    """Whether a provider can be used on this server. The key itself is never shown, only true or false."""

    name: str
    label: str
    gives_prices: bool
    configured: bool
    enabled: bool

    @classmethod
    def from_status(cls, s: ProviderStatus) -> "ProviderStatusOut":
        return cls(
            name=s.name,
            label=s.label,
            gives_prices=s.gives_prices,
            configured=s.configured,
            enabled=s.enabled,
        )


class ProviderListOut(BaseModel):
    items: list[ProviderStatusOut]


class ObservationOut(BaseModel):
    id: int
    barcode: str
    provider: str
    product_name: str | None
    brand: str | None
    price: Decimal
    currency: str
    location: str | None
    source_url: str | None
    observed_on: date | None
    checked_at: datetime


class HistoryOut(Page):
    items: list[ObservationOut]
