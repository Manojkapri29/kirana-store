"""Understanding a question: which read-only tool answers it, and what the assistant must refuse.

Two planners, one validation path:

  1. A deterministic router that knows the common questions in English, Hinglish and Hindi. It works with no
  AI
     provider at all, is instant and free, and cannot be talked into anything: it only ever returns a
     tool from the
     fixed list, or nothing.
  2. If a provider is configured and the router found nothing, the provider is asked to pick a tool. Its
  answer is
     untrusted: the tool name must be in the list and the arguments must pass the tool's own model
     (`extra="forbid"`).
     Anything else is thrown away as "not understood".

Before either runs, `guard` refuses what the assistant will never do: database commands, another shop's data,
changes to business records, and attempts to change its instructions. The refusals are decided here, by the
backend,
never left to a model. Shop isolation does not depend on this text matching: no tool can address another shop
at all.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.services import ai_dates
from app.services.ai_provider import AiProvider, ProviderReply
from app.services.ai_tools import TOOLS

MAX_QUESTION_CHARS = 500


@dataclass(frozen=True)
class PlannedCall:
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Refusal:
    kind: str  # sql | other_shop | write | instructions
    message: str


@dataclass(frozen=True)
class Clarify:
    message: str


def sanitize(question: str) -> str:
    """Control characters removed, whitespace collapsed, length capped. The text is data from here on."""
    cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", question)
    return re.sub(r"\s+", " ", cleaned).strip()[:MAX_QUESTION_CHARS]


def _norm(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"[^\wऀ-ॿ\s\-']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _any(text: str, *needles: str) -> bool:
    return any(re.search(rf"(?<![\wऀ-ॿ]){re.escape(n)}", text) for n in needles)


_SQL = re.compile(
    r"\b(select|insert|update|delete|drop|alter|truncate|create\s+table|union\s+select)\b.{0,80}"
    r"\b(from|into|table|set|where|values)\b|;\s*--|\bpragma\b|\bsqlite_master\b|\binformation_schema\b",
    re.I,
)
_OTHER_SHOP = re.compile(
    r"\b(another|other|different|someone else'?s?|competitor'?s?|neighbou?r'?s?)\s+(shop|store|business|dukaan|dukan)s?\b"
    r"|\b(all|every)\s+(the\s+)?shops\b|\bshop[\s_-]?id\b|\bshop\s*#?\s*\d+\b|\bother\s+tenants?\b"
    r"|(dusri|doosri|dusre|doosre|dusra)\s+(ki\s+|ke\s+)?(dukaan|dukan)|दूसरी दुकान|दूसरे की दुकान|दूसरी दुकान",
    re.I,
)
_INSTRUCTIONS = re.compile(
    r"ignore (all |any |the )?(previous|prior|above|earlier|your) (instructions|rules|prompt)|disregard .{0,30}(instructions|rules)"
    r"|(reveal|show|print|tell me) (me )?(your|the) (system )?(prompt|instructions|rules)|system prompt|developer mode|jailbreak"
    r"|you are now\b|act as (an? )?(admin|root|developer)|pretend (that )?you|\bapi[\s_-]?key\b|\bsecret key\b|override (the )?(rules|safety)",
    re.I,
)
_WRITE_VERBS = (
    "delete|remove|cancel|post|refund|change|update|set|adjust|increase|decrease|reduce|raise|lower|add|create|activate|"
    "approve|reject|issue|record|mark|void|reverse|edit|modify|fix|reset|clear|wipe|erase|write off|transfer|send|pay|buy|order|"
    "upgrade|downgrade|accept|deactivate|pause|expire"
)
_WRITE = re.compile(
    rf"^(please\s+|kindly\s+|now\s+|just\s+)?({_WRITE_VERBS})\b"
    rf"|\b(can|could|will|would|should)\s+you\s+({_WRITE_VERBS})\b"
    rf"|\b(i\s+want\s+(you\s+)?to|go\s+ahead\s+and|please)\s+({_WRITE_VERBS})\b"
    r"|\b(hatao|hata\s+do|mita\s+do|mita\s+de|delete\s+kar|refund\s+kar|badlo|badal\s+do|badal\s+de|band\s+kar|cancel\s+kar|"
    r"post\s+kar|kar\s+do|kardo|kar\s+de)\b",
    re.I,
)
_ALLOWED_CREATE = re.compile(
    r"\b(purchase|reorder|order)\s+(list|suggestion|draft)s?\b|\bpurchase\s+draft\b", re.I
)

REFUSAL_TEXT = {
    "sql": "I can't run database commands. I answer from your business records through fixed, read-only reports.",
    "other_shop": "I can only answer questions about your own shop.",
    "write": (
        "I can't change your records directly. I only read them. I can prepare a draft for you to review and confirm: "
        "a purchase draft from the reorder list, a stock adjustment from a counted list, or an offer draft."
    ),
    "instructions": "I can't follow instructions that try to change how I work. I only answer questions about your shop's data.",
}


INSTRUCTION_PATTERN = _INSTRUCTIONS
SQL_PATTERN = _SQL


def guard(question: str) -> Refusal | None:
    """Refuse what the assistant never does. Checked before any planning, in this order."""
    if _INSTRUCTIONS.search(question):
        return Refusal("instructions", REFUSAL_TEXT["instructions"])
    if _SQL.search(question):
        return Refusal("sql", REFUSAL_TEXT["sql"])
    if _OTHER_SHOP.search(question):
        return Refusal("other_shop", REFUSAL_TEXT["other_shop"])
    if _WRITE.search(question.strip()) and not _ALLOWED_CREATE.search(question):
        return Refusal("write", REFUSAL_TEXT["write"])
    return None


# --- The deterministic router ---------------------------------------------------------------------------

_COMPARE = re.compile(r"\b(vs\.?|versus|compared? (to|with)|comparison|tulna|se tulna)\b|तुलना")


def _period_argument(text: str, today: date) -> dict[str, Any]:
    """The phrase that names the period, if any. For a comparison ("this month vs last month") only what comes
    before the "vs" names the period; the comparison period is worked out by the backend."""
    part = _COMPARE.split(text)[0] if _COMPARE.search(text) else text
    try:
        found = ai_dates.resolve(part, today)
    except Exception:  # an impossible range: let the tool report it with its own message
        return {"period": part}
    return {"period": part} if found is not None else {}


def _limit(text: str) -> int | None:
    found = re.search(
        r"\b(?:top|first|best|last|worst|bottom)\s+(\d{1,2})\b|\b(\d{1,2})\s+(?:products?|items?|customers?|saman)\b",
        text,
    )
    if not found:
        return None
    value = int(found.group(1) or found.group(2))
    return value if 1 <= value <= 50 else None


def _product_name(text: str) -> str | None:
    found = re.search(
        r"(?:price\s+(?:of|for)|prices?\s+for|competitive\s+for|of|for)\s+(?:my\s+|the\s+)?(.+?)(?:\s+(?:competitive|in the market|market|compared).*)?$",
        text,
    )
    if not found:
        return None
    name = re.sub(r"\b(is|are|my|the|price|selling|competitive|good|fair|high|low)\b", " ", found.group(1))
    name = re.sub(r"\s+", " ", name).strip(" ?.")
    return name or None


def route(question: str, today: date) -> PlannedCall | Clarify | None:
    """The tool for a common question, or None. Never guesses a dangerous default: an unclear period is passed on
    to the tool, which refuses it, and a missing product name is asked for."""
    text = _norm(question)
    period = _period_argument(text, today)

    def call(tool: str, **extra: Any) -> PlannedCall:
        return PlannedCall(tool, {**(period if TOOLS[tool].args.model_fields.get("period") else {}), **extra})

    if _any(
        text,
        "what should i purchase",
        "what to purchase",
        "what should i buy",
        "what do i need to buy",
        "purchase suggestion",
        "purchase list",
        "shopping list",
        "kya kharid",
        "kya order",
        "purchase karna",
        "क्या खरीद",
    ) or (_any(text, "suggest", "prepare", "make") and _any(text, "purchase", "order list")):
        return call("get_purchase_suggestions")
    if _any(text, "reorder", "re-order", "restock", "dobara order", "रीऑर्डर", "kitna order"):
        return call("get_reorder_recommendations")
    if _any(text, "unusual", "anomal", "abnormal", "suspicious", "asamany", "asaamanya", "gadbad", "असामान्य"):
        return call("get_anomalies")
    if _any(text, "promotion", "offer", "discount", "scheme", "coupon", "ऑफर", "छूट") and _any(
        text, "idea", "suggest", "recommend", "should i run", "sujhao", "सुझाव"
    ):
        return call("get_promotion_ideas")
    if _any(
        text,
        "competitive",
        "market price",
        "outside price",
        "external price",
        "compare my price",
        "price comparison",
        "bhav",
    ):
        name = _product_name(text)
        return (
            call("get_price_comparison", product=name)
            if name
            else Clarify(
                "Which product should I check? For example: 'Is the price of Sugar 1kg competitive?'"
            )
        )
    if _any(text, "summary", "report", "saar", "सार") and _any(
        text, "monthly", "month", "business", "mahine", "महीने", "मासिक"
    ):
        return call("get_business_report")
    if _any(
        text, "dashboard", "overview", "at a glance", "how is my business", "business kaisa", "kaisa chal"
    ):
        return call("get_business_dashboard")
    if _any(text, "online order", "online revenue", "online sale", "online store", "delivery", "ऑनलाइन"):
        return call("get_online_order_summary")
    if _any(text, "declin", "falling", "dropping", "decreas", "ghat", "kam ho rahi", "गिर") and _any(
        text, "sale", "product", "bik", "item"
    ):
        return call("get_declining_products")
    if _any(
        text,
        "high stock",
        "slow moving",
        "slow-moving",
        "dead stock",
        "not selling",
        "nahi bik",
        "kam bik",
        "excess stock",
        "overstock",
    ):
        return call("get_slow_moving_products")
    if _any(text, "categor", "श्रेणी") and _any(
        text, "perform", "poor", "weak", "best", "worst", "sale", "kaisa", "bad"
    ):
        return call("get_category_performance")
    if _any(
        text,
        "outstanding",
        "khata",
        "udhaar",
        "udhar",
        "baaki",
        "bakaya",
        "owe",
        "balance due",
        "बकाया",
        "उधार",
        "खाता",
    ):
        return call("get_customer_outstanding", **({"limit": n} if (n := _limit(text)) else {}))
    if _any(
        text,
        "low stock",
        "running low",
        "low in stock",
        "out of stock",
        "below reorder",
        "stock kam",
        "kam stock",
        "stock khatam",
        "khatam",
        "कम स्टॉक",
    ):
        return call("get_low_stock_products")
    if _any(text, "inventory", "stock value", "how much stock", "total stock", "stock kitna", "स्टॉक"):
        return call("get_inventory_status")
    if _any(text, "profit", "margin", "munafa", "munafaa", "labh", "मुनाफ"):
        return call("get_profit_summary")
    if _any(text, "promotion", "offer", "coupon", "discount", "chhoot", "chhut", "छूट", "ऑफर"):
        return call("get_promotion_summary")
    if _any(text, "purchase", "purchases", "purchased", "bought", "kharid", "kharida", "खरीद"):
        return call("get_purchase_summary")
    if _any(
        text,
        "top",
        "best",
        "most",
        "sabse zyada",
        "sabse jyada",
        "bestseller",
        "best-selling",
        "least",
        "worst",
        "slowest",
        "sabse kam",
    ) and _any(
        text,
        "product",
        "products",
        "item",
        "items",
        "sold",
        "selling",
        "sell",
        "bik",
        "saman",
        "सामान",
        "बिक",
    ):
        order = "bottom" if _any(text, "least", "worst", "slowest", "sabse kam", "bottom") else "top"
        limit = _limit(text)
        return call("get_product_sales", order=order, **({"limit": limit} if limit else {}))
    if _any(
        text,
        "sale",
        "sales",
        "sold",
        "sell",
        "revenue",
        "turnover",
        "bikri",
        "bika",
        "becha",
        "kamai",
        "बिक्री",
        "सेल",
    ):
        return call("get_sales_summary", compare=bool(_COMPARE.search(text)))
    if _any(text, "insight", "what should i know", "anything i should know", "important"):
        return call("get_insights")
    return None


def plan_with_provider(
    provider: AiProvider, question: str, today: date
) -> tuple[PlannedCall | None, ProviderReply]:
    """Ask the provider to pick a tool for a question the router did not know. Its reply is validated like any other
    untrusted input; a wrong tool, a stray argument or a malformed reply is "not understood", never an
    action."""
    reply = provider.plan(question, [tool.spec() for tool in TOOLS.values()], today)
    name = reply.data.get("tool")
    args = reply.data.get("args") or {}
    if not isinstance(name, str) or name not in TOOLS or not isinstance(args, dict):
        return None, reply
    try:
        TOOLS[name].args.model_validate(args)
    except ValidationError:
        return None, reply
    return PlannedCall(name, args), reply
