"""Turning a photo into SUGGESTIONS for a person to review. An optional layer: nothing depends on it.

The flow is always: Photo, Analysis, Suggestions, Review, Confirm, Save. An analysis changes NOTHING: no
product, price, stock, order, khata entry or file is created or modified by it, and the photo is not kept.
Only the user's explicit confirmation creates a product (through `product_service`, with all its own
rules), and only their explicit choice keeps the photo. Results are labelled DETECTED (read from the photo
or a barcode) or SUGGESTED (a guess from a name, a database or an image provider), never "confirmed".

What an analysis does, in order:
  1. checks the plan (`image_intelligence`) and validates the file (`image_validation`);
  2. takes a barcode read in the browser, if any, and checks its check digit (a misread is ignored, not
  trusted); 3. ONLY if the user asked for it, sends the picture to the configured image provider for text
  and a product guess;
     with no provider configured it says "Image analysis is not configured yet." and carries on;
  4. looks the barcode up in the shop's own products through the Phase 8 lookup (no second barcode system);
  5. ONLY if the user asked, and outside lookups are on, asks Open Food Facts for what the barcode is (the
  barcode
     is sent, never the picture);
  6. suggests a name, brand, pack size, category and unit, and looks for possible duplicates before
  anything could
     be created.

Every query is scoped to the caller's shop. Failures of a provider or lookup are reported as a status and
never stop the rest: the normal application is unaffected.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.context import RequestContext
from app.models import Category, ProductImage, Unit
from app.services import (
    entitlement_service,
    image_providers,
    image_validation,
    price_providers,
    product_lookup_service,
    product_service,
)
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.image_providers import ImageProviderError, ImageReading
from app.services.image_store import ImageStore, default_store, storage_key
from app.services.product_match_service import ProductMatch, Strength, find_possible_matches
from app.services.product_service import ProductView

FEATURE = "image_intelligence"
NOT_CONFIGURED = "Image analysis is not configured yet."
DETECTED = "Detected"
SUGGESTED = "Suggested"
_DIGITS = re.compile(r"(?<!\d)\d{8,14}(?!\d)")
_PACK_TEXT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|gm|gms|mg|l|ltr|litre|liter|ml|pcs|pc)\b", re.I)


class AnalysisStatus(StrEnum):
    LOCAL_ONLY = "LOCAL_ONLY"  # the user did not ask for the image provider
    PROVIDER_USED = "PROVIDER_USED"
    NOT_CONFIGURED = "NOT_CONFIGURED"  # the user asked, but no provider is set up
    PROVIDER_FAILED = "PROVIDER_FAILED"


@dataclass(frozen=True)
class Suggestion:
    field: str  # name | brand | barcode | pack_size | category | unit | visible_text
    value: str
    label: str  # Detected | Suggested
    source: (
        str  # what it came from: barcode_reader, photo_text, open_food_facts, the provider's name, catalog
    )
    category_id: int | None = None
    unit_id: int | None = None


@dataclass(frozen=True)
class Analysis:
    image: image_validation.ImageInfo
    status: AnalysisStatus
    provider_label: str | None
    sent_to_provider: bool
    barcode: str | None
    barcode_note: str | None
    suggestions: list[Suggestion]
    existing: list[ProductView]  # the shop's own product(s) with this barcode
    possible_duplicates: list[ProductMatch]
    visible_text: list[str]
    notes: list[str] = field(default_factory=list)


# --- Barcodes ------------------------------------------------------------------------------------


def valid_gtin(code: str) -> bool:
    """A product barcode (EAN-8, UPC-A, EAN-13, GTIN-14) whose check digit is right; a misread rarely is."""
    if not code.isascii() or not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits[:-1])))
    return (10 - total % 10) % 10 == digits[-1]


def _barcodes_in(lines: list[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        for match in _DIGITS.findall(line.replace(" ", "")):
            if valid_gtin(match) and match not in found:
                found.append(match)
    return found


# --- Analysis ------------------------------------------------------------------------------------


def _limits(settings: Settings) -> tuple[int, int]:
    return settings.image_max_bytes, settings.image_max_side


def _pack_text(*texts: str | None) -> str | None:
    for text in texts:
        found = _PACK_TEXT.search(text or "")
        if found:
            return f"{found.group(1).replace(',', '.')} {found.group(2).lower()}"
    return None


def _category_for(session: Session, shop_id: int, *texts: str | None) -> tuple[int, str] | None:
    haystack = " ".join(t.lower() for t in texts if t)
    if not haystack:
        return None
    best: tuple[int, str] | None = None
    for category in session.scalars(
        select(Category).where(Category.shop_id == shop_id, Category.is_active.is_(True))
    ):
        name = category.name.strip().lower()
        if len(name) >= 3 and name in haystack and (best is None or len(category.name) > len(best[1])):
            best = (category.id, category.name)
    return best


def _piece_unit(session: Session) -> Unit | None:
    return session.scalar(select(Unit).where(Unit.code == "pcs"))


def analyze(
    session: Session,
    shop_id: int,
    *,
    data: bytes,
    declared_type: str | None = None,
    barcode_hint: str | None = None,
    use_provider: bool = False,
    enrich: bool = False,
    settings: Settings | None = None,
) -> Analysis:
    """Read a photo. Read-only: it never writes to the database or the image store."""
    entitlement_service.require_feature(session, shop_id, FEATURE)
    settings = settings or get_settings()
    max_bytes, max_side = _limits(settings)
    info = image_validation.inspect_image(
        data, declared_type=declared_type, max_bytes=max_bytes, max_side=max_side
    )
    notes: list[str] = []
    suggestions: list[Suggestion] = []

    # 2. a barcode read in the browser: trusted only if its check digit is right
    barcode: str | None = None
    barcode_note: str | None = None
    hint = "".join((barcode_hint or "").split())
    if hint:
        if valid_gtin(hint):
            barcode = hint
        else:
            barcode_note = "The barcode read from the photo did not pass its check digit, so it was ignored."

    # 3. the image provider: only on the user's explicit request, and only if one is configured
    status = AnalysisStatus.LOCAL_ONLY
    provider = image_providers.configured_provider(settings) if use_provider else None
    reading = ImageReading()
    if use_provider and provider is None:
        status = AnalysisStatus.NOT_CONFIGURED
        notes.append(NOT_CONFIGURED)
    elif provider is not None:
        try:
            reading = provider.analyze(data, info.content_type, settings)
            status = AnalysisStatus.PROVIDER_USED
        except ImageProviderError as exc:
            status = AnalysisStatus.PROVIDER_FAILED
            notes.append(f"The image service {exc.reason}. You can still enter the details by hand.")
        except Exception:  # a provider bug must never reach the user as an error
            status = AnalysisStatus.PROVIDER_FAILED
            notes.append(
                "The image service could not read this photo. You can still enter the details by hand."
            )
    sent = provider is not None
    source = provider.name if provider is not None else "photo_text"
    guess = reading.product
    if barcode is None:
        candidates = ([guess.barcode] if guess and guess.barcode else []) + _barcodes_in(reading.text_lines)
        barcode = next((c for c in candidates if valid_gtin(c)), None)
        if barcode:
            barcode_note = "Read from the text in the photo."
    if barcode:
        suggestions.append(Suggestion("barcode", barcode, DETECTED, "barcode_reader" if hint else source))

    # 4. the shop's own products with this barcode (the Phase 8 lookup; the plan was already checked above)
    existing: list[ProductView] = []
    if barcode:
        result = product_lookup_service.lookup(session, shop_id, barcode, limit=5, enforce_plan=False)
        if result.match_type is product_lookup_service.MatchType.BARCODE:
            existing = result.products

    # 5. what the barcode is called elsewhere: only if asked, only if allowed, and only the barcode is sent
    name = guess.name if guess else None
    brand = guess.brand if guess else None
    pack = _pack_text(guess.pack_text if guess else None, name, *reading.text_lines[:6])
    category_hint = guess.category_hint if guess else None
    enriched_from: str | None = None
    if enrich and barcode and not existing:
        if not settings.external_lookups_enabled:
            notes.append("Outside lookups are switched off on this server.")
        else:
            try:
                outcome = price_providers.OpenFoodFacts().lookup(barcode, settings, price_providers.http_get)
                identity = outcome.identity
                if identity is not None:
                    name, brand = name or identity.name, brand or identity.brand
                    pack = pack or _pack_text(identity.pack_text, identity.name)
                    enriched_from = "open_food_facts"
                else:
                    notes.append("No outside information was found for this barcode.")
            except price_providers.ProviderUnavailable as exc:
                notes.append(f"Product information service {exc.reason}. You can enter the details by hand.")

    # 6. suggestions, clearly labelled
    guess_source = enriched_from if (enriched_from and not (guess and guess.name)) else source
    if name:
        suggestions.append(Suggestion("name", name, SUGGESTED, guess_source))
    if brand:
        suggestions.append(Suggestion("brand", brand, SUGGESTED, guess_source))
    if pack:
        suggestions.append(Suggestion("pack_size", pack, SUGGESTED, guess_source))
        unit = _piece_unit(session)
        if unit is not None:
            suggestions.append(Suggestion("unit", unit.code, SUGGESTED, "catalog", unit_id=unit.id))
    category = _category_for(session, shop_id, category_hint, name, *reading.text_lines[:12])
    if category:
        suggestions.append(Suggestion("category", category[1], SUGGESTED, "catalog", category_id=category[0]))
    if reading.text_lines:
        suggestions.append(Suggestion("visible_text", " | ".join(reading.text_lines[:12]), DETECTED, source))

    duplicates = find_possible_matches(
        session, shop_id, barcode=barcode, name=name, brand=brand, pack_text=pack
    )
    return Analysis(
        image=info,
        status=status,
        provider_label=provider.label if provider is not None else None,
        sent_to_provider=sent,
        barcode=barcode,
        barcode_note=barcode_note,
        suggestions=suggestions,
        existing=existing,
        possible_duplicates=duplicates,
        visible_text=reading.text_lines[:30],
        notes=notes,
    )


def provider_status(settings: Settings | None = None) -> dict[str, Any]:
    """Whether image analysis can be used on this server. Never reveals a key or a provider's settings."""
    settings = settings or get_settings()
    provider = image_providers.configured_provider(settings)
    return {
        "configured": provider is not None,
        "provider_label": provider.label if provider else None,
        "max_bytes": settings.image_max_bytes,
        "max_side": settings.image_max_side,
        "formats": sorted(image_validation.ALLOWED),
    }


