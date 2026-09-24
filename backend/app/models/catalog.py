"""The product catalogue: units, categories and products."""

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.types import Money, Quantity
from app.models.base import (
    Base,
    IdType,
    TimestampMixin,
    id_column,
    non_negative,
    not_blank,
    shop_id_column,
    tenant_fk,
)


class Unit(Base):
    """Units of measure. Global (shared by all shops) and seeded by the first migration."""

    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("code"), not_blank("code"), not_blank("name"))

    id: Mapped[int] = id_column()
    code: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(50))
    # Whether quantities in this unit may be fractional (2.5 kg yes, 2.5 pieces no). Enforced by services.
    allows_decimal: Mapped[bool] = mapped_column(Boolean, server_default=expression.false())


class Category(TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("shop_id", "name"),
        UniqueConstraint("shop_id", "id"),
        not_blank("name"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())


class Product(TimestampMixin, Base):
    """A sellable item.

    There is deliberately NO stock column. Current stock is derived from `inventory_transactions`.
    Prices here are defaults and reference values; each document copies the price it actually used.
    """

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("shop_id", "sku"),
        # Uniqueness of a non-NULL barcode per shop. NULLs are never equal to each other in a UNIQUE
        # constraint (SQLite and PostgreSQL agree), so many products may have no barcode.
        UniqueConstraint("shop_id", "barcode"),
        UniqueConstraint("shop_id", "id"),
        tenant_fk("category_id", "categories"),
        tenant_fk("default_supplier_id", "suppliers"),
        Index("ix_products_shop_id_name", "shop_id", "name"),
        Index("ix_products_shop_id_category_id", "shop_id", "category_id"),
        Index("ix_products_shop_id_default_supplier_id", "shop_id", "default_supplier_id"),
        not_blank("sku"),
        not_blank("name"),
        not_blank("barcode"),  # NULL passes; an empty string does not
        non_negative("reorder_level"),
        non_negative("mrp"),
        non_negative("selling_price"),
        non_negative("purchase_price"),
        non_negative("avg_cost"),
        non_negative("pack_size"),
        non_negative("moq"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    sku: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(100))
    category_id: Mapped[int] = mapped_column(IdType)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    default_supplier_id: Mapped[int | None] = mapped_column(IdType)
    reorder_level: Mapped[Decimal] = mapped_column(Quantity, default=Decimal("0"))
    # MRP is separate from the selling price. Optional: loose goods have none.
    mrp: Mapped[Decimal | None] = mapped_column(Money)
    selling_price: Mapped[Decimal] = mapped_column(Money)
    # Unknown purchase price stays NULL, never 0 (BUSINESS_RULES C3).
    purchase_price: Mapped[Decimal | None] = mapped_column(Money)
    # Moving weighted average cost: the single deliberate cache, rebuildable from the ledger.
    avg_cost: Mapped[Decimal | None] = mapped_column(Money)
    barcode: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
    # Reorder planning (Phase 13): both optional. NULL means "not set" (assumed 1 / no minimum), never 0.
    pack_size: Mapped[Decimal | None] = mapped_column(Quantity)  # bought/sold in multiples of this many units
    moq: Mapped[Decimal | None] = mapped_column(Quantity)  # the supplier's minimum order quantity
