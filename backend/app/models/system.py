"""Cross-cutting tables: document numbering, idempotency and the audit log."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import UTCDateTime, utc_now
from app.models.base import (
    Base,
    CreatedAtMixin,
    IdType,
    id_column,
    non_negative,
    not_blank,
    shop_id_column,
    tenant_fk,
)


class DocumentSequence(Base):
    """Last number used per shop, document type and financial year (e.g. SALE / 2026-27).

    Incremented inside the transaction that creates the document. This is deliberately a table and not
    a database sequence: it works identically on SQLite and PostgreSQL and numbers restart per year.
    """

    __tablename__ = "document_sequences"
    __table_args__ = (
        UniqueConstraint("shop_id", "doc_type", "fiscal_year"),
        not_blank("doc_type"),
        not_blank("fiscal_year"),
        non_negative("last_number"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    doc_type: Mapped[str] = mapped_column(String(30))
    fiscal_year: Mapped[str] = mapped_column(String(9))  # "2026-27"
    last_number: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class IdempotencyKey(CreatedAtMixin, Base):
    """Remembers the outcome of a create request so a repeated submit (double tap, retry) returns the
    original result instead of creating a duplicate."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("shop_id", "key"),
        not_blank("key"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    key: Mapped[str] = mapped_column(String(100))
    operation: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_json: Mapped[Any | None] = mapped_column(JSON)


class AuditLog(CreatedAtMixin, Base):
    """Who changed what. INSERT-ONLY (a database trigger enforces it)."""

    __tablename__ = "audit_log"
    __table_args__ = (
        tenant_fk("user_id", "users"),
        Index("ix_audit_log_shop_entity", "shop_id", "entity_type", "entity_id"),
        Index("ix_audit_log_shop_created", "shop_id", "created_at"),
        not_blank("entity_type"),
        not_blank("action"),
    )

    id: Mapped[int] = id_column()
    shop_id: Mapped[int] = shop_id_column()
    user_id: Mapped[int | None] = mapped_column(IdType)  # NULL for system actions
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int | None] = mapped_column(IdType)
    action: Mapped[str] = mapped_column(String(50))
    # Generic JSON (not PostgreSQL JSONB) so the same column works on SQLite.
    before_json: Mapped[Any | None] = mapped_column(JSON)
    after_json: Mapped[Any | None] = mapped_column(JSON)
    request_id: Mapped[str | None] = mapped_column(
        String(64)
    )  # the request that caused it, to match server logs
