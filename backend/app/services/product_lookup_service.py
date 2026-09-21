"""Finding a product from what was typed or scanned: one reusable lookup for every screen that needs it.

A USB or Bluetooth scanner is a keyboard that types the code and presses Enter, so a scan and a typed entry
are the same thing here. Lookup order, first hit wins:

  1. exact barcode       (also with the leading zero a UPC-A / EAN-13 pair differs by)
  2. exact SKU           (SKUs are stored upper-case, so case does not matter)
  3. exact name          (ignoring case, punctuation and repeated spaces)
  4. search              (SKU, name, brand or barcode contains the text; active products only)

An exact barcode, SKU or name match returns the product even if it is inactive, so the screen can say "this
product is inactive" instead of "not found"; a search never returns an inactive product. Everything is
scoped to the caller's shop. A code nobody has gives "Barcode not found": lookup NEVER creates a product.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import Product
from app.services import entitlement_service, promotion_service
from app.services._filters import product_search_clause
from app.services.errors import InvalidInputError
from app.services.product_service import ProductView, views_where

FEATURE = "barcode_lookup"
MAX_CODE_LENGTH = 100
DEFAULT_LIMIT = 10
NOT_FOUND = "Barcode not found"
NOT_FOUND_TEXT = "No matching product"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")  # scanners may add Enter, Tab or other control characters
_NOT_WORD = re.compile(r"[\W_]+")  # anything that is not a letter or a digit


class MatchType(StrEnum):
    BARCODE = "BARCODE"
    SKU = "SKU"
    NAME = "NAME"
    SEARCH = "SEARCH"
    NONE = "NONE"


@dataclass(frozen=True)
class LookupResult:
    match_type: MatchType
    products: list[ProductView] = field(default_factory=list)
    offers: dict[int, promotion_service.ProductOffer] = field(default_factory=dict)
    message: str | None = None  # set when nothing was found
    code: str = ""  # what was looked up, cleaned

    @property
    def found(self) -> bool:
        return bool(self.products)


def clean_code(raw: str) -> str:
    """What a scanner or a person typed, without control characters or surrounding spaces."""
    return _CONTROL.sub("", raw).strip()


def normalize_name(value: str) -> str:
    """Name as compared for an exact match: no accents-variants, case, punctuation or repeated spaces."""
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(_NOT_WORD.sub(" ", folded).split())


# --- Pack sizes and names, for matching a name across sources --------------------------------------
_PACK = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(kg|g|gm|gms|gram|grams|mg|l|ltr|litre|liter|ml|pcs|pc|pack)\b", re.I
)
_TO_BASE = {
    "kg": ("g", Decimal(1000)), "g": ("g", Decimal(1)), "gm": ("g", Decimal(1)), "gms": ("g", Decimal(1)),
    "gram": ("g", Decimal(1)), "grams": ("g", Decimal(1)), "mg": ("g", Decimal("0.001")),
    "l": ("ml", Decimal(1000)), "ltr": ("ml", Decimal(1000)), "litre": ("ml", Decimal(1000)),
    "liter": ("ml", Decimal(1000)), "ml": ("ml", Decimal(1)),
    "pcs": ("pc", Decimal(1)), "pc": ("pc", Decimal(1)), "pack": ("pc", Decimal(1)),
}  # fmt: skip


def pack_size_of(*texts: str | None) -> tuple[Decimal, str] | None:
    """A pack size such as '1 kg' or '500g' as (amount in grams / millilitres / pieces, family)."""
    for text in texts:
        found = _PACK.search(text or "")
        if found:
            family, factor = _TO_BASE[found.group(2).lower()]
            return Decimal(found.group(1).replace(",", ".")) * factor, family
    return None


_NUMBER_UNIT = re.compile(r"(\d)\s+(kg|g|gm|gms|mg|l|ltr|ml|pcs|pc)\b")


def name_key(name: str | None) -> str:
    """A name for comparing: no case or punctuation, and '1 kg' written like '1kg'."""
    return _NUMBER_UNIT.sub(r"\1\2", normalize_name(name or ""))


def barcode_variants(code: str) -> list[str]:
    """The code itself, plus its UPC-A <-> EAN-13 twin (the same product with or without a leading zero)."""
    variants = [code]
    if code.isdigit():
        if len(code) == 12:
            variants.append("0" + code)
        elif len(code) == 13 and code.startswith("0"):
            variants.append(code[1:])
    return variants


def _looks_like_a_barcode(code: str) -> bool:
    return code.isdigit() and len(code) >= 6


def lookup(
    session: Session, shop_id: int, raw_code: str, *, limit: int = DEFAULT_LIMIT, enforce_plan: bool = True
) -> LookupResult:
    """Find products for a scanned or typed code, in the order above. Needs the plan's barcode feature, unless
    the caller has already checked the plan for the feature it is serving (`enforce_plan=False`)."""
    if enforce_plan:
        entitlement_service.require_feature(session, shop_id, FEATURE)
    code = clean_code(raw_code)
    if not code:
        raise InvalidInputError("Scan or type a barcode, SKU or product name.", field="code")
    if len(code) > MAX_CODE_LENGTH:
        raise InvalidInputError(f"That is too long (at most {MAX_CODE_LENGTH} characters).", field="code")

    def result(kind: MatchType, views: list[ProductView]) -> LookupResult:
        offers = promotion_service.product_offers(session, shop_id, [v.product for v in views])
        return LookupResult(kind, views, offers, code=code)

    in_shop = [Product.shop_id == shop_id]
    views = views_where(
        session, shop_id, [*in_shop, Product.barcode.in_(barcode_variants(code))], limit=limit
    )
    if views:
        return result(MatchType.BARCODE, views)
    views = views_where(session, shop_id, [*in_shop, Product.sku == code.upper()], limit=limit)
    if views:
        return result(MatchType.SKU, views)

    wanted = normalize_name(code)
    if wanted:
        # Narrow in SQL by one word of the name, then compare the normalised names exactly.
        first_word = wanted.split()[0]
        candidates = views_where(
            session,
            shop_id,
            [*in_shop, func.lower(Product.name).contains(first_word, autoescape=True)],
            limit=500,
        )
        exact = [v for v in candidates if normalize_name(v.product.name) == wanted][:limit]
        if exact:
            return result(MatchType.NAME, exact)

    views = views_where(
        session,
        shop_id,
        [*in_shop, Product.is_active.is_(True), or_(product_search_clause(code))],
        limit=limit,
    )
    if views:
        return result(MatchType.SEARCH, views)
    return LookupResult(
        MatchType.NONE, message=NOT_FOUND if _looks_like_a_barcode(code) else NOT_FOUND_TEXT, code=code
    )
