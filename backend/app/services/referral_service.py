"""Referral foundation: one program per shop, a referral code per referrer, and a referral event per referred
customer. Rewards are granted through the existing loyalty ledger (`loyalty_service.grant_reward`) — there is
no separate referral wallet. Basic, honestly-named safety rules only: self-referral is refused, and a
customer can be *referred* at most once ever (a database constraint, not just an application check). No fraud
detection is claimed or implied anywhere in this module.
"""

import logging
import secrets
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import observability
from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import ReferralCode, ReferralEvent, ReferralProgram
from app.models.enums import EventSeverity, ReferralEventStatus
from app.services import customer_service, loyalty_service, system_event_service
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I: easier to read aloud or copy


def get_program(session: Session, shop_id: int) -> ReferralProgram | None:
    return session.scalar(select(ReferralProgram).where(ReferralProgram.shop_id == shop_id))


def configure_program(
    session: Session,
    ctx: RequestContext,
    *,
    is_active: bool,
    referrer_reward_points: int | None = None,
    referred_reward_points: int | None = None,
    min_purchase_amount: Decimal | None = None,
    max_referrals_per_customer: int | None = None,
    expiry_days: int | None = None,
) -> ReferralProgram:
    program = get_program(session, ctx.shop_id)
    before = None
    if program is None:
        program = ReferralProgram(shop_id=ctx.shop_id, created_by=ctx.user_id)
        session.add(program)
    else:
        before = {"is_active": program.is_active}
    program.is_active = is_active
    program.referrer_reward_points = referrer_reward_points
    program.referred_reward_points = referred_reward_points
    program.min_purchase_amount = min_purchase_amount
    program.max_referrals_per_customer = max_referrals_per_customer
    program.expiry_days = expiry_days
    session.flush()
    record_audit(
        session, ctx, entity_type="referral_program", entity_id=program.id,
        action="referral_program_configured", before=before, after={"is_active": is_active},
    )  # fmt: skip
    return program


def code_for(session: Session, shop_id: int, customer_id: int) -> ReferralCode | None:
    return session.scalar(
        select(ReferralCode).where(ReferralCode.shop_id == shop_id, ReferralCode.customer_id == customer_id)
    )


def get_or_create_code(session: Session, ctx: RequestContext, customer_id: int) -> ReferralCode:
    customer_service.get_customer(session, ctx.shop_id, customer_id)
    existing = code_for(session, ctx.shop_id, customer_id)
    if existing is not None:
        return existing
    for _attempt in range(10):
        candidate = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not session.scalar(
            select(ReferralCode.id).where(ReferralCode.shop_id == ctx.shop_id, ReferralCode.code == candidate)
        ):
            code = ReferralCode(shop_id=ctx.shop_id, customer_id=customer_id, code=candidate)
            session.add(code)
            session.flush()
            record_audit(
                session, ctx, entity_type="referral_code", entity_id=code.id, action="referral_code_created"
            )
            return code
    raise ConflictError("Could not generate a unique referral code. Try again.")


def count_referrals(session: Session, shop_id: int, referrer_customer_id: int) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(ReferralEvent).where(
                ReferralEvent.shop_id == shop_id, ReferralEvent.referrer_customer_id == referrer_customer_id,
                ReferralEvent.status != ReferralEventStatus.INVALID,
            )
        )
        or 0
    )  # fmt: skip


def register_referral(
    session: Session, ctx: RequestContext, *, code: str, referred_customer_id: int
) -> ReferralEvent:
    """A person records that `referred_customer_id` was referred using `code`. Basic validation only: no
    self-referral, and a customer can be the *referred* party at most once ever (enforced by both this check
    and a unique database constraint, so a race still cannot create two)."""
    referral_code = session.scalar(
        select(ReferralCode).where(
            ReferralCode.shop_id == ctx.shop_id, ReferralCode.code == code.strip().upper()
        )
    )
    if referral_code is None or not referral_code.is_active:
        raise NotFoundError("Referral code not found")
    if referral_code.customer_id == referred_customer_id:
        raise InvalidInputError("A customer cannot refer themselves.", field="referred_customer_id")
    customer_service.get_customer(session, ctx.shop_id, referred_customer_id)

    program = get_program(session, ctx.shop_id)
    if program is not None and program.max_referrals_per_customer is not None:
        made = count_referrals(session, ctx.shop_id, referral_code.customer_id)
        if made >= program.max_referrals_per_customer:
            raise ConflictError("This customer has reached the maximum number of referrals.")

    existing = session.scalar(
        select(ReferralEvent).where(
            ReferralEvent.shop_id == ctx.shop_id, ReferralEvent.referred_customer_id == referred_customer_id
        )
    )
    if existing is not None:
        raise ConflictError("This customer has already been referred once.", code="already_referred")

    event = ReferralEvent(
        shop_id=ctx.shop_id, referral_code_id=referral_code.id,
        referrer_customer_id=referral_code.customer_id,
        referred_customer_id=referred_customer_id, status=ReferralEventStatus.PENDING,
    )  # fmt: skip
    session.add(event)
    session.flush()
    record_audit(session, ctx, entity_type="referral_event", entity_id=event.id, action="referral_registered")
    return event