# --- Confirming: the only way a photo leads to saved data ---------------------------------------


def _duplicate_data(matches: list[ProductMatch]) -> dict[str, Any]:
    return {
        "possible_duplicates": [
            {
                "product_id": m.view.product.id,
                "name": m.view.product.name,
                "sku": m.view.product.sku,
                "barcode": m.view.product.barcode,
                "brand": m.view.product.brand,
                "strength": m.strength.label,
                "reasons": m.reasons,
            }
            for m in matches
        ]
    }


def confirm_product(
    session: Session,
    ctx: RequestContext,
    fields: dict[str, Any],
    *,
    image: bytes | None = None,
    declared_type: str | None = None,
    keep_image: bool = False,
    acknowledge_duplicates: bool = False,
    settings: Settings | None = None,
    store: ImageStore | None = None,
) -> product_service.SaveResult:
    """Create the product the user reviewed. Refuses when it looks like one the shop already has, unless the
    user has seen that and confirmed. The photo is kept only when the user asked for it."""
    entitlement_service.require_feature(session, ctx.shop_id, FEATURE)
    settings = settings or get_settings()
    info = None
    if keep_image:
        if image is None:
            raise InvalidInputError("Choose the photo to keep.", field="image", code="image_missing")
        info = image_validation.inspect_image(
            image,
            declared_type=declared_type,
            max_bytes=settings.image_max_bytes,
            max_side=settings.image_max_side,
        )
        _check_room(session, ctx.shop_id, settings)

    matches = find_possible_matches(
        session,
        ctx.shop_id,
        barcode=fields.get("barcode"),
        sku=fields.get("sku"),
        name=fields.get("name"),
        brand=fields.get("brand"),
    )
    exact = [m for m in matches if m.strength is Strength.EXACT]
    unconfirmed = [m for m in matches if m.strength is not Strength.EXACT]
    if exact or (unconfirmed and not acknowledge_duplicates):
        first = (exact or unconfirmed)[0]
        raise ConflictError(
            f"Possible existing product found: '{first.view.product.name}'. Nothing was created.",
            field="barcode" if exact and first.reasons[0] == "The same barcode" else None,
            code="possible_duplicate",
            data=_duplicate_data(matches),
        )

    result = product_service.create_product(session, ctx, fields)
    record_audit(
        session,
        ctx,
        entity_type="product",
        entity_id=result.view.product.id,
        action="create_from_image",
        after={"kept_image": bool(keep_image), "acknowledged_duplicates": acknowledge_duplicates},
    )
    if keep_image and image is not None and info is not None:
        _keep(session, ctx, result.view.product.id, image, info, store or default_store(settings))
    return result


