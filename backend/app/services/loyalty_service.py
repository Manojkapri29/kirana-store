"""Loyalty: one configurable program per shop, and an INSERT-ONLY ledger exactly like `khata_service`'s. A
customer's point balance is never stored — it is the sum of their `loyalty_ledger` rows — so this is the only
service that may read or write `LoyaltyLedger` (see `test_architecture.py`); everything else calls the bulk
helpers below (`get_balance_map`, `list_ledger`), mirroring `khata_service.get_balance_map`.

Earning and reversal are called from `sale_service`/`quick_sale_service` exactly where khata charging already
is, but run in a savepoint and never raise: a loyalty problem must never block a sale from posting or being
voided, the same guarantee `notification_service.emit_safely` gives for notifications. Idempotency comes from
a unique constraint on (shop_id, customer_id, entry_type, reference_type, reference_id) — earning twice for
the same sale, or reversing twice, simply does nothing the second time.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import observability
from app.core.context import RequestContext
from app.models import LoyaltyLedger, LoyaltyProgram
from app.models.enums import ApprovalStatus, EventSeverity, LoyaltyEntryType
from app.services import approval_service, system_event_service
from app.services.audit_service import record_audit
from app.services.customer_service import get_customer
from app.services.errors import ConflictError, InvalidInputError
from app.services.shop_service import get_shop

ZERO = Decimal("0")
APPROVAL_KIND = "LOYALTY_LARGE_ADJUSTMENT"


@dataclass(frozen=True)
class LedgerEntryView:
    """A read-only view of one ledger row — every other service and every schema type-hints against this,
    never the ORM model, exactly like `khata_service.LedgerRow`."""

    id: int
    customer_id: int
    entry_type: LoyaltyEntryType
    points_delta: int
    reference_type: str
    reference_id: int | None
    note: str | None
    entry_date: date
    created_at: datetime


def _view(row: LoyaltyLedger) -> LedgerEntryView:
    return LedgerEntryView(
        id=row.id, customer_id=row.customer_id, entry_type=row.entry_type, points_delta=row.points_delta,
        reference_type=row.reference_type, reference_id=row.reference_id, note=row.note,
        entry_date=row.entry_date, created_at=row.created_at,
    )  # fmt: skip


@dataclass(frozen=True)
class ProgramConfig:
    is_active: bool
    points_per_amount: Decimal
    min_transaction_amount: Decimal
    redemption_value: Decimal
    min_redemption_points: int | None
    max_redeem_points_per_txn: int | None
    points_expiry_days: int | None


def get_program(session: Session, shop_id: int) -> LoyaltyProgram | None:
    return session.scalar(select(LoyaltyProgram).where(LoyaltyProgram.shop_id == shop_id))


def configure_program(
    session: Session,
    ctx: RequestContext,
    *,
    is_active: bool,
    points_per_amount: Decimal,
    min_transaction_amount: Decimal = ZERO,
    redemption_value: Decimal,
    min_redemption_points: int | None = None,
    max_redeem_points_per_txn: int | None = None,
    points_expiry_days: int | None = None,
) -> LoyaltyProgram:
    """Create or update the shop's one loyalty program. Every rule is a parameter here, never assumed."""
    if points_per_amount <= 0:
        raise InvalidInputError("Points per amount must be greater than zero.", field="points_per_amount")
    if redemption_value <= 0:
        raise InvalidInputError("Redemption value must be greater than zero.", field="redemption_value")
    if min_transaction_amount < 0:
        raise InvalidInputError(
            "Minimum transaction amount cannot be negative.", field="min_transaction_amount"
        )

    program = get_program(session, ctx.shop_id)
    before = None
    if program is None:
        program = LoyaltyProgram(
            shop_id=ctx.shop_id,
            created_by=ctx.user_id,
            points_per_amount=points_per_amount,
            redemption_value=redemption_value,
        )
        session.add(program)
    else:
        before = {
            "is_active": program.is_active, "points_per_amount": str(program.points_per_amount),
            "redemption_value": str(program.redemption_value),
        }  # fmt: skip
    program.is_active = is_active
    program.points_per_amount = points_per_amount
    program.min_transaction_amount = min_transaction_amount
    program.redemption_value = redemption_value
    program.min_redemption_points = min_redemption_points
    program.max_redeem_points_per_txn = max_redeem_points_per_txn
    program.points_expiry_days = points_expiry_days
    session.flush()
    record_audit(
        session, ctx, entity_type="loyalty_program", entity_id=program.id,
        action="loyalty_program_configured",
        before=before, after={"is_active": is_active, "points_per_amount": str(points_per_amount)},
    )  # fmt: skip
    return program


