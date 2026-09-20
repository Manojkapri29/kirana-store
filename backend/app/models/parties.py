"""People and businesses the shop deals with: suppliers and customers."""

from sqlalchemy import Boolean, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.models.base import Base, TimestampMixin, id_column, not_blank, shop_id_column


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("shop_id", "id"), not_blank("name"))

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(String(500))
    gstin: Mapped[str | None] = mapped_column(String(15))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())


class Customer(TimestampMixin, Base):
    """A customer, mainly for khata (credit). The outstanding balance is never stored here; it is
    derived from `customer_ledger`."""

    __tablename__ = "customers"
    __table_args__ = (
        # Phone is optional, but when present it identifies the customer within the shop.
        UniqueConstraint("shop_id", "phone"),
        UniqueConstraint("shop_id", "id"),
        not_blank("name"),
        not_blank("phone"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
