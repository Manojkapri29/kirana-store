from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import Campaign, CampaignSend
from app.models.enums import CampaignSendStatus, CampaignStatus, NotificationChannel
from app.schemas.common import Page

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class CampaignCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    description: str | None = Field(default=None, max_length=2000)
    channel: NotificationChannel
    target_group_id: int | None = None
    target_segment: str | None = Field(default=None, max_length=40)
    promotion_id: int | None = None
    message_template: Message
    start_at: datetime | None = None
    end_at: datetime | None = None


class CampaignUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    description: str | None = Field(default=None, max_length=2000)
    channel: NotificationChannel | None = None
    target_group_id: int | None = None
    target_segment: str | None = Field(default=None, max_length=40)
    promotion_id: int | None = None
    message_template: Message | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


class ScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_at: datetime


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class CampaignOut(BaseModel):
    id: int
    name: str
    description: str | None
    status: CampaignStatus
    channel: NotificationChannel
    target_group_id: int | None
    target_segment: str | None
    promotion_id: int | None
    message_template: str
    start_at: datetime | None
    end_at: datetime | None
    created_by: int
    launched_at: datetime | None
    launched_by: int | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    requires_approval: bool
    created_at: datetime

    @classmethod
    def of(cls, c: Campaign) -> "CampaignOut":
        return cls(
            id=c.id, name=c.name, description=c.description, status=c.status, channel=c.channel,
            target_group_id=c.target_group_id, target_segment=c.target_segment,
            promotion_id=c.promotion_id, message_template=c.message_template, start_at=c.start_at,
            end_at=c.end_at, created_by=c.created_by, launched_at=c.launched_at,
            launched_by=c.launched_by, cancelled_at=c.cancelled_at, cancel_reason=c.cancel_reason,
            requires_approval=c.requires_approval, created_at=c.created_at,
        )  # fmt: skip


class CampaignListOut(Page):
    items: list[CampaignOut]


class AudiencePreviewOut(BaseModel):
    customer_ids: list[int]
    audience_size: int


class SendOutcomeOut(BaseModel):
    id: int
    customer_id: int
    channel: NotificationChannel
    status: CampaignSendStatus
    detail: str | None

    @classmethod
    def of(cls, s: CampaignSend) -> "SendOutcomeOut":
        return cls(id=s.id, customer_id=s.customer_id, channel=s.channel, status=s.status, detail=s.detail)


class SendOutcomesOut(BaseModel):
    items: list[SendOutcomeOut]
