from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import StockCount
from app.models.enums import StockCountScope, StockCountStatus
from app.schemas.common import Page
from app.services.stock_count_service import ItemView

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class StockCountCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    scope: StockCountScope
    category_id: int | None = None
    product_ids: list[int] | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)


class CountEntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int
    counted_quantity: Decimal = Field(ge=0)
    note: str | None = Field(default=None, max_length=200)


class EnterCountsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[CountEntryIn] = Field(min_length=1, max_length=500)


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class StockCountItemOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    sku: str
    unit_code: str
    expected_quantity: Decimal
    counted_quantity: Decimal | None
    variance: Decimal | None
    unit_cost_snapshot: Decimal | None
    variance_value: Decimal | None
    note: str | None

    @classmethod
    def of(cls, i: ItemView) -> "StockCountItemOut":
        return cls(**i.__dict__)


class StockCountOut(BaseModel):
    id: int
    title: str
    scope: StockCountScope
    category_id: int | None
    status: StockCountStatus
    notes: str | None
    requires_approval: bool
    created_by: int
    reviewed_by: int | None
    reviewed_at: datetime | None
    approved_by: int | None
    approved_at: datetime | None
    posted_by: int | None
    posted_at: datetime | None
    cancelled_by: int | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    created_at: datetime
    item_count: int | None = None
    items: list[StockCountItemOut] | None = None

    @classmethod
    def of(
        cls, c: StockCount, *, item_count: int | None = None, items: list[ItemView] | None = None
    ) -> "StockCountOut":
        return cls(
            id=c.id, title=c.title, scope=c.scope, category_id=c.category_id, status=c.status, notes=c.notes,
            requires_approval=c.requires_approval, created_by=c.created_by, reviewed_by=c.reviewed_by,
            reviewed_at=c.reviewed_at, approved_by=c.approved_by, approved_at=c.approved_at,
            posted_by=c.posted_by, posted_at=c.posted_at, cancelled_by=c.cancelled_by,
            cancelled_at=c.cancelled_at, cancel_reason=c.cancel_reason, created_at=c.created_at,
            item_count=item_count,
            items=[StockCountItemOut.of(i) for i in items] if items is not None else None,
        )  # fmt: skip


class StockCountListOut(Page):
    items: list[StockCountOut]
