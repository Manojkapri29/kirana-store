"""People and businesses the shop deals with: suppliers and customers."""

from sqlalchemy import JSON, Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.models.base import (
    Base,
    CheckConstraint,
    TimestampMixin,
    enum_type,
    id_column,
    not_blank,
    shop_id_column,
    tenant_fk,
)
from app.models.enums import CustomerSource, CustomerType, NotificationChannel


class Supplier(TimestampMixin, Base):
    """Someone the shop buys from. Generic: the same for every kind of business.

    Only the name is required. Contact details are optional and validated by `supplier_service`.
    Phone numbers are stored in a compact form (`+919876543210`, `9876543210`) and email in lower case.
    A supplier is never deleted, only deactivated, so purchases made from it (Phase 5) stay explainable.
    """

    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("shop_id", "id"),  # target of tenant foreign keys (products.default_supplier_id)
        Index("ix_suppliers_shop_id_name", "shop_id", "name"),
        not_blank("name"),
        CheckConstraint("lead_time_days IS NULL OR lead_time_days > 0", name="lead_time_days_positive"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    alternate_phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    address: Mapped[str | None] = mapped_column(String(500))
    gstin: Mapped[str | None] = mapped_column(String(15))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
    # Reorder planning (Phase 13): how many days delivery usually takes. NULL means the shop has not set one;
    # a calculation that needs it then says so explicitly rather than assuming a number.
    lead_time_days: Mapped[int | None] = mapped_column(Integer)


class Customer(TimestampMixin, Base):
    """Someone the shop sells to, mainly on credit (khata). Generic: the same for every kind of business.

    The outstanding balance is never stored here: it is the sum of the customer's `customer_ledger` rows,
    and only `khata_service` reads or writes that ledger. A customer is never deleted, only deactivated, so
    the ledger stays explainable.

    Phone is optional; when present it is unique **within the shop** (never across shops), and is stored in
    the compact form produced by `contact_validation`. Only the name is required.

    Phase 14 (CRM) adds classification fields (`customer_type`, `source`, `tags`) that are facts a person
    records, never computed segments (segments are worked out on demand by `customer_intelligence_service`),
    marketing consent (opt-in, per channel, defaulting to False: a customer is never opted in by default),
    and `referred_by_customer_id` for the referral system. None of this is a second customer table.
    """

    __tablename__ = "customers"
    __table_args__ = (
        # Phone is optional, but when present it identifies the customer within the shop.
        UniqueConstraint("shop_id", "phone"),
        UniqueConstraint("shop_id", "id"),
        Index("ix_customers_shop_id_name", "shop_id", "name"),
        Index("ix_customers_shop_id_referred_by", "shop_id", "referred_by_customer_id"),
        tenant_fk("referred_by_customer_id", "customers"),
        not_blank("name"),
        not_blank("phone"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    address: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=expression.true())
    # CRM (Phase 14): facts a person records, never a computed segment.
    customer_type: Mapped[CustomerType | None] = mapped_column(enum_type(CustomerType, "customer_type"))
    source: Mapped[CustomerSource | None] = mapped_column(enum_type(CustomerSource, "customer_source"))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    preferred_contact_channel: Mapped[NotificationChannel | None] = mapped_column(
        enum_type(NotificationChannel, "preferred_contact_channel")
    )
    # Marketing consent, per channel, separate from transactional notifications (which are shop-staff-only
    # and unaffected by this). Defaults to False: a customer is never opted in without asking.
    marketing_opt_in_email: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    marketing_opt_in_sms: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    marketing_opt_in_whatsapp: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    marketing_opt_in_push: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Referral (Phase 14): who referred this customer, if anyone. Set once, at creation.
    referred_by_customer_id: Mapped[int | None] = mapped_column(Integer)
