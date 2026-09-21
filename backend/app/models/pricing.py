"""Price observations: what outside sources said a product costs, kept per shop.

Each row is one price one provider reported for one barcode when the shop asked (`checked_at`). Rows are only
ever added, so the table is both the shop's price-check history and its cache: the newest `checked_at` per
(barcode, provider) is served again without asking the provider while it is fresh, and as a fallback when the
provider is down. An observation is information for a person to read. Nothing here, and nothing that reads it,
ever changes a product's MRP, selling price, purchase price or average cost.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import Money, UTCDateTime
from app.models.base import Base, TimestampMixin, id_column, not_blank, positive, shop_id_column


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
