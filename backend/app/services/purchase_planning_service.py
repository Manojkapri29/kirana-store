"""Purchase planning: turns the reorder recommendations (`ai_insights_service`) into a workspace for building
a purchase draft — never into a purchase itself. The recommendations are read-only figures; the only way
stock is ever ordered is the ordinary, unchanged `purchase_service.create_purchase` (a DRAFT) and, later,
`purchase_service.post_purchase` under its own existing permission. This module adds nothing new to that
path, only a convenient way to select lines supplied to it and hand them to it.

A line's price is never invented here: `purchase_service` itself refuses a line with no `unit_cost`, so a
product whose cost is unknown must have a price entered before it can be drafted — the recommendation only
ever *suggests* one (when a purchase price or average cost is on record) for the person to accept or
change."""

from datetime import date

from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.services import ai_insights_service, purchase_service
from app.services.errors import InvalidInputError
from app.services.purchase_service import PurchaseView

Grouped = list[ai_insights_service.SupplierGroup]


def suggestions(
    session: Session,
    shop_id: int,
    today: date,
    *,
    window_days: int = ai_insights_service.WINDOW_DAYS,
    cover_days: int = ai_insights_service.COVER_DAYS,
) -> Grouped:
    """The reorder list grouped by supplier, exactly as the assistant sees it. Nothing is created by reading
    this."""
    return ai_insights_service.purchase_suggestions(
        session, shop_id, today, window_days=window_days, cover_days=cover_days
    )


def build_draft(
    session: Session,
    ctx: RequestContext,
    *,
    supplier_id: int,
    lines: list[dict],
    purchase_date: date | None = None,
    notes: str | None = None,
) -> PurchaseView:
    """Create a purchase DRAFT from selected, possibly edited, lines:
    `[{"product_id", "quantity", "unit_cost"}, ...]`. A line without a price is refused by
    `purchase_service` with a clear message naming which one."""
    if not lines:
        raise InvalidInputError("Choose at least one product.", field="lines")
    header = {"supplier_id": supplier_id, "purchase_date": purchase_date, "notes": notes}
    return purchase_service.create_purchase(session, ctx, header, lines)
