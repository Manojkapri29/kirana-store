"""Business types: what kind of business a shop is, and the defaults that suggests.

**A business type provides suggestions, never restrictions.** A grocery shop can still sell flowers, a fruit
vendor can still sell bottled water, and any shop can use any unit. Nothing in the product, inventory,
purchase or sales logic looks at the business type; only this module and the screens that offer defaults do.

Two places, on purpose:
  * the `business_types` table says which types exist (so a shop cannot have an invalid one, and adding a
    type is a data migration, not a code change);
  * `TEMPLATES` below says what each type suggests. A type without an entry simply suggests nothing, so a
    newly inserted type works immediately and gets a template when someone writes one.

The template is deliberately small (suggested categories and units). It is the seam where business-specific
defaults will plug in later (dashboard labels, product templates, report layouts, storefront look) without
touching the core engine. Specialised workflows such as recipes or IMEI tracking are future modules, not part
of this table or of the core.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import BusinessType, Category, Shop
from app.services.audit_service import record_audit
from app.services.errors import InvalidInputError, NotFoundError
from app.services.shop_service import get_shop


@dataclass(frozen=True)
class BusinessTemplate:
    """Suggested defaults for one business type. Every field is optional and only a suggestion."""

    categories: tuple[str, ...] = ()
    # Unit codes (see the `units` table) shown first when choosing a unit. Empty = no preference.
    unit_codes: tuple[str, ...] = ()


TEMPLATES: dict[str, BusinessTemplate] = {
    "GROCERY": BusinessTemplate(
        categories=(
            "Rice & Grains",
            "Flour & Pulses",
            "Oil & Spices",
            "Snacks",
            "Beverages",
            "Personal Care",
            "Household",
        ),
        unit_codes=("kg", "g", "L", "ml", "pkt", "pcs", "box", "doz"),
    ),
    "GENERAL_STORE": BusinessTemplate(
        categories=("Groceries", "Household", "Stationery", "Personal Care", "Snacks & Beverages"),
        unit_codes=("pcs", "kg", "g", "L", "ml", "pkt", "box", "doz", "pair"),
    ),
    "SWEET_SHOP": BusinessTemplate(
        categories=("Sweets", "Namkeen", "Snacks", "Beverages"),
        unit_codes=("kg", "g", "pcs", "box", "pkt"),
    ),
    "BAKERY": BusinessTemplate(
        categories=("Bread", "Cakes & Pastries", "Biscuits & Cookies", "Snacks", "Beverages"),
        unit_codes=("pcs", "kg", "box", "tray", "pkt"),
    ),
    "DAIRY": BusinessTemplate(
        categories=("Milk", "Curd & Yogurt", "Paneer & Cheese", "Butter & Ghee", "Ice Cream"),
        unit_codes=("L", "ml", "kg", "g", "pkt", "pcs", "btl"),
    ),
    "FRUIT": BusinessTemplate(
        categories=("Fruits", "Exotic Fruits", "Dry Fruits", "Juices"),
        unit_codes=("kg", "g", "pcs", "doz", "box", "tray"),
    ),
    "VEGETABLE": BusinessTemplate(
        categories=("Vegetables", "Leafy Vegetables", "Herbs & Greens", "Roots & Tubers"),
        unit_codes=("kg", "g", "pcs", "doz", "pkt"),
    ),
    "MEAT_FOOD": BusinessTemplate(
        categories=("Chicken", "Mutton", "Fish & Seafood", "Eggs", "Ready to Cook"),
        unit_codes=("kg", "g", "pcs", "doz", "pkt", "tray"),
    ),
    "GARMENTS": BusinessTemplate(
        categories=("Men's Wear", "Women's Wear", "Kids' Wear", "Innerwear", "Accessories"),
        unit_codes=("pcs", "pair", "box", "m"),
    ),
    "FOOTWEAR": BusinessTemplate(
        categories=(
            "Men's Footwear",
            "Women's Footwear",
            "Kids' Footwear",
            "Sports Shoes",
            "Sandals & Slippers",
        ),
        unit_codes=("pair", "pcs", "box"),
    ),
    "COSMETICS": BusinessTemplate(
        categories=("Skin Care", "Hair Care", "Makeup", "Fragrances", "Personal Care"),
        unit_codes=("pcs", "btl", "ml", "box", "pkt"),
    ),
    "ELECTRONICS": BusinessTemplate(
        categories=("Mobiles", "Accessories", "Chargers & Cables", "Audio", "Appliances"),
        unit_codes=("pcs", "box", "pair"),
    ),
    "HARDWARE": BusinessTemplate(
        categories=("Tools", "Electrical", "Plumbing", "Paint", "Fasteners", "Building Material"),
        unit_codes=("pcs", "kg", "m", "box", "pkt", "L"),
    ),
    "STATIONERY": BusinessTemplate(
        categories=(
            "Notebooks & Paper",
            "Pens & Pencils",
            "Art Supplies",
            "Office Supplies",
            "School Supplies",
        ),
        unit_codes=("pcs", "pkt", "box", "doz", "pair"),
    ),
    "OTHER": BusinessTemplate(),  # no suggestions: the owner defines everything
}

NO_TEMPLATE = BusinessTemplate()


def get_template(business_type: str) -> BusinessTemplate:
    """The suggestions for a type. Unknown or newly added types suggest nothing rather than failing."""
    return TEMPLATES.get(business_type, NO_TEMPLATE)


def list_business_types(session: Session, *, active_only: bool = True) -> list[BusinessType]:
    query = select(BusinessType).order_by(BusinessType.sort_order, BusinessType.code)
    if active_only:
        query = query.where(BusinessType.is_active.is_(True))
    return list(session.scalars(query))


def get_business_type(session: Session, code: str) -> BusinessType | None:
    return session.get(BusinessType, code)


def set_shop_business_type(session: Session, ctx: RequestContext, code: str) -> Shop:
    """Change the shop's kind of business. Only defaults change; no product, unit or stock is touched."""
    code = code.strip().upper()
    business_type = get_business_type(session, code)
    if business_type is None or not business_type.is_active:
        raise InvalidInputError("Choose a business type from the list.", field="business_type")

    shop = get_shop(session, ctx.shop_id)
    if shop.business_type != code:
        before = shop.business_type
        shop.business_type = code
        session.flush()
        record_audit(
            session,
            ctx,
            entity_type="shop",
            entity_id=shop.id,
            action="update",
            before={"business_type": before},
            after={"business_type": code},
        )
    return shop


@dataclass(frozen=True)
class SuggestedCategory:
    name: str
    exists: bool  # the shop already has a category with this name (ignoring case)


@dataclass(frozen=True)
class ShopTemplateView:
    business_type: str
    business_type_name: str
    categories: list[SuggestedCategory]
    unit_codes: tuple[str, ...]


def shop_template(session: Session, shop_id: int) -> ShopTemplateView:
    """What the shop's business type suggests, marking suggested categories the shop already has."""
    shop = get_shop(session, shop_id)
    business_type = get_business_type(session, shop.business_type)
    if business_type is None:  # cannot happen (foreign key), but never crash a screen over a label
        raise NotFoundError("Business type not found")
    template = get_template(shop.business_type)

    existing = {
        name.lower()
        for name in session.scalars(select(func.lower(Category.name)).where(Category.shop_id == shop_id))
    }
    return ShopTemplateView(
        business_type=business_type.code,
        business_type_name=business_type.name,
        categories=[
            SuggestedCategory(name=name, exists=name.lower() in existing) for name in template.categories
        ],
        unit_codes=template.unit_codes,
    )
