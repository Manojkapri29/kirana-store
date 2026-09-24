"""Financial periods: OPEN, LOCKED and CLOSED date ranges that gate changes to past records.

* OPEN: normal operations.
* LOCKED: ordinary changes are refused; only a *controlled* correction (an authorised adjustment, see
  `finance_ledger_service.record_adjustment`) may still be dated inside it.
* CLOSED: nothing may be posted, voided or adjusted with a date inside it. A correction is a new entry dated
  in an open period that says what it corrects, or the period is first reopened (which can require a second
  person's approval, see `finance_settings_service`).

A date that no period covers is OPEN: periods are opt-in. Every lock, close, reopen and unlock is audited,
and a refused change to a locked or closed period is recorded as a system event that survives the
rollback (see `app.db.session.note_after_rollback`), so it can raise an alert.
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.session import note_after_rollback
from app.db.types import utc_now
from app.models import ApprovalRequest, FinancialPeriod
from app.models.enums import ApprovalStatus, EventSeverity, PeriodStatus
from app.services import approval_service, system_event_service
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.finance_settings_service import get_settings

REOPEN_APPROVAL_KIND = "FINANCE_PERIOD_REOPEN"
BLOCKED_EVENT_CODE = "FINANCE_PERIOD_BLOCKED"


def _get(session: Session, shop_id: int, period_id: int, *, lock: bool = False) -> FinancialPeriod:
    query = select(FinancialPeriod).where(FinancialPeriod.shop_id == shop_id, FinancialPeriod.id == period_id)
    period = session.scalar(query.with_for_update() if lock else query)
    if period is None:
        raise NotFoundError("Financial period not found")
    return period


def get(session: Session, shop_id: int, period_id: int) -> FinancialPeriod:
    return _get(session, shop_id, period_id)


def list_periods(session: Session, shop_id: int) -> list[FinancialPeriod]:
    return list(
        session.scalars(
            select(FinancialPeriod)
            .where(FinancialPeriod.shop_id == shop_id)
            .order_by(FinancialPeriod.period_start.desc())
        )
    )


def period_for(session: Session, shop_id: int, on: date) -> FinancialPeriod | None:
    return session.scalar(
        select(FinancialPeriod).where(
            FinancialPeriod.shop_id == shop_id,
            FinancialPeriod.period_start <= on,
            FinancialPeriod.period_end >= on,
        )
    )


def status_on(session: Session, shop_id: int, on: date) -> PeriodStatus:
    period = period_for(session, shop_id, on)
    return period.status if period else PeriodStatus.OPEN


def create_period(
    session: Session, ctx: RequestContext, *, period_start: date, period_end: date, note: str | None = None
) -> FinancialPeriod:
    if period_end < period_start:
        raise InvalidInputError("The end date cannot be before the start date.", field="period_end")
    clash = session.scalar(
        select(FinancialPeriod).where(
            FinancialPeriod.shop_id == ctx.shop_id,
            FinancialPeriod.period_start <= period_end,
            FinancialPeriod.period_end >= period_start,
        )
    )
    if clash is not None:
        raise ConflictError(
            f"This overlaps the period {clash.period_start} to {clash.period_end}.", code="period_overlap"
        )
    period = FinancialPeriod(
        shop_id=ctx.shop_id, period_start=period_start, period_end=period_end, note=note,
        created_by=ctx.user_id,
    )  # fmt: skip
    session.add(period)
    session.flush()
    record_audit(
        session, ctx, entity_type="financial_period", entity_id=period.id, action="period_created",
        after={"period_start": period_start, "period_end": period_end},
    )  # fmt: skip
    return period


# --- The guard every financial change goes through ---------------------------------------------------------


def _note_blocked(session: Session, ctx: RequestContext, on: date, status: PeriodStatus, action: str) -> None:
    shop_id, user_id = ctx.shop_id, ctx.user_id

    def _record(s: Session) -> None:
        system_event_service.record(
            s, category="FINANCE", severity=EventSeverity.WARNING, source="finance_period",
            code=BLOCKED_EVENT_CODE, shop_id=shop_id,
            message=f"A change ({action}) dated {on.isoformat()} was refused: that period is {status.value}"
            f" (user {user_id}).",
        )  # fmt: skip

    note_after_rollback(session, _record)


def assert_open(
    session: Session, ctx: RequestContext, on: date, *, action: str, controlled: bool = False
) -> None:
    """Refuse a change dated in a locked or closed period. `controlled=True` is for a correction made through
    the authorised adjustment path, which a LOCKED period still accepts (a CLOSED one never does)."""
    status = status_on(session, ctx.shop_id, on)
    if status is PeriodStatus.OPEN or (status is PeriodStatus.LOCKED and controlled):
        return
    _note_blocked(session, ctx, on, status, action)
    if status is PeriodStatus.CLOSED:
        raise ConflictError(
            f"The books for {on.isoformat()} are closed, so this cannot be changed directly. Record a "
            "correcting adjustment dated in an open period, or ask for the period to be reopened.",
            code="period_closed",
        )
    raise ConflictError(
        f"The period containing {on.isoformat()} is locked. Only an authorised correction is allowed.",
        code="period_locked",
    )


# --- Lock / close / reopen ---------------------------------------------------------------------------------


def _change(
    session: Session, ctx: RequestContext, period: FinancialPeriod, new: PeriodStatus, action: str,
    note: str | None = None,
) -> FinancialPeriod:  # fmt: skip
    before = period.status
    period.status = new
    period.status_changed_by = ctx.user_id
    period.status_changed_at = utc_now()
    session.flush()
    record_audit(
        session, ctx, entity_type="financial_period", entity_id=period.id, action=action,
        before={"status": before}, after={"status": new, "note": note},
    )  # fmt: skip
    return period


def lock(session: Session, ctx: RequestContext, period_id: int) -> FinancialPeriod:
    period = _get(session, ctx.shop_id, period_id, lock=True)
    if period.status is not PeriodStatus.OPEN:
        raise ConflictError("Only an open period can be locked.")
    return _change(session, ctx, period, PeriodStatus.LOCKED, "period_locked")


def close(session: Session, ctx: RequestContext, period_id: int) -> FinancialPeriod:
    period = _get(session, ctx.shop_id, period_id, lock=True)
    if period.status is PeriodStatus.CLOSED:
        raise ConflictError("This period is already closed.")
    return _change(session, ctx, period, PeriodStatus.CLOSED, "period_closed")


def unlock(session: Session, ctx: RequestContext, period_id: int) -> FinancialPeriod:
    """LOCKED -> OPEN. A closed period is reopened with `reopen`, a heavier, separately gated step."""
    period = _get(session, ctx.shop_id, period_id, lock=True)
    if period.status is not PeriodStatus.LOCKED:
        raise ConflictError("Only a locked period can be unlocked. Use reopen for a closed one.")
    return _change(session, ctx, period, PeriodStatus.OPEN, "period_unlocked")


def _latest_reopen_approval(session: Session, shop_id: int, period_id: int) -> ApprovalRequest | None:
    return session.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.shop_id == shop_id,
            ApprovalRequest.kind == REOPEN_APPROVAL_KIND,
            ApprovalRequest.entity_type == "financial_period",
            ApprovalRequest.entity_id == period_id,
        )
        .order_by(ApprovalRequest.id.desc())
        .limit(1)
    )


def reopen(session: Session, ctx: RequestContext, period_id: int, reason: str) -> FinancialPeriod:
    """CLOSED -> OPEN, with a reason. When the shop requires approval this opens a request and leaves the
    period closed; once a second person approves it, calling reopen again completes it. An approval only
    counts for the closing that was current when it was decided, so it can be used once."""
    if not reason.strip():
        raise InvalidInputError("Give a reason for reopening this period.", field="reason")
    period = _get(session, ctx.shop_id, period_id, lock=True)
    if period.status is not PeriodStatus.CLOSED:
        raise ConflictError("Only a closed period can be reopened.")
    if get_settings(session, ctx.shop_id).period_reopen_requires_approval:
        closed_at = period.status_changed_at
        latest = _latest_reopen_approval(session, ctx.shop_id, period.id)
        current = latest is not None and closed_at is not None and latest.created_at > closed_at
        if current and latest.status is ApprovalStatus.PENDING:
            return period
        approved = current and latest.status is ApprovalStatus.APPROVED
        if not approved:
            approval_service.create(
                session, ctx, kind=REOPEN_APPROVAL_KIND, entity_type="financial_period", entity_id=period.id,
                reason=f"Reopen {period.period_start} to {period.period_end}: {reason.strip()}",
            )  # fmt: skip
            record_audit(
                session, ctx, entity_type="financial_period", entity_id=period.id,
                action="period_reopen_approval_requested", after={"reason": reason.strip()},
            )  # fmt: skip
            return period
    return _change(session, ctx, period, PeriodStatus.OPEN, "period_reopened", note=reason.strip())


def has_pending_reopen(session: Session, period: FinancialPeriod) -> bool:
    """Is a reopen request for the current closing waiting for a second person?"""
    latest = _latest_reopen_approval(session, period.shop_id, period.id)
    closed_at = period.status_changed_at
    return (
        period.status is PeriodStatus.CLOSED
        and latest is not None
        and closed_at is not None
        and latest.created_at > closed_at
        and latest.status is ApprovalStatus.PENDING
    )


def one_day_before(day: date) -> date:
    return day - timedelta(days=1)
