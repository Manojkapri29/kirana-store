"""AI actions: a change the AI prepared, and what a person did with it.

    AI proposes (structured, validated)  ->  the person sees an exact preview  ->  Confirm / Edit / Cancel
        -> on Confirm, an EXISTING service does the work in one transaction  ->  the result is checked

The AI never writes. There is no path from a model, a document or a question to the database except this one,
and it
runs only when a person presses Confirm. What Confirm can do is a short fixed list, each one an existing
service:

    PURCHASE_DRAFT     purchase_service.create_purchase        a DRAFT: nothing is posted, no stock moves
    STOCK_ADJUSTMENT   inventory_service.record_adjustment     with a reason code, against the stock seen
    at preview
    PROMOTION_DRAFT    promotion_service.create_promotion      a DRAFT: it applies to nothing until activated

Posting a purchase, activating an offer, refunds, price changes, khata entries and cancelling orders are NOT
actions
the AI can prepare. Every step is audited (who, which shop, which AI feature, what was proposed, what was
confirmed,
the resulting record ids, success or failure and the error reference), and no prompt or document text is
stored.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.db.types import utc_now
from app.models import AiAction, Product, Supplier, Unit
from app.models.enums import (
    AdjustmentReason,
    AiActionKind,
    AiActionStatus,
    NotificationChannel,
    PromotionScope,
    PromotionType,
    TaskPriority,
)
from app.services import (
    ai_format as fmt,
)
from app.services import (
    authorization_service,
    campaign_service,
    entitlement_service,
    inventory_service,
    promotion_service,
    purchase_service,
    task_service,
)
from app.services.audit_service import record_audit
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop

MAX_LINES = 200
FEATURES = {
    "purchase_suggestions",
    "reorder",
    "promotion_ideas",
    "invoice_photo",
    "stock_list_photo",
    "assistant",
}
_NEEDS_FEATURE = {
    AiActionKind.PURCHASE_DRAFT: "ai_assistant",
    AiActionKind.STOCK_ADJUSTMENT: "ai_assistant",
    AiActionKind.PROMOTION_DRAFT: "ai_assistant",
    AiActionKind.TASK_DRAFT: "ai_assistant",
    AiActionKind.CAMPAIGN_DRAFT: "ai_assistant",
}
_DOCUMENT_FEATURES = {"invoice_photo", "stock_list_photo"}


# --- Payload models (validated before anything is stored) ---------------------------------------------------


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PurchaseLine(_Payload):
    product_id: int
    quantity: Decimal = Field(gt=0, le=Decimal("1000000"))
    unit_cost: Decimal | None = Field(default=None, ge=0, le=Decimal("10000000"))
    discount: Decimal | None = Field(default=None, ge=0, le=Decimal("10000000"))

    @field_validator("unit_cost", "discount", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if value in ("", None) else value


class PurchaseDraftPayload(_Payload):
    supplier_id: int
    purchase_date: date | None = None
    supplier_invoice_no: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)
    items: list[PurchaseLine] = Field(min_length=1, max_length=MAX_LINES)


class AdjustmentLine(_Payload):
    product_id: int
    counted_quantity: Decimal = Field(ge=0, le=Decimal("1000000"))
    system_quantity: Decimal  # the stock the person was shown; the adjustment is refused if it has moved
    reason_code: AdjustmentReason = AdjustmentReason.COUNT_CORRECTION
    note: str | None = Field(default=None, max_length=200)


class StockAdjustmentPayload(_Payload):
    items: list[AdjustmentLine] = Field(min_length=1, max_length=MAX_LINES)


class PromotionDraftPayload(_Payload):
    name: str = Field(min_length=1, max_length=120)
    promo_type: PromotionType
    scope: PromotionScope = PromotionScope.CART
    percent: Decimal | None = None
    amount: Decimal | None = None
    min_cart_value: Decimal | None = None
    max_discount: Decimal | None = None
    product_ids: list[int] = Field(default_factory=list, max_length=500)
    category_ids: list[int] = Field(default_factory=list, max_length=500)

    @field_validator("promo_type")
    @classmethod
    def _only_simple_kinds(cls, value: PromotionType) -> PromotionType:
        if value not in (PromotionType.PERCENT, PromotionType.AMOUNT):
            raise ValueError("The assistant can only prepare percentage or amount offers.")
        return value


class TaskDraftPayload(_Payload):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    kind: str | None = Field(default=None, max_length=40)
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: date | None = None
    entity_type: str | None = Field(default=None, max_length=40)
    entity_id: int | None = None


class CampaignDraftPayload(_Payload):
    """A DRAFT campaign only — never launched by this payload. `campaign_service.launch` is a separate,
    separately-permissioned (`CAMPAIGN_LAUNCH`) step a person takes afterwards."""

    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    channel: NotificationChannel = NotificationChannel.IN_APP
    target_group_id: int | None = None
    target_segment: str | None = Field(default=None, max_length=40)
    promotion_id: int | None = None
    message_template: str = Field(min_length=1, max_length=4000)


_MODELS: dict[AiActionKind, type[_Payload]] = {
    AiActionKind.PURCHASE_DRAFT: PurchaseDraftPayload,
    AiActionKind.STOCK_ADJUSTMENT: StockAdjustmentPayload,
    AiActionKind.PROMOTION_DRAFT: PromotionDraftPayload,
    AiActionKind.TASK_DRAFT: TaskDraftPayload,
    AiActionKind.CAMPAIGN_DRAFT: CampaignDraftPayload,
}


def _clean(kind: AiActionKind, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        model = _MODELS[kind].model_validate(payload)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise InvalidInputError(f"{first['msg']}", field=where or None) from None
    return model.model_dump(mode="json")


# --- Views
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionView:
    action: AiAction
    preview: dict[str, Any]


def _get(session: Session, shop_id: int, action_id: int, *, lock: bool = False) -> AiAction:
    query = select(AiAction).where(AiAction.shop_id == shop_id, AiAction.id == action_id)
    action = session.scalar(query.with_for_update() if lock else query)
    if action is None:
        raise NotFoundError("Action not found")
    return action


def _products(session: Session, shop_id: int, ids: list[int]) -> dict[int, tuple[Product, str]]:
    rows = session.execute(
        select(Product, Unit.code)
        .join(Unit, Unit.id == Product.unit_id)
        .where(Product.shop_id == shop_id, Product.id.in_(ids))
    ).all()
    found = {product.id: (product, unit) for product, unit in rows}
    missing = set(ids) - set(found)
    if missing:
        raise NotFoundError(
            "A product in this draft was not found."
        )  # another shop's product looks like this too
    return found


def _line_total(quantity: Decimal, cost: Decimal | None, discount: Decimal | None) -> Decimal | None:
    if cost is None:
        return None
    try:
        return purchase_service.compute_line_total(quantity, cost, discount or Decimal("0"))
    except ValueError:
        return None


def preview_of(
    session: Session, ctx: RequestContext, kind: AiActionKind, payload: dict[str, Any]
) -> dict[str, Any]:
    """Exactly what confirming would do, read from the database now. Nothing is changed by building it."""
    shop = get_shop(session, ctx.shop_id)
    base: dict[str, Any] = {"shop": shop.name, "problems": [], "warnings": [], "totals": [], "impact": []}
    problems: list[str] = base["problems"]
    warnings: list[str] = base["warnings"]

    if kind is AiActionKind.PURCHASE_DRAFT:
        data = PurchaseDraftPayload.model_validate(payload)
        supplier = session.scalar(
            select(Supplier).where(Supplier.shop_id == ctx.shop_id, Supplier.id == data.supplier_id)
        )
        if supplier is None:
            raise NotFoundError("Supplier not found")
        if not supplier.is_active:
            problems.append("This supplier is inactive.")
        products = _products(session, ctx.shop_id, [i.product_id for i in data.items])
        rows, total, unpriced = [], Decimal("0"), 0
        for line in data.items:
            product, unit = products[line.product_id]
            if not product.is_active:
                problems.append(f"{product.name} is inactive.")
            amount = _line_total(line.quantity, line.unit_cost, line.discount)
            if line.unit_cost is None:
                unpriced += 1
            else:
                if amount is None:
                    problems.append(f"The discount on {product.name} is more than the line amount.")
                total += amount or Decimal("0")
            rows.append(
                [
                    product.name,
                    f"{fmt.quantity(line.quantity)} {unit}",
                    fmt.money(line.unit_cost) if line.unit_cost is not None else "— (enter a price)",
                    fmt.money(amount) if amount is not None else "—",
                ]
            )
        if unpriced:
            problems.append(
                f"Enter the price for {unpriced} line{'s' if unpriced != 1 else ''} before confirming."
            )
        base.update(
            title="Create Purchase Draft",
            summary=f"Supplier: {supplier.name}",
            supplier=supplier.name,
            lines={"columns": ["Product", "Quantity", "Unit price", "Line total"], "rows": rows},
            totals=[{"label": "Estimated total", "value": fmt.money(total)}],
            impact=[
                f"This will create a purchase DRAFT for {supplier.name} with {len(data.items)} line{'s' if len(data.items) != 1 else ''}.",
                "Nothing is posted and no stock changes. You review the draft and post it yourself.",
            ],
        )
    elif kind is AiActionKind.STOCK_ADJUSTMENT:
        data_adj = StockAdjustmentPayload.model_validate(payload)
        products = _products(session, ctx.shop_id, [i.product_id for i in data_adj.items])
        stock = inventory_service.get_stock_map(session, ctx.shop_id, [i.product_id for i in data_adj.items])
        rows, net_units, changed = [], Decimal("0"), 0
        seen: set[int] = set()
        for line in data_adj.items:
            product, unit = products[line.product_id]
            if line.product_id in seen:
                problems.append(f"{product.name} appears twice.")
            seen.add(line.product_id)
            now = stock.get(line.product_id, Decimal("0"))
            if now != line.system_quantity:
                problems.append(
                    f"Stock of {product.name} has changed since this was prepared (was {fmt.quantity(line.system_quantity)}, now {fmt.quantity(now)}). Use 'Update to current stock'."
                )
            delta = line.counted_quantity - line.system_quantity
            if not product.is_active:
                problems.append(f"{product.name} is inactive.")
            if delta == 0:
                warnings.append(f"{product.name}: the count matches the system, so nothing changes for it.")
            else:
                changed += 1
                net_units += delta
                if line.reason_code is AdjustmentReason.OTHER and not (line.note or "").strip():
                    problems.append(f"Describe the reason for {product.name}.")
            rows.append(
                [
                    product.name,
                    f"{fmt.quantity(line.system_quantity)} {unit}",
                    f"{fmt.quantity(line.counted_quantity)} {unit}",
                    f"{'+' if delta > 0 else ''}{fmt.quantity(delta)}",
                    line.reason_code.value.replace("_", " ").title(),
                ]
            )
        if changed == 0:
            problems.append("There is no difference to adjust.")
        base.update(
            title="Create Inventory Adjustment",
            summary=f"{changed} product{'s' if changed != 1 else ''} will change",
            lines={"columns": ["Product", "System stock", "Counted", "Difference", "Reason"], "rows": rows},
            totals=[
                {
                    "label": "Net change in units",
                    "value": f"{'+' if net_units > 0 else ''}{fmt.quantity(net_units)}",
                }
            ],
            impact=[
                f"This will create {changed} inventory adjustment{'s' if changed != 1 else ''} in the stock ledger (net {'+' if net_units > 0 else ''}{fmt.quantity(net_units)} units), each with its reason.",
                "Stock will match your count. Each entry stays in the product's history and can be reversed.",
            ],
        )
    elif kind is AiActionKind.TASK_DRAFT:
        data_task = TaskDraftPayload.model_validate(payload)
        base.update(
            title="Create Task",
            summary=data_task.title,
            lines={
                "columns": ["Title", "Priority", "Due"],
                "rows": [
                    [
                        data_task.title,
                        data_task.priority.value.title(),
                        data_task.due_date.isoformat() if data_task.due_date else "—",
                    ]
                ],
            },
            impact=[
                f"This will create a task: '{data_task.title}'.",
                "It changes nothing else: no stock, price, order or financial record is touched.",
            ],
        )
    elif kind is AiActionKind.CAMPAIGN_DRAFT:
        data_campaign = CampaignDraftPayload.model_validate(payload)
        base.update(
            title="Create Campaign Draft",
            summary=data_campaign.name,
            lines={
                "columns": ["Name", "Channel", "Target"],
                "rows": [
                    [
                        data_campaign.name,
                        data_campaign.channel.value,
                        (
                            f"Group #{data_campaign.target_group_id}"
                            if data_campaign.target_group_id
                            else (data_campaign.target_segment or "—")
                        ),
                    ]
                ],
            },
            impact=[
                f"This will create a DRAFT campaign: '{data_campaign.name}'.",
                "Nothing is sent: a person must review the audience and launch it separately.",
            ],
        )
    else:
        data_pro = PromotionDraftPayload.model_validate(payload)
        if data_pro.scope is PromotionScope.PRODUCTS:
            names = (
                [p.name for p, _u in _products(session, ctx.shop_id, data_pro.product_ids).values()]
                if data_pro.product_ids
                else []
            )
            if not names:
                problems.append("Choose at least one product for this offer.")
        else:
            names = []
        terms = (
            f"{data_pro.percent}% off"
            if data_pro.promo_type is PromotionType.PERCENT
            else f"{fmt.money(data_pro.amount)} off"
        )
        if data_pro.min_cart_value:
            terms += f" above {fmt.money(data_pro.min_cart_value)}"
        base.update(
            title="Create Offer Draft",
            summary=f"{data_pro.name}: {terms}",
            lines={
                "columns": ["Offer", "Applies to"],
                "rows": [[data_pro.name, ", ".join(names) if names else data_pro.scope.value.title()]],
            },
            impact=[
                f"This will create an offer DRAFT: {terms}.",
                "It applies to nothing until you activate it from the Offers page.",
            ],
        )
    base["can_confirm"] = not problems
    return base


def _view(session: Session, ctx: RequestContext, action: AiAction) -> ActionView:
    if action.status in (AiActionStatus.PROPOSED, AiActionStatus.FAILED):
        try:
            preview = preview_of(session, ctx, action.kind, action.current)
        except NotFoundError as error:
            preview = {
                "shop": get_shop(session, ctx.shop_id).name,
                "title": action.kind.value,
                "problems": [error.message],
                "warnings": [],
                "totals": [],
                "impact": [],
                "can_confirm": False,
            }
    else:
        preview = {
            "shop": get_shop(session, ctx.shop_id).name,
            "title": action.kind.value,
            "problems": [],
            "warnings": [],
            "totals": [],
            "impact": [],
            "can_confirm": False,
        }
    return ActionView(action, preview)


# --- Life cycle --------------------------------------------------------------------------------------------


# What the person must hold for the action the assistant proposes: it can never do what they could not do themselves.
ACTION_PERMISSION = {
    AiActionKind.PURCHASE_DRAFT: "PURCHASE_CREATE",
    AiActionKind.STOCK_ADJUSTMENT: "INVENTORY_ADJUST",
    AiActionKind.PROMOTION_DRAFT: "PROMOTION_CREATE",
    AiActionKind.TASK_DRAFT: "TASK_CREATE",
    AiActionKind.CAMPAIGN_DRAFT: "CAMPAIGN_MANAGE",
}


def propose(
    session: Session, ctx: RequestContext, *, kind: AiActionKind, feature: str, payload: dict[str, Any]
) -> ActionView:
    """Record what the AI proposes. Nothing happens to the shop's data."""
    authorization_service.require(ctx, ACTION_PERMISSION[kind])
    entitlement_service.require_feature(session, ctx.shop_id, _NEEDS_FEATURE[kind])
    if feature not in FEATURES:
        raise InvalidInputError("Unknown assistant feature.", field="feature")
    if feature in _DOCUMENT_FEATURES:
        entitlement_service.require_feature(session, ctx.shop_id, "ai_documents")
    elif feature != "assistant":
        entitlement_service.require_feature(session, ctx.shop_id, "ai_insights")
    clean = _clean(kind, payload)
    preview_of(session, ctx, kind, clean)  # refuses unknown suppliers and products at once
    action = AiAction(
        shop_id=ctx.shop_id,
        created_by=ctx.user_id,
        kind=kind,
        status=AiActionStatus.PROPOSED,
        feature=feature,
        proposal=clean,
        current=clean,
        attempts=0,
    )
    session.add(action)
    session.flush()
    record_audit(
        session,
        ctx,
        entity_type="ai_action",
        entity_id=action.id,
        action="propose",
        after={"kind": kind.value, "feature": feature, "proposed": clean},
    )
    return _view(session, ctx, action)