def _expired(program: ReferralProgram, event: ReferralEvent, today: date) -> bool:
    if program.expiry_days is None:
        return False
    return (today - event.created_at.date()).days > program.expiry_days


def qualify_from_sale(
    session: Session, ctx: RequestContext, *, customer_id: int | None, amount: Decimal,
    reference_type: str, reference_id: int, entry_date: date,
) -> ReferralEvent | None:  # fmt: skip
    """Best-effort: never raises. Called from `sale_service.post_sale` / `quick_sale_service.post_quick_sale`
    alongside `loyalty_service.earn_for_sale`. If this customer was referred and is still PENDING, and this
    transaction meets the program's minimum purchase amount, the referral qualifies and rewards are granted
    to the referrer (and to the referred customer, if configured) through `loyalty_service.grant_reward` —
    which is itself idempotent, so a retried or duplicate call never double-rewards."""
    if customer_id is None:
        return None
    try:
        with session.begin_nested():
            program = get_program(session, ctx.shop_id)
            if program is None or not program.is_active:
                return None
            event = session.scalar(
                select(ReferralEvent).where(
                    ReferralEvent.shop_id == ctx.shop_id, ReferralEvent.referred_customer_id == customer_id,
                    ReferralEvent.status == ReferralEventStatus.PENDING,
                ).with_for_update()
            )  # fmt: skip
            if event is None:
                return None
            if _expired(program, event, entry_date):
                event.status = ReferralEventStatus.EXPIRED
                return event
            if program.min_purchase_amount is not None and amount < program.min_purchase_amount:
                return None
            event.status = ReferralEventStatus.QUALIFIED
            event.qualifying_reference_type = reference_type
            event.qualifying_reference_id = reference_id
            event.qualified_at = utc_now()
            rewarded = False
            if program.referrer_reward_points:
                loyalty_service.grant_reward(
                    session, ctx, customer_id=event.referrer_customer_id,
                    points=program.referrer_reward_points,
                    reference_type="REFERRAL", reference_id=event.id, entry_date=entry_date,
                    note=f"Referral reward for referring customer #{event.referred_customer_id}",
                )  # fmt: skip
                rewarded = True
            if program.referred_reward_points:
                loyalty_service.grant_reward(
                    session, ctx, customer_id=event.referred_customer_id,
                    points=program.referred_reward_points,
                    reference_type="REFERRAL", reference_id=event.id, entry_date=entry_date,
                    note="Welcome reward for being referred",
                )  # fmt: skip
                rewarded = True
            if rewarded:
                event.status = ReferralEventStatus.REWARDED
                event.rewarded_at = utc_now()
            return event
    except Exception:  # noqa: BLE001
        observability.log_event(
            "referral", "could not qualify a referral", level=logging.WARNING,
            shop_id=ctx.shop_id, reference_type=reference_type, reference_id=reference_id,
        )  # fmt: skip
        system_event_service.record(
            session, category="referral", severity=EventSeverity.WARNING, source="qualify_from_sale",
            code="qualify_failed", message="A referral could not be qualified after a sale.",
            shop_id=ctx.shop_id,
        )  # fmt: skip
        return None


def list_events(
    session: Session, shop_id: int, *, referrer_customer_id: int | None = None, limit: int | None = 100
) -> list[ReferralEvent]:
    query = select(ReferralEvent).where(ReferralEvent.shop_id == shop_id)
    if referrer_customer_id is not None:
        query = query.where(ReferralEvent.referrer_customer_id == referrer_customer_id)
    query = query.order_by(ReferralEvent.id.desc())
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query))
