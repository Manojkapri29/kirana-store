from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models import ApprovalRequest
from app.models.enums import ApprovalStatus
from app.schemas.common import Page


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class ApprovalRequestOut(BaseModel):
    id: int
    kind: str
    entity_type: str
    entity_id: int
    status: ApprovalStatus
    reason: str
    threshold_value: Decimal | None
    observed_value: Decimal | None
    requested_by: int
    decided_by: int | None
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime

    @classmethod
    def of(cls, a: ApprovalRequest) -> "ApprovalRequestOut":
        return cls(
            id=a.id, kind=a.kind, entity_type=a.entity_type, entity_id=a.entity_id, status=a.status,
            reason=a.reason, threshold_value=a.threshold_value, observed_value=a.observed_value,
            requested_by=a.requested_by, decided_by=a.decided_by, decided_at=a.decided_at,
            decision_note=a.decision_note, created_at=a.created_at,
        )  # fmt: skip


class ApprovalListOut(Page):
    items: list[ApprovalRequestOut]