def get(session: Session, ctx: RequestContext, action_id: int) -> ActionView:
    return _view(session, ctx, _get(session, ctx.shop_id, action_id))


def list_actions(
    session: Session, ctx: RequestContext, *, status: AiActionStatus | None = None, limit: int = 30
) -> list[AiAction]:
    query = select(AiAction).where(AiAction.shop_id == ctx.shop_id).order_by(AiAction.id.desc()).limit(limit)
    if status is not None:
        query = query.where(AiAction.status == status)
    return list(session.scalars(query))


def _open(action: AiAction) -> None:
    if action.status not in (AiActionStatus.PROPOSED, AiActionStatus.FAILED):
        raise ConflictError(f"This action is already {action.status.value.lower()}.", code="action_closed")


def edit(session: Session, ctx: RequestContext, action_id: int, payload: dict[str, Any]) -> ActionView:
    """Change what a confirmation would do. The first proposal is kept as it was."""
    action = _get(session, ctx.shop_id, action_id, lock=True)
    _open(action)
    clean = _clean(action.kind, payload)
    preview_of(session, ctx, action.kind, clean)
    before = action.current
    action.current = clean
    action.status = AiActionStatus.PROPOSED
    action.failure_message = None
    action.reference_id = None
    record_audit(
        session,
        ctx,
        entity_type="ai_action",
        entity_id=action.id,
        action="edit",
        before={"current": before},
        after={"current": clean},
    )
    session.flush()
    return _view(session, ctx, action)


