"""Document intelligence: a photo of a supplier invoice or a counted stock list becomes a DRAFT for a person to review.

    photo -> provider reads it into rows (untrusted) -> every field checked -> rows matched to products
          -> a draft for review -> the person confirms -> an existing service creates the record
          (`ai_action_service`)

Two rules govern everything here.

1. The document is DATA. Text printed on it (a product name, a note in a margin) is copied into a field and
shown to a
   person; it is never read as an instruction, never sent to the planner, and never decides an action. A
   line that looks
   like an instruction ("ignore previous instructions", a database command) is dropped and reported.
2. Nothing is created here. Reading, checking and matching only produce rows with a status; a purchase draft
or a stock
   adjustment exists only after a person confirms (`ai_action_service`), and stock is only ever changed by
   `inventory_service`, from a count the person approved.

Matching uses the same product matcher as everything else (barcode, SKU, normalised name, brand, pack size),
so no
second matching logic exists and no duplicate product is ever created: an unmatched line is a "New Product
Candidate"
that the person resolves by choosing a product or adding it themselves first.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.context import RequestContext
from app.models import Product, Supplier, Unit
from app.services import ai_format as fmt
from app.services import (
    ai_planner,
    ai_usage_service,
    entitlement_service,
    image_validation,
    inventory_service,
    product_match_service,
    purchase_service,
)
from app.services.ai_provider import AiProvider, configured_provider
from app.services.errors import AiServiceError, InvalidInputError
from app.services.product_lookup_service import name_key, pack_size_of
from app.services.shop_service import get_shop, shop_today

FEATURE = "ai_documents"
KINDS = ("invoice", "stock_list")
MAX_ROWS = 200
NOT_CONFIGURED_TEXT = "Document analysis is not configured yet."
MATCHED = "MATCHED"
POSSIBLE = "POSSIBLE_MATCH"
NEW = "NEW_PRODUCT_CANDIDATE"
_MAX_PRICE = Decimal("10000000")
_MAX_QTY = Decimal("1000000")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_EXTRA_INSTRUCTION = re.compile(
    r"\b(ignore|disregard|forget)\b.{0,40}\b(instruction|rule|prompt|above|previous)|\bsystem\s*:|\bassistant\s*:"
    r"|\b(delete|drop|truncate|wipe)\s+(all|the|every|inventory|stock|table|products?|data)\b|\byou\s+(must|should|are\s+now)\b"
    r"|\b(do not|don't)\s+tell\b|<\s*/?\s*(script|system|instructions?)\b",
    re.I,
)


@dataclass
class Extraction:
    status: str  # OK | NOT_CONFIGURED
    message: str | None
    kind: str
    header: dict[str, Any] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provider_label: str | None = None
    provider_error: AiServiceError | None = None


def _text(value: object, limit: int = 200) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", _CONTROL.sub(" ", str(value))).strip()
    return cleaned[:limit] or None


def looks_like_instruction(text: str | None) -> bool:
    """Words that look like an order to the assistant or a database command, inside document text."""
    return bool(text) and bool(
        ai_planner.INSTRUCTION_PATTERN.search(text or "")
        or ai_planner.SQL_PATTERN.search(text or "")
        or _EXTRA_INSTRUCTION.search(text or "")
    )


def _number(value: object) -> Decimal | None:
    """A printed number ("1,250.50", "₹ 45"); None for anything else. Never raises."""
    if value is None:
        return None
    cleaned = re.sub(r"[₹,\s]|rs\.?|inr", "", str(value).casefold())
    if not cleaned or not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _date(value: object) -> date | None:
    text = _text(value, 20)
    if not text or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def sanitize_extraction(
    data: dict[str, Any], kind: str
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Turn a provider's raw reply into clean header fields and row dicts (all text). Numbers stay as printed text so
    the person sees what was read. Hostile or malformed content is dropped and reported, never used."""
    warnings: list[str] = []
    header: dict[str, Any] = {}
    for key, limit in (("supplier", 120), ("invoice_no", 50), ("invoice_date", 20), ("total", 30)):
        value = _text(data.get(key), limit)
        if value and looks_like_instruction(value):
            warnings.append(f"The {key.replace('_', ' ')} looked like an instruction and was ignored.")
            value = None
        header[key] = value
    rows: list[dict[str, Any]] = []
    raw_rows = data.get("rows")
    if not isinstance(raw_rows, list):
        raw_rows = []
        warnings.append("No lines could be read from this document.")
    if len(raw_rows) > MAX_ROWS:
        warnings.append(f"Only the first {MAX_ROWS} lines were kept.")
    for raw in raw_rows[:MAX_ROWS]:
        if not isinstance(raw, dict):
            continue
        row = {
            k: _text(raw.get(k), 200 if k == "name" else 60)
            for k in (
                "name",
                "brand",
                "barcode",
                "sku",
                "quantity",
                "unit",
                "unit_price",
                "discount",
                "line_total",
            )
        }
        joined = " ".join(str(v) for v in row.values() if v)
        if looks_like_instruction(joined):
            warnings.append(
                "A line that looks like an instruction was ignored. Text on a document is only ever copied, never followed."
            )
            continue
        if not any(row.values()):
            continue
        rows.append(row)
    return header, rows, warnings


