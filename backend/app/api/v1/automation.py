"""Marketing automation rules. See `automation_service` for every rule (trigger conditions, cooldown, the
safe actions it can take); this router only parses requests, opens the write transaction, and shapes the
response."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.schemas.automation import RuleCreateIn, RuleListOut, RuleOut, RunResultOut
from app.services import automation_service
from app.services.shop_service import get_shop, shop_today

router = APIRouter(prefix="/automation-rules", tags=["automation"])
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("", response_model=RuleListOut)
def list_rules(ctx: Ctx, session: ReadSession) -> RuleListOut:
    return RuleListOut(items=[RuleOut.of(r) for r in automation_service.list_rules(session, ctx.shop_id)])


@router.post("", response_model=RuleOut, status_code=201)
def create_rule(payload: RuleCreateIn, ctx: Ctx) -> RuleOut:
    with write_transaction() as session:
        rule = automation_service.create_rule(
            session, ctx, name=payload.name, trigger_type=payload.trigger_type,
            conditions=payload.conditions, action_type=payload.action_type,
            action_config=payload.action_config, cooldown_days=payload.cooldown_days,
        )  # fmt: skip
        return RuleOut.of(rule)


@router.get("/{rule_id}", response_model=RuleOut)
def get_rule(rule_id: int, ctx: Ctx, session: ReadSession) -> RuleOut:
    return RuleOut.of(automation_service.get_rule(session, ctx.shop_id, rule_id))


@router.post("/{rule_id}/activate", response_model=RuleOut)
def activate(rule_id: int, ctx: Ctx) -> RuleOut:
    with write_transaction() as session:
        return RuleOut.of(automation_service.set_active(session, ctx, rule_id, True))


@router.post("/{rule_id}/deactivate", response_model=RuleOut)
def deactivate(rule_id: int, ctx: Ctx) -> RuleOut:
    with write_transaction() as session:
        return RuleOut.of(automation_service.set_active(session, ctx, rule_id, False))


@router.post("/{rule_id}/run", response_model=RunResultOut)
def run_now(rule_id: int, ctx: Ctx) -> RunResultOut:
    with write_transaction() as session:
        today = shop_today(get_shop(session, ctx.shop_id))
        result = automation_service.run_rule(session, ctx, rule_id, today)
        return RunResultOut(
            run_status=result.run_status.value, customers_matched=result.customers_matched,
            customers_actioned=result.customers_actioned, detail=result.detail,
        )  # fmt: skip
