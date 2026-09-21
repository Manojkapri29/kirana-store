"""The AI business assistant, document intelligence and AI actions.

Reading is the default: `ask` and `tools/{name}` only ever run the fixed read-only tools, scoped to the
signed-in
shop. Business changes exist only as *actions*: the AI (or a document) proposes, a person reviews the exact
preview
and confirms, and an existing service does the work. No endpoint here accepts SQL, a shop id, or a free-form
write.
There is no DELETE: an unwanted action is cancelled, and the record of it stays.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import Ctx, feature_flag, rate_limited
from app.core import diagnostics
from app.core.config import get_settings
from app.core.context import RequestContext
from app.db.session import get_session, write_transaction
from app.models.enums import AiActionStatus
from app.schemas.ai import (
    ActionListOut,
    ActionOut,
    ActionRowOut,
    AiStatusOut,
    AnswerOut,
    AskIn,
    EditIn,
    ExtractIn,
    ExtractionOut,
    MatchIn,
    MatchOut,
    ProposeIn,
    SuggestionOut,
    ToolIn,
    UsageOut,
)
from app.services import (
    ai_action_service,
    ai_assistant_service,
    ai_provider,
    ai_usage_service,
    document_intelligence_service,
    entitlement_service,
)
from app.services.errors import DomainError, NotFoundError

router = APIRouter(
    prefix="/ai",
    tags=["ai"],
    dependencies=[Depends(feature_flag("ai")), Depends(rate_limited("ai"))],
)
ReadSession = Annotated[Session, Depends(get_session)]


@router.get("/status", response_model=AiStatusOut)
def status(ctx: Ctx, session: ReadSession, language: str = "en") -> AiStatusOut:
    entitlements = entitlement_service.get_entitlements(session, ctx.shop_id)
    features = {key: entitlements.allows(key) for key in ("ai_assistant", "ai_insights", "ai_documents")}
    usage = ai_usage_service.summary(session, ctx.shop_id) if features["ai_assistant"] else None
    info = ai_provider.provider_status(get_settings())
    return AiStatusOut(
        configured=info["configured"],
        provider_label=info["provider_label"],
        documents=info["documents"],
        features=features,
        usage=UsageOut(**usage.__dict__) if usage else None,
        suggestions=[SuggestionOut(**s) for s in ai_assistant_service.suggested_questions(language)],
    )


@router.post("/ask", response_model=AnswerOut)
def ask(payload: AskIn, ctx: Ctx) -> AnswerOut:
    """Answer one question from the shop's own data. Read-only; the question is not stored."""
    with write_transaction() as session:  # writes only the usage row
        outcome = ai_assistant_service.ask(session, ctx, payload.question, language=payload.language)
    if outcome.provider_error is not None:
        raise outcome.provider_error  # after the failure record is saved
    return AnswerOut.from_answer(outcome.answer)


@router.post("/tools/{name}", response_model=AnswerOut)
def run_tool(name: str, payload: ToolIn, ctx: Ctx) -> AnswerOut:
    """Run one named read-only tool (used by the insights and recommendation screens)."""
    with write_transaction() as session:
        answer = ai_assistant_service.run_tool_directly(
            session, ctx, name, payload.args, language=payload.language
        )
    return AnswerOut.from_answer(answer)


# --- Document intelligence
# ------------------------------------------------------------------------------------


@router.post(
    "/documents/extract", response_model=ExtractionOut, dependencies=[Depends(rate_limited("image"))]
)
def extract_document(payload: ExtractIn, ctx: Ctx) -> ExtractionOut:
    """Read a photographed invoice or stock list into rows. Nothing is stored and nothing is created."""
    with write_transaction() as session:
        result = document_intelligence_service.extract(
            session,
            ctx,
            kind=payload.kind,
            image_base64=payload.image_base64,
            content_type=payload.content_type,
        )
    if result.provider_error is not None:
        raise result.provider_error
    return ExtractionOut.from_extraction(result)