def get_balance(session: Session, shop_id: int, customer_id: int) -> int:
    total = session.scalar(
        select(func.coalesce(func.sum(LoyaltyLedger.points_delta), 0)).where(
            LoyaltyLedger.shop_id == shop_id, LoyaltyLedger.customer_id == customer_id
        )
    )
    return int(total or 0)


def get_balance_map(session: Session, shop_id: int, customer_ids: Sequence[int]) -> dict[int, int]:
    """Balances for several customers in one query. A customer without entries has balance 0."""
    balances = {customer_id: 0 for customer_id in customer_ids}
    if not customer_ids:
        return balances
    rows = session.execute(
        select(LoyaltyLedger.customer_id, func.sum(LoyaltyLedger.points_delta))
        .where(LoyaltyLedger.shop_id == shop_id, LoyaltyLedger.customer_id.in_(customer_ids))
        .group_by(LoyaltyLedger.customer_id)
    )
    for customer_id, total in rows:
        balances[customer_id] = int(total or 0)
    return balances


def list_ledger(
    session: Session, shop_id: int, customer_id: int, *, limit: int | None = 50, offset: int = 0
) -> tuple[list[LedgerEntryView], int]:
    get_customer(
        session, shop_id, customer_id
    )  # a customer of another shop is "not found", never an empty list
    conditions = (LoyaltyLedger.shop_id == shop_id, LoyaltyLedger.customer_id == customer_id)
    total = session.scalar(select(func.count()).select_from(LoyaltyLedger).where(*conditions)) or 0
    rows_query = (
        select(LoyaltyLedger)
        .where(*conditions)
        .order_by(LoyaltyLedger.entry_date.desc(), LoyaltyLedger.id.desc())
        .offset(offset)
    )
    if limit is not None:
        rows_query = rows_query.limit(limit)
    return [_view(r) for r in session.scalars(rows_query)], total


def points_summary(session: Session, shop_id: int) -> dict[str, int]:
    """Issued vs. redeemed, for the CRM dashboard. Both are always-non-negative totals, never netted against
    each other, so "points issued" and "points redeemed" mean exactly what they say."""
    rows = dict(
        session.execute(
            select(LoyaltyLedger.entry_type, func.coalesce(func.sum(LoyaltyLedger.points_delta), 0))
            .where(LoyaltyLedger.shop_id == shop_id)
            .group_by(LoyaltyLedger.entry_type)
        ).all()
    )
    issued = int(rows.get(LoyaltyEntryType.EARN, 0) or 0)
    redeemed = -int(rows.get(LoyaltyEntryType.REDEEM, 0) or 0)
    outstanding = sum(int(v or 0) for v in rows.values())
    return {"points_issued": issued, "points_redeemed": redeemed, "points_outstanding": outstanding}


@dataclass(frozen=True)
class ExpiryResult:
    customers_expired: int
    points_expired: int