def refresh_stock(session: Session, ctx: RequestContext, action_id: int) -> ActionView:
    """For a stock adjustment: set every line's 'system stock' to the stock right now (the counted quantities stay)."""
    action = _get(session, ctx.shop_id, action_id, lock=True)
    _open(action)
    if action.kind is not AiActionKind.STOCK_ADJUSTMENT:
        raise InvalidInputError("Only a stock adjustment has system stock to update.")
    payload = dict(action.current)
    stock = inventory_service.get_stock_map(session, ctx.shop_id, [i["product_id"] for i in payload["items"]])
    payload["items"] = [
        {**i, "system_quantity": str(stock.get(i["product_id"], Decimal("0")))} for i in payload["items"]
    ]
    return edit(session, ctx, action_id, payload)


def cancel(session: Session, ctx: RequestContext, action_id: int) -> ActionView:
    action = _get(session, ctx.shop_id, action_id, lock=True)
    _open(action)
    action.status = AiActionStatus.CANCELLED
    action.decided_by = ctx.user_id
    action.decided_at = utc_now()
    record_audit(
        session,
        ctx,
        entity_type="ai_action",
        entity_id=action.id,
        action="cancel",
        after={"kind": action.kind.value, "feature": action.feature},
    )
    session.flush()
    return _view(session, ctx, action)


