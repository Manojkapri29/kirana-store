from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import SavedReport


class ReportDefinitionIn(BaseModel):
    """The builder's definition. It is data: field names and operators from the allowlist, never SQL."""

    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(default_factory=list, max_length=20)
    group_by: list[str] = Field(default_factory=list, max_length=5)
    aggregations: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    filters: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    sort: list[dict[str, Any]] = Field(default_factory=list, max_length=3)


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str
    definition: ReportDefinitionIn


class SavedReportIn(PreviewIn):
    name: str = Field(max_length=80)
    description: str | None = Field(default=None, max_length=300)


class SavedReportOut(BaseModel):
    id: int
    name: str
    description: str | None
    dataset: str
    definition: dict[str, Any]
    is_archived: bool
    created_by: int
    updated_by: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, r: SavedReport) -> "SavedReportOut":
        return cls(
            id=r.id, name=r.name, description=r.description, dataset=r.dataset, definition=r.definition,
            is_archived=r.is_archived, created_by=r.created_by, updated_by=r.updated_by,
            created_at=r.created_at, updated_at=r.updated_at,
        )  # fmt: skip


class SavedReportListOut(BaseModel):
    items: list[SavedReportOut]
