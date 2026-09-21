"""Tenancy: shops and their users."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.types import UTCDateTime
from app.models.base import (
    Base,
    TimestampMixin,
    enum_type,
    id_column,
    not_blank,
    shop_id_column,
)
from app.models.enums import AccountStatus, Language, MrpValidationMode, UserRole


class BusinessType(Base):
    """A kind of business a shop can be: grocery, bakery, garments, and so on.

    Reference data shared by all shops. Adding a new kind of business is an INSERT (a data migration), not
    a schema or code change: the identifier is just a row here. What a type *suggests* (categories, units)
    lives in `app.services.business_type_service`. A business type only ever provides defaults; it never
    restricts what a shop may sell.
    """

    __tablename__ = "business_types"
    __table_args__ = (not_blank("code"), not_blank("name"))

    code: Mapped[str] = mapped_column(String(30), primary_key=True)  # e.g. "GROCERY"
    name: Mapped[str] = mapped_column(String(100))  # English display name; screens translate by code
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())


class Shop(TimestampMixin, Base):
    """One business (a shop, stall or vendor). Every other business table points back here via `shop_id`.

    `name` is the business name. `business_type` says what kind of business it is; it drives defaults only.
    """

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
    business_type: Mapped[str] = mapped_column(String(30), ForeignKey("business_types.code"))
    gstin: Mapped[str | None] = mapped_column(String(15))
    upi_id: Mapped[str | None] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", server_default="Asia/Kolkata")
    language: Mapped[Language] = mapped_column(
        enum_type(Language, "language"),
        default=Language.EN,
        server_default=Language.EN.value,
    )
    # SaaS account state. Changing it never deletes anything; it only decides what the shop may do.
    account_status: Mapped[AccountStatus] = mapped_column(
        enum_type(AccountStatus, "account_status"),
        default=AccountStatus.ACTIVE,
        server_default=AccountStatus.ACTIVE.value,
    )
    status_reason: Mapped[str | None] = mapped_column(String(300))
    status_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
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
