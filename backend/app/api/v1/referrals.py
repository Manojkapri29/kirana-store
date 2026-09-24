"""Referral foundation. See `referral_service` for every rule (self-referral refused, one referred customer
per referral, rewards through the loyalty ledger); this router only parses requests, opens the write
transaction, and shapes the response."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.schemas.referrals import CodeOut, EventListOut, EventOut, ProgramIn, ProgramOut, RegisterIn
from app.services import referral_service

router = APIRouter(prefix="/referrals", tags=["referrals"])
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("/program", response_model=ProgramOut | None)
def get_program(ctx: Ctx, session: ReadSession) -> ProgramOut | None:
    program = referral_service.get_program(session, ctx.shop_id)
    return ProgramOut.of(program) if program else None


@router.put("/program", response_model=ProgramOut)
def configure_program(payload: ProgramIn, ctx: Ctx) -> ProgramOut:
    with write_transaction() as session:
        return ProgramOut.of(
            referral_service.configure_program(
                session,
                ctx,
                is_active=payload.is_active,
                referrer_reward_points=payload.referrer_reward_points,
                referred_reward_points=payload.referred_reward_points,
                min_purchase_amount=payload.min_purchase_amount,
                max_referrals_per_customer=payload.max_referrals_per_customer,
                expiry_days=payload.expiry_days,
            )  # fmt: skip
        )


@router.get("/customers/{customer_id}/code", response_model=CodeOut)
def get_or_create_code(customer_id: int, ctx: Ctx) -> CodeOut:
    with write_transaction() as session:
        return CodeOut.of(referral_service.get_or_create_code(session, ctx, customer_id))


@router.post("/register", response_model=EventOut, status_code=201)
def register(payload: RegisterIn, ctx: Ctx) -> EventOut:
    with write_transaction() as session:
        return EventOut.of(
            referral_service.register_referral(
                session, ctx, code=payload.code, referred_customer_id=payload.referred_customer_id
            )
        )


@router.get("/events", response_model=EventListOut)
def list_events(
    ctx: Ctx,
    session: ReadSession,
    referrer_customer_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> EventListOut:
    return EventListOut(
        items=[
            EventOut.of(e)
            for e in referral_service.list_events(
                session, ctx.shop_id, referrer_customer_id=referrer_customer_id, limit=limit
            )
        ]
    )