def extract(
    session: Session,
    ctx: RequestContext,
    *,
    kind: str,
    image_base64: str,
    content_type: str | None,
    provider: AiProvider | None = None,
    settings: Settings | None = None,
) -> Extraction:
    """Read a photographed document with the configured provider. Raises for a plan without document intelligence and
    for an unusable image; provider trouble is returned (see `AskOutcome`) so the failure record is saved
    first."""
    if kind not in KINDS:
        raise InvalidInputError("Choose a document type: invoice or stock list.", field="kind")
    entitlement_service.require_feature(session, ctx.shop_id, FEATURE)
    settings = settings or get_settings()
    data = image_validation.decode_base64(image_base64, max_bytes=settings.image_max_bytes)
    info = image_validation.inspect_image(
        data, declared_type=content_type, max_bytes=settings.image_max_bytes, max_side=settings.image_max_side
    )
    used = provider if provider is not None else configured_provider(settings)
    if used is None or not used.supports_documents:
        return Extraction("NOT_CONFIGURED", NOT_CONFIGURED_TEXT, kind)
    ai_usage_service.check_allowance(session, ctx.shop_id)
    feature = "invoice_photo" if kind == "invoice" else "stock_list_photo"
    try:
        reply = used.extract_document(data, info.content_type, kind)
    except AiServiceError as error:
        ai_usage_service.record(
            session, ctx, feature=feature, provider=used.name, model=None, status=ai_usage_service.FAILED
        )
        return Extraction("FAILED", None, kind, provider_label=used.label, provider_error=error)
    header, rows, warnings = sanitize_extraction(reply.data, kind)
    ai_usage_service.count_request(session, ctx.shop_id)
    ai_usage_service.record(
        session,
        ctx,
        feature=feature,
        provider=used.name,
        model=reply.model,
        status=ai_usage_service.OK,
        usage=reply.usage,
    )
    return Extraction("OK", None, kind, header, rows, warnings, used.label)


# --- Checking and matching (deterministic; works on rows from a provider or typed by a person)
# -----------------


@dataclass
class MatchResult:
    kind: str
    header: dict[str, Any]
    rows: list[dict[str, Any]]
    counts: dict[str, int]
    can_propose: bool
    problems: list[str]


def _candidates(session: Session, shop_id: int, row: dict[str, Any]) -> list[dict[str, Any]]:
    name = row.get("name")
    pack = pack_size_of(name)
    matches = product_match_service.find_possible_matches(
        session,
        shop_id,
        barcode=re.sub(r"\D", "", row.get("barcode") or "") or None,
        sku=row.get("sku"),
        name=name,
        brand=row.get("brand"),
        pack_text=f"{pack[0]} {pack[1]}" if pack else None,
        limit=5,
    )
    out = []
    for m in matches:
        p = m.view.product
        out.append(
            {
                "product_id": p.id,
                "name": p.name,
                "sku": p.sku,
                "brand": p.brand,
                "unit_code": m.view.unit.code,
                "strength": m.strength.label,
                "reasons": m.reasons,
            }
        )
    return out