def confirm(session: Session, ctx: RequestContext, action_id: int) -> ActionView:
    """Do what the person confirmed, through the existing service, then check the result. All or nothing."""
    action = _get(session, ctx.shop_id, action_id, lock=True)
    authorization_service.require(ctx, "AI_ACTION_CONFIRM", ACTION_PERMISSION[action.kind])
    _open(action)
    preview = preview_of(session, ctx, action.kind, action.current)
    if not preview["can_confirm"]:
        raise InvalidInputError(preview["problems"][0], field="action")
    action.attempts += 1
    if action.kind is AiActionKind.PURCHASE_DRAFT:
        result_type, ids = _run_purchase(session, ctx, action)
    elif action.kind is AiActionKind.STOCK_ADJUSTMENT:
        result_type, ids = _run_adjustment(session, ctx, action)
    elif action.kind is AiActionKind.TASK_DRAFT:
        result_type, ids = _run_task(session, ctx, action)
    elif action.kind is AiActionKind.CAMPAIGN_DRAFT:
        result_type, ids = _run_campaign(session, ctx, action)
    else:
        result_type, ids = _run_promotion(session, ctx, action)
    action.status = AiActionStatus.EXECUTED
    action.result_type = result_type
    action.result_ids = ids
    action.decided_by = ctx.user_id
    action.decided_at = utc_now()
    action.failure_message = None
    action.reference_id = None
    record_audit(
        session,
        ctx,
        entity_type="ai_action",
        entity_id=action.id,
        action="confirm",
        after={
            "kind": action.kind.value,
            "feature": action.feature,
            "proposed": action.proposal,
            "confirmed": action.current,
            "result": {"type": result_type, "ids": ids},
            "outcome": "success",
        },
    )
    session.flush()
    return _view(session, ctx, action)