# --- Kept photos: private, shop-scoped, one per product ---------------------------------------------


def _check_room(session: Session, shop_id: int, settings: Settings) -> None:
    kept = (
        session.scalar(select(func.count()).select_from(ProductImage).where(ProductImage.shop_id == shop_id))
        or 0
    )
    if kept >= settings.image_max_per_shop:
        raise InvalidInputError(
            f"This shop has reached the limit of {settings.image_max_per_shop} kept photos.",
            field="image",
            code="image_limit_reached",
        )


def _keep(
    session: Session,
    ctx: RequestContext,
    product_id: int,
    data: bytes,
    info: image_validation.ImageInfo,
    store: ImageStore,
) -> ProductImage:
    existing = session.scalar(
        select(ProductImage).where(ProductImage.shop_id == ctx.shop_id, ProductImage.product_id == product_id)
    )
    key = storage_key(ctx.shop_id, info.sha256, info.extension)
    old_key = existing.storage_key if existing is not None else None
    if existing is None:
        existing = ProductImage(shop_id=ctx.shop_id, product_id=product_id, created_by=ctx.user_id)
        session.add(existing)
    existing.sha256, existing.content_type = info.sha256, info.content_type
    existing.size_bytes, existing.width, existing.height = info.size_bytes, info.width, info.height
    existing.storage_key = key
    session.flush()
    store.put(key, data)
    if old_key and old_key != key:
        store.delete(old_key)
    return existing


