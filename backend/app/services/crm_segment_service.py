"""Customer segmentation and groups: rule-based filtering over
`customer_intelligence_service.CustomerAnalytics` (never a second analytics engine), and saved
`CustomerGroup`s used to target campaigns.

A rule is a plain dict of named filters (see `evaluate_rule`) — configurable, not hardcoded, and every filter
is a factual, explainable threshold (days since a purchase, a spend amount, a transaction count), never a
label of character. A MANUAL group's membership is whatever a person put in it; a RULE_BASED group's
membership is a cached snapshot, recomputed by `recalculate` and never silently drifting on its own.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import Customer, CustomerGroup, CustomerGroupMember
from app.models.enums import CustomerGroupKind
from app.services import customer_intelligence_service as cis
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

RULE_KEYS = {
    "segment", "new_days", "active_days", "frequent_visits", "long_inactive_days", "high_value_threshold",
    "frequent_buyer_visits", "min_total_spend", "max_total_spend", "min_transactions", "max_transactions",
    "min_outstanding", "max_outstanding", "created_within_days",
}  # fmt: skip


def evaluate_rule(
    session: Session, shop_id: int, today: date, rule: dict[str, Any]
) -> list[cis.CustomerAnalytics]:
    """Every customer matching `rule`. Unknown keys are refused (a typo in a saved rule should not silently
    match everyone); every threshold is read from the rule, never invented."""
    unknown = set(rule) - RULE_KEYS
    if unknown:
        raise InvalidInputError(f"Unknown rule filter(s): {', '.join(sorted(unknown))}.")

    segment = cis.CustomerSegment(rule["segment"]) if rule.get("segment") else None
    kwargs: dict[str, Any] = {}
    for key in ("new_days", "active_days", "frequent_visits", "long_inactive_days", "frequent_buyer_visits"):
        if key in rule:
            kwargs[key] = rule[key]
    if "high_value_threshold" in rule:
        kwargs["high_value_threshold"] = Decimal(str(rule["high_value_threshold"]))

    rows = cis.list_analytics(session, shop_id, today, segment=segment, limit=None, **kwargs)

    if "min_total_spend" in rule:
        threshold = Decimal(str(rule["min_total_spend"]))
        rows = [r for r in rows if r.total_purchases >= threshold]
    if "max_total_spend" in rule:
        threshold = Decimal(str(rule["max_total_spend"]))
        rows = [r for r in rows if r.total_purchases <= threshold]
    if "min_transactions" in rule:
        rows = [r for r in rows if (r.detailed_sale_count + r.quick_sale_count) >= rule["min_transactions"]]
    if "max_transactions" in rule:
        rows = [r for r in rows if (r.detailed_sale_count + r.quick_sale_count) <= rule["max_transactions"]]
    if "min_outstanding" in rule:
        threshold = Decimal(str(rule["min_outstanding"]))
        rows = [r for r in rows if r.outstanding >= threshold]
    if "max_outstanding" in rule:
        threshold = Decimal(str(rule["max_outstanding"]))
        rows = [r for r in rows if r.outstanding <= threshold]
    if "created_within_days" in rule:
        created = dict(
            session.execute(select(Customer.id, Customer.created_at).where(Customer.shop_id == shop_id))
        )
        cutoff_days = rule["created_within_days"]
        rows = [
            r
            for r in rows
            if r.customer_id in created and (today - created[r.customer_id].date()).days <= cutoff_days
        ]
    return rows


# --- Groups ------------------------------------------------------------------------------------------------


def create_manual_group(
    session: Session, ctx: RequestContext, *, name: str, customer_ids: list[int]
) -> CustomerGroup:
    if not name.strip():
        raise InvalidInputError("Give the group a name.", field="name")
    group = CustomerGroup(
        shop_id=ctx.shop_id, name=name.strip(), kind=CustomerGroupKind.MANUAL, created_by=ctx.user_id
    )
    session.add(group)
    session.flush()
    _set_members(session, ctx.shop_id, group.id, customer_ids)
    record_audit(
        session, ctx, entity_type="customer_group", entity_id=group.id, action="customer_group_created",
        after={"name": name, "kind": "MANUAL", "members": len(customer_ids)},
    )  # fmt: skip
    return group


def create_rule_based_group(
    session: Session, ctx: RequestContext, *, name: str, rule: dict[str, Any], today: date
) -> CustomerGroup:
    if not name.strip():
        raise InvalidInputError("Give the group a name.", field="name")
    evaluate_rule(session, ctx.shop_id, today, rule)  # validate the rule before saving it
    group = CustomerGroup(
        shop_id=ctx.shop_id, name=name.strip(), kind=CustomerGroupKind.RULE_BASED, rule=rule,
        created_by=ctx.user_id,
    )  # fmt: skip
    session.add(group)
    session.flush()
    recalculate(session, ctx, group.id, today=today)
    record_audit(
        session, ctx, entity_type="customer_group", entity_id=group.id, action="customer_group_created",
        after={"name": name, "kind": "RULE_BASED", "rule": rule},
    )  # fmt: skip
    return group


def _get_group(session: Session, shop_id: int, group_id: int) -> CustomerGroup:
    group = session.scalar(
        select(CustomerGroup).where(CustomerGroup.shop_id == shop_id, CustomerGroup.id == group_id)
    )
    if group is None:
        raise NotFoundError("Customer group not found")
    return group


def get_group(session: Session, shop_id: int, group_id: int) -> CustomerGroup:
    return _get_group(session, shop_id, group_id)


def list_groups(session: Session, shop_id: int) -> list[CustomerGroup]:
    return list(
        session.scalars(
            select(CustomerGroup).where(CustomerGroup.shop_id == shop_id).order_by(CustomerGroup.name)
        )
    )


def _set_members(session: Session, shop_id: int, group_id: int, customer_ids: list[int]) -> None:
    wanted = set(customer_ids)
    if wanted:
        found = set(
            session.scalars(select(Customer.id).where(Customer.shop_id == shop_id, Customer.id.in_(wanted)))
        )
        if found != wanted:
            raise NotFoundError("One or more customers were not found.")
    session.query(CustomerGroupMember).filter(
        CustomerGroupMember.shop_id == shop_id, CustomerGroupMember.group_id == group_id
    ).delete()
    for customer_id in set(customer_ids):
        session.add(CustomerGroupMember(shop_id=shop_id, group_id=group_id, customer_id=customer_id))
    session.flush()


def set_manual_members(
    session: Session, ctx: RequestContext, group_id: int, customer_ids: list[int]
) -> CustomerGroup:
    group = _get_group(session, ctx.shop_id, group_id)
    if group.kind is not CustomerGroupKind.MANUAL:
        raise ConflictError(
            "Only a manual group's membership can be set directly; a rule-based group is recalculated."
        )
    _set_members(session, ctx.shop_id, group.id, customer_ids)
    record_audit(
        session, ctx, entity_type="customer_group", entity_id=group.id, action="customer_group_members_set",
        after={"members": len(set(customer_ids))},
    )  # fmt: skip
    return group


def recalculate(session: Session, ctx: RequestContext, group_id: int, *, today: date) -> CustomerGroup:
    """Recompute a RULE_BASED group's membership from its saved rule. A no-op error for a MANUAL group: its
    membership is never overwritten automatically."""
    group = _get_group(session, ctx.shop_id, group_id)
    if group.kind is not CustomerGroupKind.RULE_BASED:
        raise ConflictError("Only a rule-based group can be recalculated.")
    matches = evaluate_rule(session, ctx.shop_id, today, group.rule or {})
    _set_members(session, ctx.shop_id, group.id, [m.customer_id for m in matches])
    group.last_recalculated_at = utc_now()
    session.flush()
    record_audit(
        session, ctx, entity_type="customer_group", entity_id=group.id, action="customer_group_recalculated",
        after={"members": len(matches)},
    )  # fmt: skip
    return group


@dataclass(frozen=True)
class GroupMembership:
    group: CustomerGroup
    customer_ids: list[int]


def members_of(session: Session, shop_id: int, group_id: int) -> GroupMembership:
    group = _get_group(session, shop_id, group_id)
    ids = list(
        session.scalars(
            select(CustomerGroupMember.customer_id).where(
                CustomerGroupMember.shop_id == shop_id, CustomerGroupMember.group_id == group_id
            )
        )
    )
    return GroupMembership(group, ids)