def expire_points(session: Session, ctx: RequestContext, today: date) -> ExpiryResult:
    """Expire points earned more than the program's `points_expiry_days` ago and not since used up, one
    EXPIRE ledger row per affected customer (never an edit of an old row). Oldest-first: debits (redemptions,
    reversals, negative adjustments, earlier expiries) are assumed to consume the oldest points, so only what
    is left of the pre-cutoff earnings can expire, and never more than the current balance. Idempotent per
    customer per day, and a no-op when the program has no expiry configured."""
    program = get_program(session, ctx.shop_id)
    if program is None or program.points_expiry_days is None:
        return ExpiryResult(0, 0)
    cutoff = today - timedelta(days=program.points_expiry_days)
    rows = session.execute(
        select(
            LoyaltyLedger.customer_id,
            func.coalesce(func.sum(LoyaltyLedger.points_delta), 0),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (LoyaltyLedger.entry_type == LoyaltyEntryType.EARN)
                            & (LoyaltyLedger.entry_date <= cutoff),
                            LoyaltyLedger.points_delta,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(case((LoyaltyLedger.points_delta < 0, -LoyaltyLedger.points_delta), else_=0)), 0
            ),
        )
        .where(LoyaltyLedger.shop_id == ctx.shop_id)
        .group_by(LoyaltyLedger.customer_id)
    ).all()
    customers = points = 0
    for customer_id, balance, earned_before_cutoff, debits in rows:
        expirable = min(int(balance), max(0, int(earned_before_cutoff) - int(debits)))
        if expirable <= 0:
            continue
        try:
            with session.begin_nested():
                _insert(
                    session, ctx, customer_id=customer_id, entry_type=LoyaltyEntryType.EXPIRE,
                    points_delta=-expirable, reference_type="EXPIRY", reference_id=today.toordinal(),
                    note=f"Points earned on or before {cutoff.isoformat()} expired.", entry_date=today,
                )  # fmt: skip
        except IntegrityError:
            continue  # already expired today
        customers += 1
        points += expirable
    if customers:
        record_audit(
            session, ctx, entity_type="loyalty_program", entity_id=program.id,
            action="loyalty_points_expired",
            after={"customers": customers, "points": points, "cutoff": cutoff.isoformat()},
        )  # fmt: skip
    return ExpiryResult(customers, points)


def _insert(
    session: Session, ctx: RequestContext, *, customer_id: int, entry_type: LoyaltyEntryType,
    points_delta: int, reference_type: str, reference_id: int | None, note: str | None, entry_date: date,
) -> LoyaltyLedger:  # fmt: skip
    """A plain insert. Callers that need idempotency against a duplicate reference wrap this call in their
    own `session.begin_nested()` and catch `IntegrityError` there (see `earn_for_sale`, `grant_reward`) —
    this function never swallows an error itself, so it is safe to call from a context that should raise."""
    row = LoyaltyLedger(
        shop_id=ctx.shop_id, customer_id=customer_id, entry_type=entry_type, points_delta=points_delta,
        reference_type=reference_type, reference_id=reference_id, note=note, entry_date=entry_date,
        created_by=ctx.user_id,
    )  # fmt: skip
    session.add(row)
    session.flush()
    return row


def earn_for_sale(
    session: Session, ctx: RequestContext, *, customer_id: int | None, amount: Decimal,
    reference_type: str, reference_id: int, entry_date: date,
) -> LedgerEntryView | None:  # fmt: skip
    """Best-effort: never raises. Called from `sale_service.post_sale` / `quick_sale_service.post_quick_sale`
    exactly where khata charging already is. A sale with no customer, or a shop with no active program, or a
    sale below `min_transaction_amount`, earns nothing — quietly, not an error."""
    if customer_id is None:
        return None
    try:
        with session.begin_nested():
            program = get_program(session, ctx.shop_id)
            if program is None or not program.is_active:
                return None
            if amount < program.min_transaction_amount:
                return None
            points = int((amount * program.points_per_amount).to_integral_value(rounding=ROUND_FLOOR))
            if points <= 0:
                return None
            return _view(
                _insert(
                    session, ctx, customer_id=customer_id, entry_type=LoyaltyEntryType.EARN,
                    points_delta=points, reference_type=reference_type, reference_id=reference_id,
                    note=None, entry_date=entry_date,
                )
            )  # fmt: skip
    except Exception:  # noqa: BLE001
        observability.log_event(
            "loyalty", "could not earn loyalty points", level=logging.WARNING,
            shop_id=ctx.shop_id, reference_type=reference_type, reference_id=reference_id,
        )  # fmt: skip
        system_event_service.record(
            session, category="loyalty", severity=EventSeverity.WARNING, source="earn_for_sale",
            code="earn_failed", message="Loyalty points could not be recorded for a sale.",
            shop_id=ctx.shop_id,
        )  # fmt: skip
        return None