def attach_image(
    session: Session,
    ctx: RequestContext,
    product_id: int,
    data: bytes,
    *,
    declared_type: str | None = None,
    settings: Settings | None = None,
    store: ImageStore | None = None,
) -> ProductImage:
    """Keep a photo for an existing product (the user chose to). Changes no price, stock or product detail."""
    entitlement_service.require_feature(session, ctx.shop_id, FEATURE)
    settings = settings or get_settings()
    product_service.get_product_view(session, ctx.shop_id, product_id)  # 404 for another shop's product
    info = image_validation.inspect_image(
        data,
        declared_type=declared_type,
        max_bytes=settings.image_max_bytes,
        max_side=settings.image_max_side,
    )
    had = session.scalar(
        select(func.count())
        .select_from(ProductImage)
        .where(ProductImage.shop_id == ctx.shop_id, ProductImage.product_id == product_id)
    )
    if not had:
        _check_room(session, ctx.shop_id, settings)
    image = _keep(session, ctx, product_id, data, info, store or default_store(settings))
    record_audit(session, ctx, entity_type="product", entity_id=product_id, action="image_kept")
    return image


def get_image(
    session: Session,
    shop_id: int,
    product_id: int,
    *,
    store: ImageStore | None = None,
    settings: Settings | None = None,
) -> tuple[bytes, str]:
    row = session.scalar(
        select(ProductImage).where(ProductImage.shop_id == shop_id, ProductImage.product_id == product_id)
    )
    if row is None:
        raise NotFoundError("This product has no photo")
    data = (store or default_store(settings or get_settings())).get(row.storage_key)
    if data is None:
        raise NotFoundError("This product has no photo")
    return data, row.content_type


def delete_image(
    session: Session,
    ctx: RequestContext,
    product_id: int,
    *,
    store: ImageStore | None = None,
    settings: Settings | None = None,
) -> None:
    row = session.scalar(
        select(ProductImage).where(ProductImage.shop_id == ctx.shop_id, ProductImage.product_id == product_id)
    )
    if row is None:
        raise NotFoundError("This product has no photo")
    key = row.storage_key
    session.delete(row)
    session.flush()
    (store or default_store(settings or get_settings())).delete(key)
    record_audit(session, ctx, entity_type="product", entity_id=product_id, action="image_removed")
