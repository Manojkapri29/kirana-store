from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import AiActionKind, AiActionStatus
from app.services.ai_action_service import ActionView
from app.services.ai_answer import Answer
from app.services.document_intelligence_service import Extraction, MatchResult

Language = Annotated[str, StringConstraints(pattern="^(en|hi)$")]
Base64Image = Annotated[str, StringConstraints(min_length=16, max_length=21_000_000)]


class AskIn(BaseModel):
    """A question. Its text is used to answer it and is not stored."""

    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
    language: Language = "en"


class ToolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    args: dict[str, Any] = Field(default_factory=dict)
    language: Language = "en"


class FigureOut(BaseModel):
    label: str
    value: str
    note: str | None = None


class TableOut(BaseModel):
    columns: list[str]
    rows: list[list[str]]


class ProposalOut(BaseModel):
    kind: str
    feature: str
    label: str
    payload: dict[str, Any]


class AnswerOut(BaseModel):
    status: str
    tool: str | None
    title: str
    message: str
    figures: list[FigureOut]
    table: TableOut | None
    sources: list[str]
    period: dict[str, str] | None
    notes: list[str]
    badges: list[str]
    proposals: list[ProposalOut]
    export: dict[str, Any] | None
    follow_ups: list[str]
    provider_used: bool

    @classmethod
    def from_answer(cls, answer: Answer) -> "AnswerOut":
        return cls.model_validate(asdict(answer))


class SuggestionOut(BaseModel):
    label: str
    question: str


class UsageOut(BaseModel):
    period: str
    requests: int
    limit: int | None
    failed: int
    input_tokens: int
    output_tokens: int
    by_feature: dict[str, int]


class AiStatusOut(BaseModel):
    """Whether AI is available. Never a key, a model name or a provider setting."""

    configured: bool
    provider_label: str | None
    documents: bool
    features: dict[str, bool | None]
    usage: UsageOut | None
    suggestions: list[SuggestionOut]


# --- Documents
# ------------------------------------------------------------------------------------------------


class ExtractIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    image_base64: Base64Image
    content_type: str | None = Field(default=None, max_length=50)


class ExtractionOut(BaseModel):
    status: str
    message: str | None
    kind: str
    header: dict[str, Any]
    rows: list[dict[str, Any]]
    warnings: list[str]
    provider_label: str | None
    stored: bool = False  # the document is not kept; nothing was created

    @classmethod
    def from_extraction(cls, e: Extraction) -> "ExtractionOut":
        return cls(
            status=e.status,
            message=e.message,
            kind=e.kind,
            header=e.header,
            rows=e.rows,
            warnings=e.warnings,
            provider_label=e.provider_label,
        )


class MatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    header: dict[str, Any] = Field(default_factory=dict)
    rows: Annotated[list[dict[str, Any]], Field(max_length=200)]


class MatchOut(BaseModel):
    kind: str
    header: dict[str, Any]
    rows: list[dict[str, Any]]
    counts: dict[str, int]
    can_propose: bool
    problems: list[str]
    changes_data: bool = False  # matching never creates or changes anything

    @classmethod
    def from_result(cls, r: MatchResult) -> "MatchOut":
        return cls(
            kind=r.kind,
            header=r.header,
            rows=r.rows,
            counts=r.counts,
            can_propose=r.can_propose,
            problems=r.problems,
        )


# --- Actions
# --------------------------------------------------------------------------------------------------


class ProposeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AiActionKind
    feature: str
    payload: dict[str, Any]


class EditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]


class ActionOut(BaseModel):
    id: int
    kind: AiActionKind
    status: AiActionStatus
    feature: str
    created_at: datetime
    decided_at: datetime | None
    attempts: int
    current: dict[str, Any]
    preview: dict[str, Any]
    result_type: str | None
    result_ids: list[int] | None
    failure_message: str | None
    reference_id: str | None

    @classmethod
    def from_view(cls, v: ActionView) -> "ActionOut":
        a = v.action
        return cls(
            id=a.id,
            kind=a.kind,
            status=a.status,
            feature=a.feature,
            created_at=a.created_at,
            decided_at=a.decided_at,
            attempts=a.attempts,
            current=a.current,
            preview=v.preview,
            result_type=a.result_type,
            result_ids=a.result_ids,
            failure_message=a.failure_message,
            reference_id=a.reference_id,
        )


class ActionRowOut(BaseModel):
    id: int
    kind: AiActionKind
    status: AiActionStatus
    feature: str
    created_at: datetime
    result_type: str | None
    result_ids: list[int] | None


class ActionListOut(BaseModel):
    items: list[ActionRowOut]
