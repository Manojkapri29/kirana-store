"""Marketing automation: event/rule-based triggers, each with an explicit trigger, condition, action,
cooldown and execution history (`AutomationRun`, insert-only). No action here ever sends a message, grants a
reward, or changes money on its own:

* `CREATE_CAMPAIGN_DRAFT` creates a DRAFT `Campaign` (never launched) targeting a fresh MANUAL group of the
  customers the rule found — a person still reviews and launches it (`campaign_service.launch`).
* `CREATE_TASK` creates an ordinary `BusinessTask` per customer, through the existing `task_service`.
* `NOTIFY` raises one ordinary in-app notification (to the shop's staff) summarising what the rule found,
  through the existing `notification_service`.

Idempotency is a cooldown check against `AutomationRun` history, not a unique constraint: a rule may
legitimately fire again for the same customer once its `cooldown_days` has passed.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import AutomationRule, AutomationRun, Customer
from app.models.enums import (
    AutomationAction,
    AutomationRunStatus,
    AutomationTrigger,
    NotificationChannel,
)
from app.services import (
    campaign_service,
    crm_segment_service,
    loyalty_service,
    notification_service,
    task_service,
)
from app.services import customer_intelligence_service as cis
from app.services.audit_service import record_audit
from app.services.errors import InvalidInputError, NotFoundError


def _get(session: Session, shop_id: int, rule_id: int) -> AutomationRule:
    rule = session.scalar(
        select(AutomationRule).where(AutomationRule.shop_id == shop_id, AutomationRule.id == rule_id)
    )
    if rule is None:
        raise NotFoundError("Automation rule not found")
    return rule


def get_rule(session: Session, shop_id: int, rule_id: int) -> AutomationRule:
    return _get(session, shop_id, rule_id)


def list_rules(session: Session, shop_id: int, *, is_active: bool | None = None) -> list[AutomationRule]:
    query = select(AutomationRule).where(AutomationRule.shop_id == shop_id)
    if is_active is not None:
        query = query.where(AutomationRule.is_active == is_active)
    return list(session.scalars(query.order_by(AutomationRule.name)))


def create_rule(
    session: Session, ctx: RequestContext, *, name: str, trigger_type: AutomationTrigger,
    conditions: dict[str, Any], action_type: AutomationAction, action_config: dict[str, Any],
    cooldown_days: int = 30,
) -> AutomationRule:  # fmt: skip
    if not name.strip():
        raise InvalidInputError("Give the rule a name.", field="name")
    if cooldown_days < 0:
        raise InvalidInputError("The cooldown cannot be negative.", field="cooldown_days")
    rule = AutomationRule(
        shop_id=ctx.shop_id, name=name.strip(), trigger_type=trigger_type, conditions=conditions,
        action_type=action_type, action_config=action_config, cooldown_days=cooldown_days,
        created_by=ctx.user_id,
    )  # fmt: skip
    session.add(rule)
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="automation_rule",
        entity_id=rule.id,
        action="automation_rule_created",
        after={"name": name, "trigger_type": trigger_type.value},
    )
    return rule


def set_active(session: Session, ctx: RequestContext, rule_id: int, active: bool) -> AutomationRule:
    rule = _get(session, ctx.shop_id, rule_id)
    rule.is_active = active
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="automation_rule",
        entity_id=rule.id,
        action="automation_rule_activated" if active else "automation_rule_deactivated",
    )
    return rule


def _last_run_date(session: Session, shop_id: int, rule_id: int, customer_id: int) -> date | None:
    ran_at = session.scalar(
        select(func.max(AutomationRun.ran_at)).where(
            AutomationRun.shop_id == shop_id, AutomationRun.rule_id == rule_id,
            AutomationRun.customer_id == customer_id, AutomationRun.status == AutomationRunStatus.SUCCESS,
        )
    )  # fmt: skip
    return ran_at.date() if ran_at else None


def _eligible_customers(session: Session, shop_id: int, rule: AutomationRule, today: date) -> list[int]:
    conditions = rule.conditions or {}
    if rule.trigger_type is AutomationTrigger.NEW_CUSTOMER:
        within_days = conditions.get("within_days", 1)
        cutoff = today - timedelta(days=within_days)
        rows = session.execute(
            select(Customer.id, Customer.created_at).where(
                Customer.shop_id == shop_id, Customer.is_active.is_(True)
            )
        )
        return [cid for cid, created_at in rows if created_at.date() >= cutoff]

    if rule.trigger_type is AutomationTrigger.INACTIVITY:
        inactive_days = conditions.get("inactive_days")
        if inactive_days is None:
            raise InvalidInputError("This rule needs an 'inactive_days' condition.", field="conditions")
        require_opt_in = conditions.get("requires_marketing_opt_in", True)
        rows = cis.list_analytics(session, shop_id, today, active_days=inactive_days, limit=None)
        customers = {
            c.id: c
            for c in session.scalars(
                select(Customer).where(
                    Customer.shop_id == shop_id, Customer.id.in_([r.customer_id for r in rows])
                )
            )
        }
        out = []
        for row in rows:
            if cis.CustomerSegment.INACTIVE not in row.segments:
                continue
            customer = customers.get(row.customer_id)
            if customer is None:
                continue
            if require_opt_in and not any(
                (
                    customer.marketing_opt_in_email,
                    customer.marketing_opt_in_sms,
                    customer.marketing_opt_in_whatsapp,
                    customer.marketing_opt_in_push,
                )
            ):
                continue
            out.append(row.customer_id)
        return out

    if rule.trigger_type is AutomationTrigger.LOYALTY_MILESTONE:
        threshold = conditions.get("points_threshold")
        if threshold is None:
            raise InvalidInputError("This rule needs a 'points_threshold' condition.", field="conditions")
        ids = list(
            session.scalars(
                select(Customer.id).where(Customer.shop_id == shop_id, Customer.is_active.is_(True))
            )
        )
        balances = loyalty_service.get_balance_map(session, shop_id, ids)
        return [cid for cid, points in balances.items() if points >= threshold]

    if rule.trigger_type is AutomationTrigger.PURCHASE_MILESTONE:
        threshold = conditions.get("purchase_count_threshold")
        if threshold is None:
            raise InvalidInputError(
                "This rule needs a 'purchase_count_threshold' condition.", field="conditions"
            )
        rows = cis.list_analytics(session, shop_id, today, limit=None)
        return [r.customer_id for r in rows if (r.detailed_sale_count + r.quick_sale_count) >= threshold]

    return []


@dataclass(frozen=True)
class RunResult:
    run_status: AutomationRunStatus
    customers_matched: int
    customers_actioned: int
    detail: str


def run_rule(session: Session, ctx: RequestContext, rule_id: int, today: date) -> RunResult:
    """Evaluate one rule now. Every customer still in cooldown is skipped; every action taken is safe
    (a draft, a task, or a staff notification — never a send, a reward or a financial change)."""
    rule = _get(session, ctx.shop_id, rule_id)
    if not rule.is_active:
        return RunResult(AutomationRunStatus.SKIPPED_CONDITION, 0, 0, "Rule is not active")

    matched = _eligible_customers(session, ctx.shop_id, rule, today)
    due = []
    for customer_id in matched:
        last_run = _last_run_date(session, ctx.shop_id, rule.id, customer_id)
        if last_run is None or (today - last_run).days >= rule.cooldown_days:
            due.append(customer_id)
    if not due:
        return RunResult(
            AutomationRunStatus.SKIPPED_CONDITION,
            len(matched),
            0,
            "No customer is due (matched but in cooldown, or none matched)",
        )

    now = utc_now()
    try:
        with session.begin_nested():  # a failing action leaves nothing half-done
            detail = _perform_action(session, ctx, rule, due, today)
    except Exception as exc:  # noqa: BLE001 - recorded on the run, then reported, never swallowed silently
        message = str(exc)[:300] or exc.__class__.__name__
        for customer_id in due:
            session.add(
                AutomationRun(
                    shop_id=ctx.shop_id, rule_id=rule.id, customer_id=customer_id,
                    status=AutomationRunStatus.FAILED,
                    result={"action": rule.action_type.value, "error": message}, ran_at=now,
                )
            )  # fmt: skip
        session.flush()
        record_audit(
            session, ctx, entity_type="automation_rule", entity_id=rule.id, action="automation_rule_failed",
            after={"error": message},
        )  # fmt: skip
        return RunResult(AutomationRunStatus.FAILED, len(matched), 0, f"The action failed: {message}")
    for customer_id in due:
        session.add(
            AutomationRun(
                shop_id=ctx.shop_id,
                rule_id=rule.id,
                customer_id=customer_id,
                status=AutomationRunStatus.SUCCESS,
                result={"action": rule.action_type.value},
                ran_at=now,
            )
        )
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="automation_rule",
        entity_id=rule.id,
        action="automation_rule_run",
        after={"customers_actioned": len(due)},
    )
    return RunResult(AutomationRunStatus.SUCCESS, len(matched), len(due), detail)


def _perform_action(
    session: Session, ctx: RequestContext, rule: AutomationRule, customer_ids: list[int], today: date
) -> str:
    config = rule.action_config or {}
    if rule.action_type is AutomationAction.CREATE_CAMPAIGN_DRAFT:
        group = crm_segment_service.create_manual_group(
            session, ctx, name=f"{rule.name} — {today.isoformat()}", customer_ids=customer_ids
        )
        campaign_service.create(
            session, ctx, name=f"{rule.name} ({today.isoformat()})",
            description=f"Auto-drafted by automation rule '{rule.name}'.",
            channel=NotificationChannel(config.get("channel", "IN_APP")), target_group_id=group.id,
            target_segment=None, promotion_id=config.get("promotion_id"),
            message_template=(
                config.get("message_template", "").strip() or "(write a message before launching)"
            ),
        )  # fmt: skip
        return f"Drafted a campaign for {len(customer_ids)} customer(s); review and launch it manually."
    if rule.action_type is AutomationAction.CREATE_TASK:
        for customer_id in customer_ids:
            task_service.create(
                session,
                ctx,
                title=config.get("title", rule.name),
                description=config.get("description"),
                kind="AUTOMATION",
                entity_type="customer",
                entity_id=customer_id,
            )
        return f"Created {len(customer_ids)} task(s)."
    if rule.action_type is AutomationAction.NOTIFY:
        notification_service.emit_safely(
            session, ctx.shop_id, "BUSINESS_ALERT",
            title=f"Automation: {rule.name}",
            message=f"{len(customer_ids)} customer(s) matched '{rule.name}'.",
            dedupe_key=f"automation:{rule.id}:{today.isoformat()}",
            entity_type="automation_rule", entity_id=rule.id,
        )  # fmt: skip
        return f"Notified staff about {len(customer_ids)} customer(s)."
    return "No action configured."


def run_due(session: Session, ctx: RequestContext, today: date) -> list[RunResult]:
    """Every active rule for this shop, run once. There is no scheduler calling this yet (see the docs)."""
    return [
        run_rule(session, ctx, rule.id, today) for rule in list_rules(session, ctx.shop_id, is_active=True)
    ]