def reverse_for_sale(
    session: Session,
    ctx: RequestContext,
    *,
    reference_type: str,
    reference_id: int,
    entry_date: date,
    reason: str,
) -> LedgerEntryView | None:
    """Best-effort: never raises. Reverses a prior EARN for this reference, if one exists and has not
    already been reversed. Called from `sale_service.void_sale` / `quick_sale_service.void_quick_sale`."""
    try:
        with session.begin_nested():
            earned = session.scalar(
                select(LoyaltyLedger).where(
                    LoyaltyLedger.shop_id == ctx.shop_id, LoyaltyLedger.entry_type == LoyaltyEntryType.EARN,
                    LoyaltyLedger.reference_type == reference_type,
                    LoyaltyLedger.reference_id == reference_id,
                )
            )  # fmt: skip
            if earned is None or earned.points_delta == 0:
                return None
            return _view(
                _insert(
                    session, ctx, customer_id=earned.customer_id, entry_type=LoyaltyEntryType.REVERSAL,
                    points_delta=-earned.points_delta, reference_type=reference_type,
                    reference_id=reference_id, note=reason[:300], entry_date=entry_date,
                )
            )  # fmt: skip
    except Exception:  # noqa: BLE001
        observability.log_event(
            "loyalty", "could not reverse loyalty points", level=logging.WARNING,
            shop_id=ctx.shop_id, reference_type=reference_type, reference_id=reference_id,
        )  # fmt: skip
        system_event_service.record(
            session, category="loyalty", severity=EventSeverity.WARNING, source="reverse_for_sale",
            code="reverse_failed", message="Loyalty points could not be reversed for a voided sale.",
            shop_id=ctx.shop_id,
        )  # fmt: skip
        return None


def record_redeem(
    session: Session,
    ctx: RequestContext,
    *,
    customer_id: int,
    points: int,
    entry_date: date,
    note: str | None = None,
) -> LedgerEntryView:
    """A person redeems points for the customer. Unlike earning, this is not best-effort: a redemption is a
    deliberate action and its rules (minimum, maximum, enough balance) must be enforced, not silently
    skipped."""
    get_customer(session, ctx.shop_id, customer_id)
    if points <= 0:
        raise InvalidInputError("Enter how many points to redeem.", field="points")
    program = get_program(session, ctx.shop_id)
    if program is None or not program.is_active:
        raise ConflictError("There is no active loyalty program for this shop.")
    if program.min_redemption_points is not None and points < program.min_redemption_points:
        raise InvalidInputError(
            f"At least {program.min_redemption_points} points must be redeemed at once.", field="points"
        )
    if program.max_redeem_points_per_txn is not None and points > program.max_redeem_points_per_txn:
        raise InvalidInputError(
            f"At most {program.max_redeem_points_per_txn} points can be redeemed at once.",
            field="points",
        )
    balance = get_balance(session, ctx.shop_id, customer_id)
    if points > balance:
        raise ConflictError(f"Only {balance} points are available.", field="points")
    row = LoyaltyLedger(
        shop_id=ctx.shop_id, customer_id=customer_id, entry_type=LoyaltyEntryType.REDEEM,
        points_delta=-points, reference_type="REDEMPTION", reference_id=None,
        note=(note or "").strip() or None, entry_date=entry_date, created_by=ctx.user_id,
    )  # fmt: skip
    session.add(row)
    session.flush()
    record_audit(
        session, ctx, entity_type="customer", entity_id=customer_id, action="loyalty_redeemed",
        after={"points": points, "redemption_value": str(points * program.redemption_value)},
    )  # fmt: skip
    return _view(row)


@dataclass(frozen=True)
class AdjustOutcome:
    """Either the recorded entry, or the id of the approval it is waiting on (never both)."""

    entry: LedgerEntryView | None
    approval_request_id: int | None