def record_failure(
    session: Session, ctx: RequestContext, action_id: int, *, message: str, reference_id: str | None
) -> None:
    """Called in a NEW transaction after a confirmation failed and rolled back: keep the failure on the action and in
    the audit log. The action stays open, so it can be edited and confirmed again."""
    action = _get(session, ctx.shop_id, action_id, lock=True)
    if action.status in (AiActionStatus.EXECUTED, AiActionStatus.CANCELLED):
        return
    action.status = AiActionStatus.FAILED
    action.attempts += 1
    action.failure_message = message[:300]
    action.reference_id = reference_id
    record_audit(
        session,
        ctx,
        entity_type="ai_action",
        entity_id=action.id,
        action="confirm_failed",
        after={
            "kind": action.kind.value,
            "feature": action.feature,
            "confirmed": action.current,
            "outcome": "failure",
            "message": message[:300],
            "reference_id": reference_id,
        },
    )


# --- Executors: each calls one existing service
# ---------------------------------------------------------------


def _run_task(session: Session, ctx: RequestContext, action: AiAction) -> tuple[str, list[int]]:
    data = TaskDraftPayload.model_validate(action.current)
    task = task_service.create(
        session, ctx, title=data.title, description=data.description, kind=data.kind, priority=data.priority,
        due_date=data.due_date, entity_type=data.entity_type, entity_id=data.entity_id,
    )  # fmt: skip
    return "task", [task.id]


