"""The assistant: question in, answer built from real data out.

    question -> guard (refuse what is never done) -> plan (router, else the provider) -> tool (read-only, this
    shop only) -> answer (templated from the tool's figures) -> usage row

The assistant cannot write. Its answers may carry *proposals* (a purchase draft, an offer draft) that a person
can
choose to turn into an action; that goes through `ai_action_service` and needs a confirmation, and nothing in
this
module creates one. The question text is never stored.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.context import RequestContext
from app.models.enums import EventSeverity
from app.services import ai_planner, ai_tools, ai_usage_service, entitlement_service, system_event_service
from app.services.ai_answer import (
    NOT_CONFIGURED,
    NOT_CONFIGURED_TEXT,
    NOT_UNDERSTOOD,
    REFUSED,
    Answer,
)
from app.services.ai_provider import AiProvider, ProviderReply, configured_provider
from app.services.errors import AiServiceError, ForbiddenError, InvalidInputError, NotFoundError
from app.services.shop_service import get_shop, shop_today

SUGGESTED_QUESTIONS = {
    "en": [
        ("Today's Sales", "How much did I sell today?"),
        ("Low Stock", "Which products are low in stock?"),
        ("Top Products", "Which 10 products sold the most this month?"),
        ("Outstanding Customers", "Which customers have the highest outstanding balance?"),
        ("Purchase Suggestions", "What should I purchase this week?"),
        ("Online Orders", "How many online orders did I receive?"),
        ("Sales vs Last Month", "Show me sales vs last month."),
        ("Monthly Summary", "Generate my monthly business summary."),
    ],
    "hi": [
        ("आज की बिक्री", "आज कितनी सेल हुई?"),
        ("कम स्टॉक", "कौन से उत्पाद कम स्टॉक में हैं?"),
        ("टॉप उत्पाद", "इस महीने सबसे ज़्यादा कौन से 10 उत्पाद बिके?"),
        ("बकाया ग्राहक", "ग्राहकों का कितना बकाया है?"),
        ("खरीद के सुझाव", "इस हफ्ते मुझे क्या खरीदना चाहिए?"),
        ("ऑनलाइन ऑर्डर", "मुझे कितने ऑनलाइन ऑर्डर मिले?"),
        ("पिछले महीने से तुलना", "पिछले महीने से बिक्री की तुलना दिखाओ।"),
        ("मासिक सार", "मेरा मासिक व्यापार सार बनाओ।"),
    ],
}


@dataclass
class AskOutcome:
    answer: Answer
    provider_error: AiServiceError | None = None  # raised by the API layer once the usage row is safely saved


def _language(value: str) -> str:
    return value if value in ("en", "hi") else "en"


def _unusable(text: str, tool: str | None = None) -> Answer:
    return Answer(NOT_UNDERSTOOD, tool, "Assistant", text)


def ask(
    session: Session,
    ctx: RequestContext,
    question: str,
    *,
    language: str = "en",
    provider: AiProvider | None = None,
    settings: Settings | None = None,
) -> AskOutcome:
    """Answer one question. Raises EntitlementError if the plan lacks the assistant or the month's allowance is spent,
    and ForbiddenError for a question about another shop. Provider trouble is returned, not raised, so the
    caller can
    save the failure record first."""
    sid = ctx.shop_id
    entitlement_service.require_feature(session, sid, ai_tools.BASIC)
    lang = _language(language)
    text = ai_planner.sanitize(question)
    if not text:
        raise InvalidInputError("Type a question first.", field="question")

    refusal = ai_planner.guard(text)
    if refusal is not None:
        if refusal.kind == "other_shop":
            raise ForbiddenError(refusal.message)
        return AskOutcome(Answer(REFUSED, None, "Assistant", refusal.message))

    settings = settings or get_settings()
    today = shop_today(get_shop(session, sid))
    planned = ai_planner.route(text, today)
    if isinstance(planned, ai_planner.Clarify):
        return AskOutcome(_unusable(planned.message))

    reply: ProviderReply | None = None
    used_provider: AiProvider | None = None
    if planned is None:
        used_provider = provider if provider is not None else configured_provider(settings)
        if used_provider is None:
            return AskOutcome(
                Answer(
                    NOT_CONFIGURED,
                    None,
                    "Assistant",
                    f"{NOT_CONFIGURED_TEXT} I can answer the ready-made questions below, but I cannot understand other "
                    "wording until an AI provider is set up.",
                    follow_ups=[q for _label, q in SUGGESTED_QUESTIONS[lang][:4]],
                )
            )
        ai_usage_service.check_allowance(session, sid)  # do not spend a provider call the plan does not allow
        try:
            planned, reply = ai_planner.plan_with_provider(used_provider, text, today)
        except AiServiceError as error:
            ai_usage_service.record(
                session,
                ctx,
                feature="ask",
                provider=used_provider.name,
                model=None,
                status=ai_usage_service.FAILED,
            )
            system_event_service.record(
                session,
                category="ai",
                severity=EventSeverity.WARNING,
                source=f"ai:{used_provider.name}",
                code=error.reason,
                message="The AI provider could not answer.",
                shop_id=ctx.shop_id,
            )
            return AskOutcome(_unusable(""), error)
        if planned is None:
            ai_usage_service.record(
                session,
                ctx,
                feature="ask",
                provider=used_provider.name,
                model=reply.model,
                status="UNSUPPORTED",
                usage=reply.usage,
            )
            return AskOutcome(
                _unusable(
                    "I didn't understand that as a question about your shop's data. Try one of the suggested questions.",
                )
            )

    ai_usage_service.check_allowance(session, sid)
    try:
        answer = ai_tools.run_tool(session, ctx, planned.tool, dict(planned.args), lang)
    except InvalidInputError as error:
        if error.field in ("period", "date_from", "date_to"):
            return AskOutcome(_unusable(error.message, planned.tool))
        raise
    ai_usage_service.count_request(session, sid)
    ai_usage_service.record(
        session,
        ctx,
        feature="ask",
        provider=used_provider.name if used_provider else None,
        model=reply.model if reply else None,
        status=ai_usage_service.OK,
        usage=reply.usage if reply else None,
    )
    answer.provider_used = reply is not None
    return AskOutcome(answer)


def run_tool_directly(
    session: Session, ctx: RequestContext, tool: str, args: dict[str, Any], *, language: str = "en"
) -> Answer:
    """Run one named tool (an insights or recommendations screen asking for it). Same checks and counting as `ask`."""
    if tool not in ai_tools.TOOLS:
        raise NotFoundError("That report does not exist.")
    ai_usage_service.check_allowance(session, ctx.shop_id)
    answer = ai_tools.run_tool(session, ctx, tool, args, _language(language))
    ai_usage_service.count_request(session, ctx.shop_id)
    ai_usage_service.record(
        session, ctx, feature="tool", provider=None, model=None, status=ai_usage_service.OK
    )
    return answer


def suggested_questions(language: str) -> list[dict[str, str]]:
    return [
        {"label": label, "question": question} for label, question in SUGGESTED_QUESTIONS[_language(language)]
    ]
