from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import AccountStatus, AdminRole
from app.schemas.common import Page

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class AdminMeOut(BaseModel):
    email: str
    display_name: str
    role: AdminRole
    permissions: list[str]


class ShopOut(BaseModel):
    id: int
    name: str
    account_status: AccountStatus
    status_reason: str | None
    status_changed_at: datetime | None
    plan_code: str
    plan_source: str
    subscription_status: str | None
    created_at: datetime
    products: int
    users: int


class ShopListOut(Page):
    items: list[ShopOut]


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AccountStatus
    reason: Reason


class SubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    trial_days: int | None = Field(default=None, ge=1, le=365)
    notes: Annotated[str, StringConstraints(max_length=300)] | None = None


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admin_email: str
    reason: Reason
    hours: int = Field(default=4, ge=1, le=72)


class GrantOut(BaseModel):
    id: int
    shop_id: int
    admin_email: str
    reason: str
    expires_at: datetime


class SupportViewOut(BaseModel):
    """Account and health for one shop. Counts and statuses only: never sales, customers, amounts or documents."""

    shop: ShopOut
    usage: dict[str, int]
    events: list[dict[str, Any]]
    integrity: dict[str, Any]


class EventOut(BaseModel):
    id: int
    shop_id: int | None
    category: str
    severity: str
    source: str
    code: str
    message: str
    request_id: str | None
    created_at: datetime


class EventListOut(Page):
    items: list[EventOut]


class AdminAuditOut(BaseModel):
    id: int
    admin_id: int | None
    action: str
    permission: str | None
    outcome: str
    target_shop_id: int | None
    detail: dict[str, Any] | None
    request_id: str | None
    created_at: datetime


class AdminAuditListOut(Page):
    items: list[AdminAuditOut]


class BackupOut(BaseModel):
    backup_key: str
    kind: str
    status: str
    storage_provider: str
    size_bytes: int | None
    sha256: str | None
    schema_revision: str | None
    initiated_by: str
    error_code: str | None
    error_message: str | None
    created_at: datetime
    verified_at: datetime | None
    deleted_at: datetime | None


class VerificationOut(BaseModel):
    ok: bool
    checks: dict[str, str]
    schema_revision: str | None
    compatibility: str | None


class RestoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Annotated[str, StringConstraints(max_length=100)]


class RestoreOut(BaseModel):
    ok: bool
    mode: str
    backup_key: str
    detail: str
    pre_restore_key: str | None
    report: dict[str, int] | None
    verification: VerificationOut | None
    confirmation_phrase: str


class RetentionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apply: bool = False  # false = a dry run that only lists what would be deleted


class RetentionOut(BaseModel):
    dry_run: bool
    keep: list[str]
    delete: list[str]
    deleted: list[str]


class IntegrityOut(BaseModel):
    ok: bool
    errors: int
    warnings: int
    findings: list[dict[str, Any]]
    read_only: bool = True  # this check never repairs anything