def _run_campaign(session: Session, ctx: RequestContext, action: AiAction) -> tuple[str, list[int]]:
    data = CampaignDraftPayload.model_validate(action.current)
    campaign = campaign_service.create(
        session, ctx, name=data.name, description=data.description, channel=data.channel,
        target_group_id=data.target_group_id, target_segment=data.target_segment,
        promotion_id=data.promotion_id, message_template=data.message_template,
    )  # fmt: skip
    return "campaign", [campaign.id]


def _run_purchase(session: Session, ctx: RequestContext, action: AiAction) -> tuple[str, list[int]]:
    data = PurchaseDraftPayload.model_validate(action.current)
    notes = (
        (data.notes or "").strip()
        or f"Draft prepared with the AI assistant ({action.feature.replace('_', ' ')}). Review before posting."
    )
    header = {
        "supplier_id": data.supplier_id,
        "purchase_date": data.purchase_date,
        "supplier_invoice_no": data.supplier_invoice_no,
        "notes": notes,
    }
    items = [
        {
            "product_id": i.product_id,
            "quantity": i.quantity,
            "unit_cost": i.unit_cost,
            "discount": i.discount or Decimal("0"),
        }
        for i in data.items
    ]
    view = purchase_service.create_purchase(session, ctx, header, items)
    # Verify: it is a draft, with every line, and the total is the sum of the lines.
    expected = sum(
        (_line_total(i.quantity, i.unit_cost, i.discount) or Decimal("0") for i in data.items), Decimal("0")
    )
    if (
        view.purchase.status.value != "DRAFT"
        or len(view.items) != len(data.items)
        or view.purchase.total_amount != expected
    ):
        raise ConflictError(
            "The draft that was created does not match what was confirmed, so nothing was kept.",
            code="verification_failed",
        )
    return "purchase", [view.purchase.id]


