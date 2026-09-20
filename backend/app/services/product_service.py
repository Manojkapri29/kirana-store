"""Products: create, update, search, activate/deactivate.

Rules implemented here (see docs/BUSINESS_RULES.md):
  * SKU is unique per shop (stored upper-case, so 'sku-1' and 'SKU-1' are the same SKU);
  * a barcode is optional and unique per shop when present;
  * there is NO stock column: stock is always read from the inventory ledger via `inventory_service`;
  * missing prices stay NULL, never 0 (purchase price and MRP are optional);
  * `avg_cost` is not editable: it is maintained by stock movements (opening cost now, costing in Phase 5);
  * MRP is separate from the selling price; whether a selling price above MRP is a warning or an error is
    the shop's setting `mrp_validation_mode`;
  * products are never deleted, only deactivated.

Nothing here depends on the kind of business. A product is a name, a unit and prices, whether it is rice
counted in kilograms, a T-shirt counted in pieces or cloth counted in metres. The kind of business only
suggests defaults elsewhere (see the business-type module) and never limits what can be created.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.locale import CURRENCY_SYMBOL
from app.models import Category, Product, Supplier, Unit
from app.models.enums import MrpValidationMode
from app.services import inventory_service
from app.services._filters import product_search_clause
from app.services.audit_service import record_audit
from app.services.catalog_service import clean_name
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.inventory_service import StockStatus, stock_status
from app.services.shop_service import get_shop

AUDITED_FIELDS = (
    "sku", "name", "brand", "category_id", "unit_id", "default_supplier_id", "reorder_level",
    "mrp", "selling_price", "purchase_price", "avg_cost", "barcode", "is_active",
)  # fmt: skip
UPDATABLE_FIELDS = {
    "sku", "name", "brand", "category_id", "unit_id", "default_supplier_id", "reorder_level",
    "mrp", "selling_price", "purchase_price", "barcode",
}  # fmt: skip
REQUIRED_FIELDS = {"sku", "name", "category_id", "unit_id", "selling_price", "reorder_level"}


@dataclass(frozen=True)
class ProductView:
    """A product together with what the screens need: names, and stock derived from the ledger."""

    product: Product
    category_name: str
    unit: Unit
    current_stock: Decimal
    stock_status: StockStatus
    supplier_name: str | None = None  # the default supplier, if any


@dataclass
class SaveResult:
    view: ProductView
    warnings: list[str] = field(default_factory=list)


def _snapshot(product: Product) -> dict[str, Any]:
    return {name: getattr(product, name) for name in AUDITED_FIELDS}


# --- Reading -------------------------------------------------------------------------------------


def _select_with_names(shop_id: int) -> Any:
    return (
        select(Product, Category.name, Unit, Supplier.name)
        .join(Category, and_(Category.shop_id == Product.shop_id, Category.id == Product.category_id))
        .join(Unit, Unit.id == Product.unit_id)
        # A product need not have a supplier, so this join is optional. It matches on shop as well as id.
        .outerjoin(
            Supplier, and_(Supplier.shop_id == Product.shop_id, Supplier.id == Product.default_supplier_id)
        )
        .where(Product.shop_id == shop_id)
        # Reload from the database, so an object changed in this transaction shows exactly what is
        # stored (for example money as "0.00", not the "0" that was typed).
        .execution_options(populate_existing=True)
    )


def _to_views(session: Session, shop_id: int, rows: list[Any]) -> list[ProductView]:
    stock = inventory_service.get_stock_map(session, shop_id, [row[0].id for row in rows])
    return [
        ProductView(
            product=product,
            category_name=category_name,
            unit=unit,
            current_stock=stock[product.id],
            stock_status=stock_status(stock[product.id], product.reorder_level),
            supplier_name=supplier_name,
        )
        for product, category_name, unit, supplier_name in rows
    ]


def get_product_view(session: Session, shop_id: int, product_id: int) -> ProductView:
    row = session.execute(_select_with_names(shop_id).where(Product.id == product_id)).first()
    if row is None:
        raise NotFoundError("Product not found")  # also the answer for another shop's product
    return _to_views(session, shop_id, [tuple(row)])[0]


def list_products(
    session: Session,
    shop_id: int,
    *,
    q: str | None = None,
    barcode: str | None = None,
    category_id: int | None = None,
    unit_id: int | None = None,
    supplier_id: int | None = None,
    active: bool | None = True,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[ProductView], int]:
    conditions: list[ColumnElement[bool]] = []
    if q and q.strip():
        conditions.append(product_search_clause(q))
    if barcode and barcode.strip():
        conditions.append(Product.barcode == barcode.strip())
    if category_id is not None:
        conditions.append(Product.category_id == category_id)
    if unit_id is not None:
        conditions.append(Product.unit_id == unit_id)
    if supplier_id is not None:
        conditions.append(Product.default_supplier_id == supplier_id)
    if active is not None:
        conditions.append(Product.is_active.is_(active))

    total = session.scalar(
        select(func.count()).select_from(Product).where(Product.shop_id == shop_id, *conditions)
    )
    query = (
        _select_with_names(shop_id)
        .where(*conditions)
        .order_by(func.lower(Product.name), Product.id)
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    rows = [tuple(row) for row in session.execute(query)]
    return _to_views(session, shop_id, rows), total


# --- Validation helpers --------------------------------------------------------------------------


def _normalize(changes: dict[str, Any]) -> dict[str, Any]:
    out = dict(changes)
    if "sku" in out:
        out["sku"] = out["sku"].strip().upper()
        if not out["sku"]:
            raise InvalidInputError("Enter a SKU.", field="sku")
    if "name" in out:
        out["name"] = clean_name(out["name"])
        if not out["name"]:
            raise InvalidInputError("Enter a product name.", field="name")
    for key in ("brand", "barcode"):
        if key in out and out[key] is not None:
            out[key] = out[key].strip() or None
    return out


def _require_category(session: Session, shop_id: int, category_id: int, *, keep: int | None = None) -> None:
    category = session.scalar(select(Category).where(Category.shop_id == shop_id, Category.id == category_id))
    if category is None:
        raise InvalidInputError("Choose an existing category.", field="category_id")
    if not category.is_active and category_id != keep:
        raise InvalidInputError("This category is inactive.", field="category_id")


def _require_unit(session: Session, unit_id: int) -> Unit:
    unit = session.get(Unit, unit_id)
    if unit is None:
        raise InvalidInputError("Choose a unit.", field="unit_id")
    return unit


def _require_supplier(session: Session, shop_id: int, supplier_id: int, *, keep: int | None = None) -> None:
    """The default supplier must belong to this shop and be active. An inactive supplier that the product
    already uses may stay (`keep`), so editing other details of such a product is never blocked."""
    supplier = session.scalar(select(Supplier).where(Supplier.shop_id == shop_id, Supplier.id == supplier_id))
    if supplier is None:
        raise InvalidInputError("Choose an existing supplier.", field="default_supplier_id")
    if not supplier.is_active and supplier_id != keep:
        raise InvalidInputError("This supplier is inactive.", field="default_supplier_id")


def _ensure_unique(
    session: Session, shop_id: int, *, sku: str | None, barcode: str | None, exclude_id: int | None = None
) -> None:
    for column, value, label in ((Product.sku, sku, "SKU"), (Product.barcode, barcode, "barcode")):
        if value is None:
            continue
        query = select(Product.name).where(Product.shop_id == shop_id, column == value)
        if exclude_id is not None:
            query = query.where(Product.id != exclude_id)
        owner = session.scalar(query)
        if owner is not None:
            raise ConflictError(f"The {label} '{value}' is already used by '{owner}'.", field=column.key)


def _mrp_check(mode: MrpValidationMode, mrp: Decimal | None, selling_price: Decimal) -> list[str]:
    """Selling above MRP: a warning or an error, depending on the shop's setting (BUSINESS_RULES P2)."""
    if mrp is None or selling_price <= mrp:
        return []
    message = (
        f"Selling price {CURRENCY_SYMBOL}{selling_price:.2f} is above the MRP {CURRENCY_SYMBOL}{mrp:.2f}."
    )
    if mode is MrpValidationMode.BLOCK:
        raise InvalidInputError(
            message + " This shop does not allow selling above MRP.", field="selling_price"
        )
    return [message]