@router.post("/documents/match", response_model=MatchOut)
def match_document(payload: MatchIn, ctx: Ctx, session: ReadSession) -> MatchOut:
    """Check rows (read from a photo, or typed) and match them to products. Reads only."""
    result = document_intelligence_service.match_rows(
        session, ctx, kind=payload.kind, header=payload.header, rows=payload.rows
    )
    return MatchOut.from_result(result)


# --- Actions: propose, preview, edit, confirm, cancel
# ---------------------------------------------------------


@router.post("/actions", response_model=ActionOut, status_code=201)
def propose(payload: ProposeIn, ctx: Ctx) -> ActionOut:
    """Record what the AI proposes. Nothing changes until it is confirmed."""
    with write_transaction() as session:
        view = ai_action_service.propose(
            session, ctx, kind=payload.kind, feature=payload.feature, payload=payload.payload
        )
        return ActionOut.from_view(view)


@router.get("/actions", response_model=ActionListOut)
def list_actions(ctx: Ctx, session: ReadSession, status: AiActionStatus | None = None) -> ActionListOut:
    rows = ai_action_service.list_actions(session, ctx, status=status)
    return ActionListOut(
        items=[
            ActionRowOut(
                id=a.id,
                kind=a.kind,
                status=a.status,
                feature=a.feature,
                created_at=a.created_at,
                result_type=a.result_type,
                result_ids=a.result_ids,
            )
            for a in rows
        ]
    )


@router.get("/actions/{action_id}", response_model=ActionOut)
def get_action(action_id: int, ctx: Ctx, session: ReadSession) -> ActionOut:
    return ActionOut.from_view(ai_action_service.get(session, ctx, action_id))


@router.patch("/actions/{action_id}", response_model=ActionOut)
def edit_action(action_id: int, payload: EditIn, ctx: Ctx) -> ActionOut:
    with write_transaction() as session:
        return ActionOut.from_view(ai_action_service.edit(session, ctx, action_id, payload.payload))


@router.post("/actions/{action_id}/refresh-stock", response_model=ActionOut)
def refresh_stock(action_id: int, ctx: Ctx) -> ActionOut:
    with write_transaction() as session:
        return ActionOut.from_view(ai_action_service.refresh_stock(session, ctx, action_id))


@router.post("/actions/{action_id}/cancel", response_model=ActionOut)
def cancel_action(action_id: int, ctx: Ctx) -> ActionOut:
    with write_transaction() as session:
        return ActionOut.from_view(ai_action_service.cancel(session, ctx, action_id))


@router.post("/actions/{action_id}/confirm", response_model=ActionOut)
def confirm_action(action_id: int, ctx: Ctx) -> ActionOut:
    """Do what the person confirmed, through the existing service. If it fails, nothing is kept, the
    failure is
    recorded on the action (with the error reference when unexpected), and the action stays open."""
    try:
        with write_transaction() as session:
            return ActionOut.from_view(ai_action_service.confirm(session, ctx, action_id))
    except Exception as error:
        _record_failure(ctx, action_id, error)
        raise


def _record_failure(ctx: RequestContext, action_id: int, error: Exception) -> None:
    """Keep the failure of a confirmation on the action and in the audit log (in a new transaction, since the
    failed one was rolled back). An unexpected failure is given the error reference the person will be
    shown."""

    if isinstance(error, NotFoundError) and error.message == "Action not found":
        return
    if isinstance(error, DomainError):
        message, reference = error.message, None
    else:
        reference = diagnostics.new_reference_id()
        error.reference_id = reference  # type: ignore[attr-defined]  # the error layer shows this same one
        message = "Something went wrong while completing this action."
    try:
        with write_transaction() as session:
            ai_action_service.record_failure(session, ctx, action_id, message=message, reference_id=reference)
    except Exception:  # noqa: BLE001  (recording a failure must never hide the original error)
        return
