"""Tenancy: shops and their users."""

from sqlalchemy import Boolean, CheckConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.models.base import (
    Base,
    TimestampMixin,
    enum_type,
    id_column,
    not_blank,
    shop_id_column,
)
from app.models.enums import Language, MrpValidationMode, UserRole


class Shop(TimestampMixin, Base):
    """One kirana store. Every other business table points back here through `shop_id`."""

    __tablename__ = "shops"
    __table_args__ = (
        not_blank("name"),
        not_blank("phone"),
        not_blank("address"),
        not_blank("timezone"),
    )

    id: Mapped[int] = id_column()
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(String(500))
    gstin: Mapped[str | None] = mapped_column(String(15))
    upi_id: Mapped[str | None] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", server_default="Asia/Kolkata")
    language: Mapped[Language] = mapped_column(
        enum_type(Language, "language"),
        default=Language.EN,
        server_default=Language.EN.value,
    )
    allow_negative_stock: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=expression.false()
    )
    # Warn or block when a selling price exceeds MRP. No server default on purpose: which mode is the
    # default for new shops is decided in Phase 3 (BUSINESS_RULES P2), so it can change without a migration.
    mrp_validation_mode: Mapped[MrpValidationMode] = mapped_column(
        enum_type(MrpValidationMode, "mrp_validation_mode"),
        default=MrpValidationMode.WARN,
    )


class User(TimestampMixin, Base):
    """A person who can log in to one shop. Authentication itself arrives in Phase 14."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email"),
        UniqueConstraint("shop_id", "id"),  # target of tenant foreign keys (created_by)
        not_blank("full_name"),
        # Emails are stored lower-case so uniqueness is case-insensitive without dialect-specific SQL.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        not_blank("email"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(enum_type(UserRole, "role"), default=UserRole.OWNER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
