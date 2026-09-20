"""Query fragments shared by services. Portable SQL only (no ILIKE, no dialect functions)."""

from sqlalchemy import ColumnElement, func, or_

from app.models import Product


def product_search_clause(text: str) -> ColumnElement[bool]:
    """Match a search text against SKU, name, brand and barcode (case-insensitive substring).

    Barcode scanners type the code like a keyboard, so a scanned barcode is just a search text.
    `autoescape` makes `%` and `_` in the text literal instead of wildcards.
    """
    needle = text.strip().lower()
    return or_(
        func.lower(Product.sku).contains(needle, autoescape=True),
        func.lower(Product.name).contains(needle, autoescape=True),
        func.lower(func.coalesce(Product.brand, "")).contains(needle, autoescape=True),
        func.lower(func.coalesce(Product.barcode, "")).contains(needle, autoescape=True),
    )
