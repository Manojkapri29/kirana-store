"""Price observations: what outside sources said a product costs, kept per shop.

Each row is one price one provider reported for one barcode when the shop asked (`checked_at`). Rows are
only ever added, so the table is both the shop's price-check history and its cache: the newest `checked_at`
per (barcode, provider) is served again without asking the provider while it is fresh, and as a fallback
when the provider is down. An observation is information for a person to read. Nothing here, and nothing
that reads it, ever changes a product's MRP, selling price, purchase price or average cost.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import Money, UTCDateTime
from app.models.base import (
    Base,
    IdType,
    TimestampMixin,
    id_column,
    not_blank,
    positive,
    shop_id_column,
    tenant_fk,
)


class PriceObservation(TimestampMixin, Base):
    __tablename__ = "price_observations"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        Index("ix_price_observations_shop_barcode", "shop_id", "barcode", "provider", "checked_at"),
        not_blank("barcode"),
        not_blank("provider"),
        positive("price"),
        CheckConstraint("length(currency) = 3", name="currency_is_three_letters"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    barcode: Mapped[str] = mapped_column(String(50))  # as looked up (digits)
    provider: Mapped[str] = mapped_column(String(30))
    product_name: Mapped[str | None] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(100))
    pack_text: Mapped[str | None] = mapped_column(String(50))
    price: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))  # as reported; never converted
    location_text: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(80))
    source_url: Mapped[str | None] = mapped_column(String(500))
    observed_on: Mapped[date | None] = mapped_column(Date)  # when the source says it saw the price
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime)  # when we asked


class ProductImage(TimestampMixin, Base):
    """The one photo a shop chose to keep for a product. Metadata only: the file itself lives in the image
    store
    (a private folder now, object storage later) under `storage_key`, and is only ever served to the shop
    that owns it. Nothing creates a row from an analysis: the user has to confirm and ask to keep the
    photo."""

    __tablename__ = "product_images"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),
        UniqueConstraint("shop_id", "product_id"),  # at most one kept photo per product
        tenant_fk("product_id", "products"),
        tenant_fk("created_by", "users"),
        not_blank("storage_key"),
        positive("size_bytes"),
        positive("width"),
        positive("height"),
        CheckConstraint("length(sha256) = 64", name="sha256_is_64_chars"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    product_id: Mapped[int] = mapped_column(IdType)
    sha256: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(20))
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[int] = mapped_column(IdType)
