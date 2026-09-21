from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import AccountStatus
from app.schemas.common import Page


class AccountOut(BaseModel):
    shop_name: str
    account_status: AccountStatus
    message: str | None  # a safe sentence for the owner when the account is restricted
    plan_code: str
    plan_name: str
    subscription_status: str | None
    capabilities: dict[str, bool]


class UsageItemOut(BaseModel):
    used: int
    limit: int | None
    remaining: int | None
    unlimited: bool
    percent_used: int | None


class UsageOut(BaseModel):
    period: str
    plan_code: str
    limits: dict[str, UsageItemOut]


class BackupStatusOut(BaseModel):
    state: str  # recent, stale, none, not_configured
    last_backup_at: str | None


class AlertOut(BaseModel):
    kind: str
    severity: str
    title: str
    message: str


class HealthOut(BaseModel):
    alerts: list[AlertOut]
    backup: BackupStatusOut


class NotificationOut(BaseModel):
    id: int
    event_type: str
    category: str
    title: str
    message: str
    created_at: datetime
    read: bool
    entity_type: str | None
    entity_id: int | None


class NotificationListOut(Page):
    items: list[NotificationOut]
    unread: int


class UnreadOut(BaseModel):
    unread: int


class PreferencesOut(BaseModel):
    preferences: dict[str, dict[str, bool]]
    channels: dict[
        str, bool
    ]  # which channels can actually deliver (only in-app, until a provider is configured)


class PreferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: dict[str, bool]


class AuditEntryOut(BaseModel):
    id: int
    action: str
    entity_type: str
    entity_id: int | None
    user_id: int | None
    request_id: str | None
    created_at: datetime


class AuditListOut(Page):
    items: list[AuditEntryOut]


class OverviewQuery(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    bucket: str = "day"


OverviewOut = dict[str, Any]