def _status(candidates: list[dict[str, Any]]) -> tuple[str, int | None]:
    if not candidates:
        return NEW, None
    exact = [c for c in candidates if c["strength"] == "EXACT"]
    if len(exact) == 1:
        return MATCHED, exact[0]["product_id"]
    if len(exact) > 1:
        return POSSIBLE, None  # the barcode and the SKU point at different products: a person must choose
    if len(candidates) == 1 and candidates[0]["strength"] == "LIKELY":
        return POSSIBLE, candidates[0]["product_id"]  # offered as a suggestion; never chosen for the person
    return POSSIBLE, None


def _supplier(session: Session, shop_id: int, header: dict[str, Any], problems: list[str]) -> dict[str, Any]:
    text = header.get("supplier")
    out: dict[str, Any] = {
        "supplier_text": text,
        "supplier_id": None,
        "supplier_status": NEW,
        "supplier_candidates": [],
    }
    suppliers = list(
        session.scalars(select(Supplier).where(Supplier.shop_id == shop_id, Supplier.is_active.is_(True)))
    )
    if text:
        key = name_key(text)
        scored = sorted(
            (
                (1.0 if name_key(s.name) == key else SequenceMatcher(None, key, name_key(s.name)).ratio(), s)
                for s in suppliers
            ),
            key=lambda t: -t[0],
        )
        out["supplier_candidates"] = [
            {"supplier_id": s.id, "name": s.name} for score, s in scored if score >= 0.75
        ][:3]
        exact = [s for score, s in scored if score == 1.0]
        if len(exact) == 1:
            out["supplier_id"], out["supplier_status"] = exact[0].id, MATCHED
        elif out["supplier_candidates"]:
            out["supplier_status"] = POSSIBLE
    return out