def _run_adjustment(session: Session, ctx: RequestContext, action: AiAction) -> tuple[str, list[int]]:
    data = StockAdjustmentPayload.model_validate(action.current)
    ids = [i.product_id for i in data.items]
    inventory_service.lock_products(session, ctx.shop_id, ids)  # one fixed order: no deadlocks
    created: list[int] = []
    for line in data.items:
        now = inventory_service.get_stock(session, ctx.shop_id, line.product_id)
        if now != line.system_quantity:
            raise ConflictError(
                "Stock has changed since this was prepared. Update to the current stock and check the count.",
                code="stale_proposal",
            )
        delta = line.counted_quantity - now
        if delta == 0:
            continue
        note = (line.note or "").strip() or "Stock count (prepared with the AI assistant)"
        row = inventory_service.record_adjustment(
            session,
            ctx,
            product_id=line.product_id,
            quantity_delta=delta,
            reason_code=line.reason_code,
            note=note,
        )
        created.append(row.id)
    for line in data.items:  # verify: stock now equals the count
        if inventory_service.get_stock(session, ctx.shop_id, line.product_id) != line.counted_quantity:
            raise ConflictError(
                "Stock does not match the count after adjusting, so nothing was kept.",
                code="verification_failed",
            )
    return "inventory_adjustment", created


def _run_promotion(session: Session, ctx: RequestContext, action: AiAction) -> tuple[str, list[int]]:
    data = PromotionDraftPayload.model_validate(action.current)
    values: dict[str, Any] = {
        "name": data.name,
        "promo_type": data.promo_type,
        "scope": data.scope,
        "description": "Prepared with the AI assistant. Review before activating.",
    }
    for key in ("percent", "amount", "min_cart_value", "max_discount"):
        if getattr(data, key) is not None:
            values[key] = getattr(data, key)
    if data.product_ids:
        values["product_ids"] = data.product_ids
    if data.category_ids:
        values["category_ids"] = data.category_ids
    view = promotion_service.create_promotion(session, ctx, values)
    if view.promotion.status.value != "DRAFT":
        raise ConflictError(
            "The offer was not created as a draft, so nothing was kept.", code="verification_failed"
        )
    return "promotion", [view.promotion.id]
