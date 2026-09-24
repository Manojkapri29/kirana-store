"""Loyalty: the shop's program configuration and each customer's insert-only ledger. See `loyalty_service`
for every rule; this router only parses requests, opens the write transaction, and shapes the response."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.schemas.loyalty import (
    AdjustIn,
    AdjustPendingOut,
    ExpiryOut,
    LedgerEntryOut,
    LedgerListOut,
    PointsSummaryOut,
    ProgramIn,
    ProgramOut,
    RedeemIn,
)
from app.services import loyalty_service
from app.services.shop_service import get_shop, shop_today

router = APIRouter(prefix="/loyalty", tags=["loyalty"])
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("/program", response_model=ProgramOut | None)
def get_program(ctx: Ctx, session: ReadSession) -> ProgramOut | None:
    program = loyalty_service.get_program(session, ctx.shop_id)
    return ProgramOut.of(program) if program else None


@router.put("/program", response_model=ProgramOut)
def configure_program(payload: ProgramIn, ctx: Ctx) -> ProgramOut:
    with write_transaction() as session:
        return ProgramOut.of(
            loyalty_service.configure_program(
                session,
                ctx,
                is_active=payload.is_active,
                points_per_amount=payload.points_per_amount,
                min_transaction_amount=payload.min_transaction_amount,
                redemption_value=payload.redemption_value,
                min_redemption_points=payload.min_redemption_points,
                max_redeem_points_per_txn=payload.max_redeem_points_per_txn,
                points_expiry_days=payload.points_expiry_days,
            )  # fmt: skip
        )


@router.get("/summary", response_model=PointsSummaryOut)
def summary(ctx: Ctx, session: ReadSession) -> PointsSummaryOut:
    return PointsSummaryOut(**loyalty_service.points_summary(session, ctx.shop_id))


@router.get("/customers/{customer_id}/ledger", response_model=LedgerListOut)
def get_ledger(customer_id: int, ctx: Ctx, session: ReadSession) -> LedgerListOut:
    rows, total = loyalty_service.list_ledger(session, ctx.shop_id, customer_id, limit=None)
    balance = loyalty_service.get_balance(session, ctx.shop_id, customer_id)
    return LedgerListOut(items=[LedgerEntryOut.of(r) for r in rows], total=total, balance=balance)


@router.post("/customers/{customer_id}/redeem", response_model=LedgerEntryOut, status_code=201)
def redeem(customer_id: int, payload: RedeemIn, ctx: Ctx) -> LedgerEntryOut:
    with write_transaction() as session:
        today = shop_today(get_shop(session, ctx.shop_id))
        entry = loyalty_service.record_redeem(
            session, ctx, customer_id=customer_id, points=payload.points, entry_date=today, note=payload.note
        )
        return LedgerEntryOut.of(entry)


@router.post(
    "/customers/{customer_id}/adjust", response_model=LedgerEntryOut | AdjustPendingOut, status_code=201
)
def adjust(
    customer_id: int, payload: AdjustIn, ctx: Ctx, response: Response
) -> LedgerEntryOut | AdjustPendingOut:
    with write_transaction() as session:
        today = shop_today(get_shop(session, ctx.shop_id))
        outcome = loyalty_service.adjust_with_controls(
            session,
            ctx,
            customer_id=customer_id,
            points_delta=payload.points_delta,
            entry_date=today,
            note=payload.note,
            approval_request_id=payload.approval_request_id,
        )
        if outcome.entry is None:
            response.status_code = 202
            return AdjustPendingOut(approval_request_id=outcome.approval_request_id)
        return LedgerEntryOut.of(outcome.entry)


@router.post("/expire", response_model=ExpiryOut)
def expire_points(ctx: Ctx) -> ExpiryOut:
    """Expires points past the program's expiry window. A person triggers this; nothing expires silently."""
    with write_transaction() as session:
        today = shop_today(get_shop(session, ctx.shop_id))
        result = loyalty_service.expire_points(session, ctx, today)
        return ExpiryOut(customers_expired=result.customers_expired, points_expired=result.points_expired)