def adjust_with_controls(
    session: Session, ctx: RequestContext, *, customer_id: int, points_delta: int, entry_date: date,
    note: str, approval_request_id: int | None = None,
) -> AdjustOutcome:  # fmt: skip
    """The manual-adjustment entry point for people. An adjustment LARGER than the shop's configured threshold
    is not recorded straight away: it opens an approval for a second person, and is recorded only when the
    requester comes back with that approved request's id. No threshold configured = recorded immediately."""
    get_customer(session, ctx.shop_id, customer_id)
    threshold = get_shop(session, ctx.shop_id).crm_loyalty_adjustment_threshold
    size = abs(points_delta)
    if threshold is None or size <= threshold:
        return AdjustOutcome(record_adjust(session, ctx, customer_id=customer_id, points_delta=points_delta,
                                           entry_date=entry_date, note=note), None)  # fmt: skip
    if points_delta == 0 or not note.strip():
        return AdjustOutcome(record_adjust(session, ctx, customer_id=customer_id, points_delta=points_delta,
                                           entry_date=entry_date, note=note), None)  # fmt: skip
    if approval_request_id is None:
        request = approval_service.create(
            session, ctx, kind=APPROVAL_KIND, entity_type="customer", entity_id=customer_id,
            reason=f"Adjustment of {points_delta:+d} points exceeds the approval threshold of {threshold}. "
                   f"Reason given: {note.strip()}",
            threshold_value=Decimal(threshold), observed_value=Decimal(size),
        )  # fmt: skip
        return AdjustOutcome(None, request.id)
    request = approval_service.get(session, ctx.shop_id, approval_request_id)
    if (
        request.kind != APPROVAL_KIND
        or request.entity_id != customer_id
        or request.observed_value != Decimal(size)
    ):
        raise InvalidInputError("That approval is not for this adjustment.", field="approval_request_id")
    if request.status is not ApprovalStatus.APPROVED:
        raise ConflictError("That adjustment has not been approved.")
    try:
        with session.begin_nested():
            entry = record_adjust(
                session, ctx, customer_id=customer_id, points_delta=points_delta, entry_date=entry_date,
                note=note, reference_type="APPROVAL", reference_id=request.id,
            )  # fmt: skip
    except IntegrityError as exc:
        raise ConflictError("That approval has already been used.") from exc
    return AdjustOutcome(entry, None)


def record_adjust(
    session: Session, ctx: RequestContext, *, customer_id: int, points_delta: int, entry_date: date,
    note: str, reference_type: str = "MANUAL", reference_id: int | None = None,
) -> LedgerEntryView:  # fmt: skip
    """A manual staff correction. Always requires a reason, exactly like a stock or khata adjustment."""
    get_customer(session, ctx.shop_id, customer_id)
    if points_delta == 0:
        raise InvalidInputError("The adjustment cannot be zero.", field="points_delta")
    if not note.strip():
        raise InvalidInputError("Give a reason for this adjustment.", field="note")
    row = LoyaltyLedger(
        shop_id=ctx.shop_id, customer_id=customer_id, entry_type=LoyaltyEntryType.ADJUST,
        points_delta=points_delta, reference_type=reference_type, reference_id=reference_id,
        note=note.strip(), entry_date=entry_date, created_by=ctx.user_id,
    )  # fmt: skip
    session.add(row)
    session.flush()
    record_audit(
        session, ctx, entity_type="customer", entity_id=customer_id, action="loyalty_adjusted",
        after={"points_delta": points_delta, "note": note.strip()},
    )  # fmt: skip
    return _view(row)


def grant_reward(
    session: Session, ctx: RequestContext, *, customer_id: int, points: int, reference_type: str,
    reference_id: int, entry_date: date, note: str,
) -> LedgerEntryView | None:  # fmt: skip
    """An EARN entry for a non-sale reward (a referral). Idempotent on (customer, reference): a second grant
    for the same reference does nothing and returns None, rather than raising or double-crediting."""
    if points <= 0:
        raise InvalidInputError("The reward must be a positive number of points.", field="points")
    try:
        with session.begin_nested():
            return _view(
                _insert(
                    session, ctx, customer_id=customer_id, entry_type=LoyaltyEntryType.EARN,
                    points_delta=points, reference_type=reference_type, reference_id=reference_id,
                    note=note, entry_date=entry_date,
                )
            )  # fmt: skip
    except IntegrityError:
        return None
