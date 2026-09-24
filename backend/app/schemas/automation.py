from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import AutomationRule
from app.models.enums import AutomationAction, AutomationTrigger

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]


class RuleCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    trigger_type: AutomationTrigger
    conditions: dict[str, Any] = Field(default_factory=dict)
    action_type: AutomationAction
    action_config: dict[str, Any] = Field(default_factory=dict)
    cooldown_days: int = Field(default=30, ge=0)


class RuleOut(BaseModel):
    id: int
    name: str
    trigger_type: AutomationTrigger
    conditions: dict[str, Any]
    action_type: AutomationAction
    action_config: dict[str, Any]
    cooldown_days: int
    is_active: bool
    created_by: int
    created_at: datetime

    @classmethod
    def of(cls, r: AutomationRule) -> "RuleOut":
        return cls(
            id=r.id, name=r.name, trigger_type=r.trigger_type, conditions=r.conditions,
            action_type=r.action_type, action_config=r.action_config, cooldown_days=r.cooldown_days,
            is_active=r.is_active, created_by=r.created_by, created_at=r.created_at,
        )  # fmt: skip


class RuleListOut(BaseModel):
    items: list[RuleOut]


class RunResultOut(BaseModel):
    run_status: str
    customers_matched: int
    customers_actioned: int
    detail: str


class RunResultsOut(BaseModel):
    items: list[RunResultOut]