def _flush_or_conflict(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:  # a concurrent duplicate that slipped past the pre-checks
        text = str(exc.orig).lower()
        field_name = "barcode" if "barcode" in text else "sku" if "sku" in text else None
        raise ConflictError("Another product already uses this SKU or barcode.", field=field_name) from exc


# --- Writing -------------------------------------------------------------------------------------


def create_product(session: Session, ctx: RequestContext, data: dict[str, Any]) -> SaveResult:
    """Create a product, optionally with its opening stock, in the caller's single transaction."""
    data = _normalize(data)
    shop = get_shop(session, ctx.shop_id)
    opening_stock: Decimal | None = data.pop("opening_stock", None)
    opening_cost: Decimal | None = data.pop("opening_stock_cost", None)

    _require_category(session, ctx.shop_id, data["category_id"])
    unit = _require_unit(session, data["unit_id"])
    if data.get("default_supplier_id") is not None:
        _require_supplier(session, ctx.shop_id, data["default_supplier_id"])
    inventory_service.validate_quantity_for_unit(data["reorder_level"], unit, field="reorder_level")
    _ensure_unique(session, ctx.shop_id, sku=data["sku"], barcode=data.get("barcode"))
    warnings = _mrp_check(shop.mrp_validation_mode, data.get("mrp"), data["selling_price"])
    if opening_cost is not None and not opening_stock:
        raise InvalidInputError("Enter the opening stock quantity for this cost.", field="opening_stock")

    product = Product(shop_id=ctx.shop_id, **{key: data.get(key) for key in UPDATABLE_FIELDS if key in data})
    session.add(product)
    _flush_or_conflict(session)
    session.refresh(product)
    record_audit(
        session, ctx, entity_type="product", entity_id=product.id, action="create", after=_snapshot(product)
    )

    if opening_stock:  # None or 0 means "no opening stock"
        inventory_service.record_opening_stock(
            session, ctx, product_id=product.id, quantity=opening_stock, unit_cost=opening_cost
        )
    return SaveResult(get_product_view(session, ctx.shop_id, product.id), warnings)


def update_product(
    session: Session, ctx: RequestContext, product_id: int, changes: dict[str, Any]
) -> SaveResult:
    """Change some fields of a product. Only fields present in `changes` are touched."""
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise InvalidInputError(f"These fields cannot be changed: {', '.join(sorted(unknown))}.")
    for key in REQUIRED_FIELDS & set(changes):
        if changes[key] is None:
            raise InvalidInputError("This field is required.", field=key)

    product = session.scalar(
        select(Product).where(Product.shop_id == ctx.shop_id, Product.id == product_id).with_for_update()
    )
    if product is None:
        raise NotFoundError("Product not found")
    shop = get_shop(session, ctx.shop_id)
    changes = _normalize(changes)
    changes = {key: value for key, value in changes.items() if getattr(product, key) != value}
    warnings: list[str] = []

    if changes:
        unit = session.get(Unit, changes.get("unit_id", product.unit_id))
        if "category_id" in changes:
            _require_category(session, ctx.shop_id, changes["category_id"])
        if "unit_id" in changes:
            unit = _require_unit(session, changes["unit_id"])
            if inventory_service.count_movements(session, ctx.shop_id, product.id):
                raise ConflictError(
                    "The unit cannot be changed after the product has stock movements.", field="unit_id"
                )
        if changes.get("default_supplier_id") is not None:
            _require_supplier(
                session, ctx.shop_id, changes["default_supplier_id"], keep=product.default_supplier_id
            )
        if "reorder_level" in changes or "unit_id" in changes:
            inventory_service.validate_quantity_for_unit(
                changes.get("reorder_level", product.reorder_level), unit, field="reorder_level"
            )
        _ensure_unique(
            session,
            ctx.shop_id,
            sku=changes.get("sku"),
            barcode=changes.get("barcode"),
            exclude_id=product.id,
        )
        if "mrp" in changes or "selling_price" in changes:
            warnings = _mrp_check(
                shop.mrp_validation_mode,
                changes["mrp"] if "mrp" in changes else product.mrp,
                changes["selling_price"] if "selling_price" in changes else product.selling_price,
            )

        before = {key: getattr(product, key) for key in changes}
        for key, value in changes.items():
            setattr(product, key, value)
        _flush_or_conflict(session)
        session.refresh(product)
        record_audit(
            session, ctx, entity_type="product", entity_id=product.id, action="update",
            before=before, after={key: getattr(product, key) for key in changes},
        )  # fmt: skip
    return SaveResult(get_product_view(session, ctx.shop_id, product.id), warnings)


def set_product_active(
    session: Session, ctx: RequestContext, product_id: int, *, active: bool
) -> ProductView:
    """Activate or deactivate. Deactivating hides the product from default lists and blocks new stock
    movements; its history is untouched. Repeating the same call changes nothing."""
    product = session.scalar(
        select(Product).where(Product.shop_id == ctx.shop_id, Product.id == product_id).with_for_update()
    )
    if product is None:
        raise NotFoundError("Product not found")
    if product.is_active != active:
        product.is_active = active
        session.flush()
        record_audit(
            session, ctx, entity_type="product", entity_id=product.id,
            action="activate" if active else "deactivate",
            before={"is_active": not active}, after={"is_active": active},
        )  # fmt: skip
    return get_product_view(session, ctx.shop_id, product.id)
