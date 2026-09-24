"""CRM foundation: classification/consent on the existing `Customer` row (no second customer table), an
insert-only note timeline, and a combined activity timeline built from every existing record of what a
customer did — sales, quick sales, khata entries, returns, promotion usage, loyalty and referral events,
notes and tasks. Nothing here is a new source of truth: every figure comes from the service that already owns
it (`khata_service`, `loyalty_service`, `customer_intelligence_service`), and the timeline is read-only.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import (
    BusinessTask,
    Customer,
    CustomerNote,
    QuickSale,
    ReferralEvent,
    Sale,
    SalePromotion,
    SalesReturn,
)
from app.models.enums import SaleStatus
from app.services import customer_intelligence_service, customer_service, khata_service, loyalty_service
from app.services.audit_service import record_audit
from app.services.errors import InvalidInputError
from app.services.shop_service import get_shop


@dataclass(frozen=True)
class TimelineEvent:
    kind: str
    occurred_at: datetime
    title: str
    detail: str
    amount: Decimal | None
    reference_type: str | None
    reference_id: int | None


@dataclass(frozen=True)
class CustomerProfile:
    """Everything the profile page needs, in one call: the customer row, its analytics (Phase 13), loyalty
    balance and referral summary. Every count here is real; nothing is fabricated."""

    customer: Customer
    analytics: customer_intelligence_service.CustomerAnalytics
    loyalty_balance: int
    loyalty_program_active: bool
    referrals_made: int
    referral_code: str | None


def _at_midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


# --- Classification and consent -------------------------------------------------------------------------


def update_classification(
    session: Session, ctx: RequestContext, customer_id: int, changes: dict[str, Any]
) -> Customer:
    """Change classification (`customer_type`, `source`, `tags`) and/or communication consent. A thin,
    validating wrapper over `customer_service.update_customer`, which already audits before/after per field —
    including consent changes, satisfying the "record consent changes" rule without a second audit path."""
    unknown = set(changes) - customer_service.CRM_FIELDS
    if unknown:
        raise InvalidInputError(f"These fields are not CRM fields: {', '.join(sorted(unknown))}.")
    result = customer_service.update_customer(session, ctx, customer_id, changes)
    return result.customer


# --- Notes ------------------------------------------------------------------------------------------------


def add_note(session: Session, ctx: RequestContext, customer_id: int, body: str) -> CustomerNote:
    if not body.strip():
        raise InvalidInputError("Write something first.", field="body")
    customer_service.get_customer(session, ctx.shop_id, customer_id)
    note = CustomerNote(shop_id=ctx.shop_id, customer_id=customer_id, user_id=ctx.user_id, body=body.strip())
    session.add(note)
    session.flush()
    record_audit(session, ctx, entity_type="customer", entity_id=customer_id, action="customer_note_added")
    return note


def list_notes(
    session: Session, shop_id: int, customer_id: int, *, limit: int | None = 50, offset: int = 0
) -> list[CustomerNote]:
    customer_service.get_customer(session, shop_id, customer_id)
    query = (
        select(CustomerNote)
        .where(CustomerNote.shop_id == shop_id, CustomerNote.customer_id == customer_id)
        .order_by(CustomerNote.id.desc())
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query))


# --- Profile ------------------------------------------------------------------------------------------------


def get_profile(session: Session, shop_id: int, customer_id: int, today: date) -> CustomerProfile:
    customer = customer_service.get_customer(session, shop_id, customer_id)
    analytics = customer_intelligence_service.analytics_for(session, shop_id, customer_id, today)
    from app.services import referral_service  # local import: avoids a circular import at module load time

    program = loyalty_service.get_program(session, shop_id)
    referrals_made = referral_service.count_referrals(session, shop_id, customer_id)
    code = referral_service.code_for(session, shop_id, customer_id)
    return CustomerProfile(
        customer=customer,
        analytics=analytics,
        loyalty_balance=loyalty_service.get_balance(session, shop_id, customer_id),
        loyalty_program_active=bool(program and program.is_active),
        referrals_made=referrals_made,
        referral_code=code.code if code else None,
    )


# --- Timeline ------------------------------------------------------------------------------------------


def get_timeline(
    session: Session, shop_id: int, customer_id: int, *, limit: int = 200
) -> list[TimelineEvent]:
    """Every recorded event touching this customer, newest first. Read-only: nothing here is written.
    Combines, in one pass: sales, quick sales, khata entries (payments, credit, adjustments, reversals),
    returns, promotion usage, loyalty events, referral events, notes and tasks."""
    customer_service.get_customer(session, shop_id, customer_id)  # 404 for another shop's customer
    events: list[TimelineEvent] = []

    for sale in session.scalars(
        select(Sale).where(
            Sale.shop_id == shop_id, Sale.customer_id == customer_id, Sale.status != SaleStatus.DRAFT
        )
    ):
        events.append(
            TimelineEvent(
                "sale", _at_midnight(sale.sale_date), f"Sale {sale.invoice_no}",
                f"{sale.status.value.title()} — total {sale.total_amount}",
                sale.total_amount, "SALE", sale.id,
            )
        )  # fmt: skip

    for qs in session.scalars(
        select(QuickSale).where(
            QuickSale.shop_id == shop_id,
            QuickSale.customer_id == customer_id,
            QuickSale.status != SaleStatus.DRAFT,
        )
    ):
        events.append(
            TimelineEvent(
                "quick_sale", _at_midnight(qs.sale_date), f"Quick sale {qs.quick_no}",
                f"{qs.status.value.title()} — total {qs.total_amount}",
                qs.total_amount, "QUICK_SALE", qs.id,
            )
        )  # fmt: skip

    ledger_rows, _ = khata_service.get_customer_ledger(session, shop_id, customer_id, limit=None)
    khata_titles = {
        "OPENING_BALANCE": "Opening balance", "CREDIT_SALE": "Credit sale", "PAYMENT": "Payment received",
        "RETURN_CREDIT": "Return credited", "ADJUSTMENT": "Khata adjustment", "REVERSAL": "Khata reversal",
    }  # fmt: skip
    for row in ledger_rows:
        events.append(
            TimelineEvent(
                "khata", _at_midnight(row.entry_date), khata_titles.get(row.entry_type.value, "Khata entry"),
                row.note or (row.reference_no or ""), abs(row.amount_delta), "KHATA_ENTRY", row.id,
            )
        )  # fmt: skip

    returns = session.execute(
        select(SalesReturn, Sale.invoice_no)
        .join(Sale, (Sale.shop_id == SalesReturn.shop_id) & (Sale.id == SalesReturn.sale_id))
        .where(SalesReturn.shop_id == shop_id, Sale.customer_id == customer_id)
    ).all()
    for ret, invoice_no in returns:
        events.append(
            TimelineEvent(
                "return", _at_midnight(ret.return_date), f"Return {ret.return_no}",
                f"Against sale {invoice_no} — refunded {ret.total_refund}",
                ret.total_refund, "SALES_RETURN", ret.id,
            )
        )  # fmt: skip

    promotions = session.execute(
        select(SalePromotion, Sale.invoice_no, Sale.sale_date)
        .join(Sale, (Sale.shop_id == SalePromotion.shop_id) & (Sale.id == SalePromotion.sale_id))
        .where(SalePromotion.shop_id == shop_id, Sale.customer_id == customer_id)
    ).all()
    for promo, invoice_no, sale_date in promotions:
        events.append(
            TimelineEvent(
                "promotion_usage", _at_midnight(sale_date), f"Used offer: {promo.name}",
                f"{promo.terms} on sale {invoice_no}", promo.discount_amount, "SALE_PROMOTION", promo.id,
            )
        )  # fmt: skip

    loyalty_rows, _ = loyalty_service.list_ledger(session, shop_id, customer_id, limit=None)
    loyalty_titles = {
        "EARN": "Loyalty points earned", "REDEEM": "Loyalty points redeemed", "ADJUST": "Loyalty adjustment",
        "EXPIRE": "Loyalty points expired", "REVERSAL": "Loyalty reversal",
    }  # fmt: skip
    for row in loyalty_rows:
        events.append(
            TimelineEvent(
                "loyalty", _at_midnight(row.entry_date),
                loyalty_titles.get(row.entry_type.value, "Loyalty entry"),
                row.note or f"{row.points_delta:+d} points", None, "LOYALTY_LEDGER", row.id,
            )
        )  # fmt: skip

    for ref in session.scalars(
        select(ReferralEvent).where(
            ReferralEvent.shop_id == shop_id,
            (ReferralEvent.referrer_customer_id == customer_id)
            | (ReferralEvent.referred_customer_id == customer_id),
        )
    ):  # fmt: skip
        role = "Referred someone" if ref.referrer_customer_id == customer_id else "Was referred"
        events.append(
            TimelineEvent(
                "referral", ref.created_at, f"{role} ({ref.status.value.title()})",
                f"Referral event #{ref.id}", None, "REFERRAL_EVENT", ref.id,
            )
        )  # fmt: skip

    for note in session.scalars(
        select(CustomerNote).where(CustomerNote.shop_id == shop_id, CustomerNote.customer_id == customer_id)
    ):
        events.append(
            TimelineEvent("note", note.created_at, "Note added", note.body, None, "CUSTOMER_NOTE", note.id)
        )

    for task in session.scalars(
        select(BusinessTask).where(
            BusinessTask.shop_id == shop_id, BusinessTask.entity_type == "customer",
            BusinessTask.entity_id == customer_id,
        )
    ):  # fmt: skip
        events.append(
            TimelineEvent(
                "task", task.created_at, f"Task: {task.title}", f"Status: {task.status.value.title()}",
                None, "BUSINESS_TASK", task.id,
            )
        )  # fmt: skip

    customer = customer_service.get_customer(session, shop_id, customer_id)
    events.append(
        TimelineEvent(
            "customer_created", customer.created_at, "Customer created", customer.name,
            None, "CUSTOMER", customer.id,
        )
    )  # fmt: skip

    events.sort(key=lambda e: e.occurred_at, reverse=True)
    return events[:limit]


def get_approval_settings(session: Session, shop_id: int) -> dict[str, int | None]:
    shop = get_shop(session, shop_id)
    return {
        "campaign_audience_threshold": shop.crm_campaign_audience_threshold,
        "loyalty_adjustment_threshold": shop.crm_loyalty_adjustment_threshold,
    }


def set_approval_settings(
    session: Session, ctx: RequestContext, *, campaign_audience_threshold: int | None,
    loyalty_adjustment_threshold: int | None,
) -> dict[str, int | None]:  # fmt: skip
    """The shop's CRM approval thresholds. None turns that extra approval off; there is no built-in number."""
    for name, value in (
        ("campaign_audience_threshold", campaign_audience_threshold),
        ("loyalty_adjustment_threshold", loyalty_adjustment_threshold),
    ):
        if value is not None and value < 0:
            raise InvalidInputError("A threshold cannot be negative.", field=name)
    shop = get_shop(session, ctx.shop_id)
    before = get_approval_settings(session, ctx.shop_id)
    shop.crm_campaign_audience_threshold = campaign_audience_threshold
    shop.crm_loyalty_adjustment_threshold = loyalty_adjustment_threshold
    session.flush()
    after = get_approval_settings(session, ctx.shop_id)
    record_audit(
        session, ctx, entity_type="shop", entity_id=ctx.shop_id, action="crm_approval_settings_changed",
        before=before, after=after,
    )  # fmt: skip
    return after
