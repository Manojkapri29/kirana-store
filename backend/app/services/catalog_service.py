"""Units of measure (shared) and product categories (per shop)."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import Category, Unit
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError


def list_units(session: Session) -> list[Unit]:
    return list(session.scalars(select(Unit).order_by(Unit.id)))


def clean_name(name: str) -> str:
    return " ".join(name.split())


def get_category(session: Session, shop_id: int, category_id: int) -> Category:
    category = session.scalar(select(Category).where(Category.shop_id == shop_id, Category.id == category_id))
    if category is None:
        raise NotFoundError("Category not found")
    return category


def list_categories(session: Session, shop_id: int, *, active: bool | None = None) -> list[Category]:
    query = select(Category).where(Category.shop_id == shop_id)
    if active is not None:
        query = query.where(Category.is_active.is_(active))
    return list(session.scalars(query.order_by(func.lower(Category.name), Category.id)))


def _ensure_name_free(session: Session, shop_id: int, name: str, *, exclude_id: int | None = None) -> None:
    query = select(Category.id).where(Category.shop_id == shop_id, func.lower(Category.name) == name.lower())
    if exclude_id is not None:
        query = query.where(Category.id != exclude_id)
    if session.scalar(query) is not None:
        raise ConflictError(f"A category named '{name}' already exists.", field="name")


def create_category(session: Session, ctx: RequestContext, *, name: str) -> Category:
    name = clean_name(name)
    if not name:
        raise InvalidInputError("Enter a category name.", field="name")
    _ensure_name_free(session, ctx.shop_id, name)

    category = Category(shop_id=ctx.shop_id, name=name)
    session.add(category)
    try:
        session.flush()
    except IntegrityError as exc:
        raise ConflictError(f"A category named '{name}' already exists.", field="name") from exc
    record_audit(
        session, ctx, entity_type="category", entity_id=category.id, action="create", after={"name": name}
    )
    return category


def update_category(
    session: Session,
    ctx: RequestContext,
    category_id: int,
    *,
    name: str | None = None,
    is_active: bool | None = None,
) -> Category:
    category = get_category(session, ctx.shop_id, category_id)
    before: dict[str, object] = {}
    after: dict[str, object] = {}

    if name is not None:
        name = clean_name(name)
        if not name:
            raise InvalidInputError("Enter a category name.", field="name")
        if name != category.name:
            _ensure_name_free(session, ctx.shop_id, name, exclude_id=category.id)
            before["name"], after["name"] = category.name, name
            category.name = name
    if is_active is not None and is_active != category.is_active:
        before["is_active"], after["is_active"] = category.is_active, is_active
        category.is_active = is_active

    if after:
        session.flush()
        record_audit(
            session,
            ctx,
            entity_type="category",
            entity_id=category.id,
            action="update",
            before=before,
            after=after,
        )
    return category
