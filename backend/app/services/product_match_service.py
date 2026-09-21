"""Is this product already in the shop? Used before anything is created from a photo, a scan or an import.

Looked at in this order, and every hit says why:
  1. barcode      exact (a UPC-A and its EAN-13 twin are the same)      EXACT
  2. SKU          exact, ignoring case                                  EXACT
  3. name         same normalised name; with the same brand and pack    LIKELY
                  size it is the same product almost for certain
  4. similar      a close name, with no brand or the same brand         POSSIBLE
A different pack size is never counted as the same product. The result is only ever a warning for a person:
nothing is merged, changed or created here. Every query is scoped to the caller's shop.
"""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import IntEnum

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Product
from app.services.product_lookup_service import barcode_variants, name_key, pack_size_of
from app.services.product_service import ProductView, views_where

SIMILAR_THRESHOLD = 0.85
CANDIDATES = 300


class Strength(IntEnum):
    POSSIBLE = 1
    LIKELY = 2
    EXACT = 3

    @property
    def label(self) -> str:
        return self.name


@dataclass
class ProductMatch:
    view: ProductView
    strength: Strength
    reasons: list[str] = field(default_factory=list)


def find_possible_matches(
    session: Session,
    shop_id: int,
    *,
    barcode: str | None = None,
    sku: str | None = None,
    name: str | None = None,
    brand: str | None = None,
    pack_text: str | None = None,
    limit: int = 5,
) -> list[ProductMatch]:
    found: dict[int, ProductMatch] = {}

    def add(view: ProductView, strength: Strength, reason: str) -> None:
        match = found.setdefault(view.product.id, ProductMatch(view, strength))
        if strength > match.strength:
            match.strength = strength
        if reason not in match.reasons:
            match.reasons.append(reason)

    in_shop = [Product.shop_id == shop_id]
    if barcode and barcode.strip():
        for view in views_where(
            session, shop_id, [*in_shop, Product.barcode.in_(barcode_variants(barcode.strip()))]
        ):
            add(view, Strength.EXACT, "The same barcode")
    if sku and sku.strip():
        for view in views_where(session, shop_id, [*in_shop, Product.sku == sku.strip().upper()]):
            add(view, Strength.EXACT, "The same SKU")

    wanted = name_key(name)
    if wanted:
        first_word = wanted.split()[0]
        wanted_pack = pack_size_of(pack_text, name)
        wanted_brand = name_key(brand)
        candidates = views_where(
            session,
            shop_id,
            [*in_shop, func.lower(Product.name).contains(first_word, autoescape=True)],
            limit=CANDIDATES,
        )
        for view in candidates:
            product = view.product
            theirs = pack_size_of(product.name)
            if wanted_pack and theirs and wanted_pack != theirs:
                continue  # a different pack size is a different product
            same_brand = bool(wanted_brand and product.brand and name_key(product.brand) == wanted_brand)
            if name_key(product.name) == wanted:
                if same_brand and wanted_pack and theirs:
                    add(view, Strength.LIKELY, "The same name, brand and pack size")
                else:
                    add(view, Strength.LIKELY, "The same name")
                continue
            ratio = SequenceMatcher(None, wanted, name_key(product.name)).ratio()
            if ratio >= SIMILAR_THRESHOLD and (same_brand or not (wanted_brand and product.brand)):
                add(view, Strength.POSSIBLE, "A very similar name")
    ordered = sorted(
        found.values(), key=lambda m: (-m.strength, m.view.product.name.lower(), m.view.product.id)
    )
    return ordered[:limit]