def match_rows(
    session: Session, ctx: RequestContext, *, kind: str, header: dict[str, Any], rows: list[dict[str, Any]]
) -> MatchResult:
    """Check every row and match it to a product. Reads only. Rows may come from a provider or from the person's own
    edits; either way they are validated the same way and nothing is trusted."""
    if kind not in KINDS:
        raise InvalidInputError("Choose a document type: invoice or stock list.", field="kind")
    entitlement_service.require_feature(session, ctx.shop_id, FEATURE)
    if len(rows) > MAX_ROWS:
        raise InvalidInputError(f"At most {MAX_ROWS} lines can be checked at once.", field="rows")
    sid = ctx.shop_id
    today = shop_today(get_shop(session, sid))
    problems: list[str] = []
    out_header: dict[str, Any] = {
        k: _text(header.get(k), 120) for k in ("supplier", "invoice_no", "invoice_date", "total")
    }
    invoice_date = _date(out_header.get("invoice_date"))
    if kind == "invoice":
        out_header.update(_supplier(session, sid, out_header, problems))
        if out_header.get("invoice_date") and invoice_date is None:
            problems.append("The invoice date could not be read as a date (use YYYY-MM-DD).")
        elif invoice_date and invoice_date > today:
            problems.append("The invoice date is in the future.")
        out_header["invoice_date"] = invoice_date.isoformat() if invoice_date else None
        out_header["invoice_duplicate"] = bool(
            out_header["supplier_id"]
            and out_header.get("invoice_no")
            and purchase_service.invoice_number_taken(
                session, sid, out_header["supplier_id"], out_header["invoice_no"]
            )
        )
        if out_header["invoice_duplicate"]:
            problems.append("This supplier's invoice number has already been entered.")

    results: list[dict[str, Any]] = []
    picked: dict[int, int] = {}
    total_check = Decimal("0")
    for index, raw in enumerate(rows):
        row = {
            k: _text(raw.get(k), 200 if k == "name" else 60)
            for k in (
                "name",
                "brand",
                "barcode",
                "sku",
                "quantity",
                "unit",
                "unit_price",
                "discount",
                "line_total",
            )
        }
        row_problems: list[str] = []
        row_warnings: list[str] = []
        if looks_like_instruction(" ".join(str(v) for v in row.values() if v)):
            raise InvalidInputError(
                "A line looks like an instruction, not a product. Remove it.", field=f"rows.{index}.name"
            )
        candidates = _candidates(session, sid, row) if (row["name"] or row["barcode"] or row["sku"]) else []
        chosen = raw.get("product_id") if isinstance(raw.get("product_id"), int) else None
        status, suggested = _status(candidates)
        if chosen is not None:
            valid = {c["product_id"] for c in candidates}
            product = session.scalar(select(Product).where(Product.shop_id == sid, Product.id == chosen))
            if product is None:
                raise InvalidInputError(
                    "A chosen product does not exist in this shop.", field=f"rows.{index}.product_id"
                )
            status = MATCHED  # the person chose it
            suggested = chosen
            if chosen not in valid:
                row_warnings.append("You chose a product that does not resemble this line. Check it.")
        if not (row["name"] or row["barcode"] or row["sku"]):
            row_problems.append("This line has no product name, barcode or SKU.")
        qty = _number(row["quantity"])
        if qty is None or qty <= 0:
            row_problems.append("The quantity must be a number above zero.")
        elif qty > _MAX_QTY or qty != qty.quantize(Decimal("0.001")):
            row_problems.append("The quantity is too large or has more than 3 decimal places.")
        price = _number(row["unit_price"])
        discount = _number(row["discount"]) or Decimal("0")
        if kind == "invoice":
            if price is None or price < 0 or price > _MAX_PRICE:
                row_problems.append("The price must be a valid amount (zero or more).")
            elif qty is not None and qty > 0:
                gross = (qty * price).quantize(Decimal("0.01"))
                if discount < 0 or discount > gross:
                    row_problems.append("The discount cannot be negative or more than the line amount.")
                stated = _number(row["line_total"])
                computed = gross - discount
                total_check += computed
                if stated is not None and abs(stated - computed) > Decimal("0.5"):
                    row_warnings.append(
                        f"The printed line total ({fmt.money(stated)}) differs from quantity x price ({fmt.money(computed)})."
                    )
        # Only an exact match (or the person's own choice) fills in the product. A possible match is
        # a suggestion.
        product_id = suggested if status == MATCHED else None
        unit_code = None
        system_quantity = None
        if product_id is not None:
            pu = session.execute(
                select(Product, Unit)
                .join(Unit, Unit.id == Product.unit_id)
                .where(Product.shop_id == sid, Product.id == product_id)
            ).first()
            if pu is not None:
                product, unit = pu
                unit_code = unit.code
                if qty is not None and qty > 0:
                    try:
                        inventory_service.validate_quantity_for_unit(qty, unit, field="quantity")
                    except InvalidInputError as error:
                        row_problems.append(error.message)
                if not product.is_active:
                    row_problems.append(f"{product.name} is inactive.")
                if kind == "stock_list":
                    system_quantity = inventory_service.get_stock(session, sid, product_id)
                if product_id in picked:
                    row_problems.append(f"This product is also on line {picked[product_id] + 1}.")
                picked.setdefault(product_id, index)
        if status == NEW:
            row_warnings.append(
                "No matching product was found. Choose an existing product, or add the product first; nothing is created automatically."
            )
        results.append(
            {
                "index": index,
                **row,
                "status": status,
                "product_id": product_id,
                "unit_code": unit_code,
                "candidates": candidates,
                "suggested_product_id": suggested if status == POSSIBLE else None,
                "problems": row_problems,
                "warnings": row_warnings,
                "system_quantity": str(system_quantity) if system_quantity is not None else None,
                "difference": str(qty - system_quantity)
                if (kind == "stock_list" and qty is not None and system_quantity is not None)
                else None,
            }
        )
    if kind == "invoice":
        stated_total = _number(out_header.get("total"))
        if stated_total is not None and total_check and abs(stated_total - total_check) > Decimal("1"):
            problems.append(
                f"The printed total ({fmt.money(stated_total)}) differs from the sum of the lines ({fmt.money(total_check)}). Check the lines."
            )
    counts = {MATCHED: 0, POSSIBLE: 0, NEW: 0, "with_problems": 0}
    for r in results:
        counts[r["status"]] += 1
        counts["with_problems"] += 1 if r["problems"] else 0
    unresolved = [r for r in results if r["product_id"] is None]
    resolved_ok = (
        bool(results) and not unresolved and not any(r["problems"] for r in results) and not problems
    )
    if kind == "invoice" and out_header.get("supplier_id") is None:
        resolved_ok = False
    return MatchResult(kind, out_header, results, counts, resolved_ok, problems)
