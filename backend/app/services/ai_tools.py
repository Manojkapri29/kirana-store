"""The assistant's tools: a fixed list of read-only functions, each calling an existing report or service.

This is the only way the assistant reads business data. A tool has a name, a validated argument model
(`extra="forbid"`, so a made-up argument such as `shop_id` or `sql` is refused), and a function that takes the
authenticated request context. There is no argument that can name another shop, and no tool that writes.

Dates never come from a model: a phrase or date range is turned into real dates by `ai_dates`. Every number in
an
answer is read from the database by a report or service and formatted by `ai_format`; the wording is a
template.
Where the data is missing the answer says so ("Not Available", "I don't have enough data to determine this.")
instead of estimating.

Tools that need a plan feature of their own (advanced reports, price intelligence) keep that requirement: they
call
the same services, which check it. The assistant has no way around a plan.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.models import Product
from app.services import (
    ai_dates,
    analytics_service,
    entitlement_service,
    inventory_service,
    khata_service,
    price_comparison_service,
    sales_report_service,
)
from app.services import ai_format as fmt
from app.services import ai_insights_service as insights_service
from app.services.ai_answer import (
    ANSWERED,
    NO_DATA,
    NO_DATA_TEXT,
    NOT_AVAILABLE,
    RECOMMENDATION_BADGE,
    Answer,
    Figure,
    Proposal,
    Table,
)
from app.services.errors import EntitlementError, InvalidInputError
from app.services.shop_service import get_shop, shop_today

BASIC = "ai_assistant"
ADVANCED = "ai_insights"
ZERO = Decimal("0")


# --- Argument models ------------------------------------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class PeriodArgs(_Args):
    period: str | None = Field(
        default=None, max_length=80, description='A phrase such as "today" or "last month".'
    )
    date_from: date | None = None
    date_to: date | None = None


class SalesArgs(PeriodArgs):
    compare: bool = Field(default=False, description="Also compare with the previous period.")


class ProductSalesArgs(PeriodArgs):
    limit: int = Field(default=10, ge=1, le=50)
    order: Literal["top", "bottom"] = "top"


class LimitArgs(_Args):
    limit: int = Field(default=10, ge=1, le=100)


class PriceArgs(_Args):
    product: str = Field(min_length=1, max_length=100, description="The product's name, SKU or barcode.")


# --- Tool plumbing --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolContext:
    session: Session
    ctx: RequestContext
    today: date
    language: str = "en"

    def t(self, english: str, hindi: str | None = None) -> str:
        return hindi if (self.language == "hi" and hindi) else english

    def period(self, args: PeriodArgs, default: str) -> ai_dates.Period:
        if args.date_from is not None or args.date_to is not None:
            start = args.date_from or args.date_to
            end = args.date_to or args.date_from
            assert start is not None and end is not None
            return ai_dates.from_dates(start, end, self.today)
        return ai_dates.require(args.period, self.today, default=default)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[_Args]
    feature: str
    run: Callable[[ToolContext, Any], Answer]

    def spec(self) -> dict[str, Any]:
        """What a language model is told about the tool. Only names and argument shapes: never data."""
        return {
            "name": self.name,
            "description": self.description,
            "arguments": self.args.model_json_schema()["properties"],
        }


def _source_sales(period: ai_dates.Period) -> str:
    return f"Based on sales data from {ai_dates.describe(period.start, period.end)}"


def _period_info(period: ai_dates.Period) -> dict[str, str]:
    return {"from": period.start.isoformat(), "to": period.end.isoformat(), "label": period.label}


def _rupees(value: Decimal) -> str:
    return fmt.money(value)


# --- Sales ----------------------------------------------------------------------------------------------


def _sales_summary(tc: ToolContext, args: SalesArgs) -> Answer:
    period = tc.period(args, "this month")
    now = sales_report_service.sales_summary(tc.session, tc.ctx.shop_id, period.start, period.end)
    combined = now.combined
    count = combined.sales_count
    figures = [
        Figure(tc.t("Net sales", "कुल बिक्री"), _rupees(combined.net)),
        Figure(tc.t("Number of sales", "बिक्री की संख्या"), str(count)),
        Figure(tc.t("Gross sales", "सकल बिक्री"), _rupees(combined.gross)),
        Figure(tc.t("Discounts", "छूट"), _rupees(combined.discount)),
    ]
    if now.returns_count:
        figures += [
            Figure(tc.t("Returns refunded", "वापसी में लौटाया"), _rupees(now.returns_total)),
            Figure(tc.t("Net sales after returns", "वापसी के बाद बिक्री"), _rupees(now.net_after_returns)),
        ]
    notes = []
    export = {
        "kind": "sales-summary",
        "filters": {"date_from": period.start.isoformat(), "date_to": period.end.isoformat()},
    }
    sources = [_source_sales(period)]
    if count == 0 and not now.returns_count:
        return Answer(
            NO_DATA,
            "get_sales_summary",
            tc.t("Sales", "बिक्री"),
            tc.t(
                f"No sales were recorded for {period.label}.", f"{period.label} के लिए कोई बिक्री दर्ज नहीं हुई।"
            ),
            sources=sources,
            period=_period_info(period),
            export=export,
        )
    message = tc.t(
        f"Net sales for {period.label} were {_rupees(combined.net)} from {count} sale{'s' if count != 1 else ''}.",
        f"{period.label} में कुल बिक्री {_rupees(combined.net)} रही ({count} बिक्री)।",
    )
    if args.compare:
        before = ai_dates.previous(period, tc.today)
        then = sales_report_service.sales_summary(tc.session, tc.ctx.shop_id, before.start, before.end)
        change = fmt.percent_change(combined.net, then.combined.net)
        figures.append(
            Figure(tc.t(f"Net sales, {before.label}", "पिछली अवधि की बिक्री"), _rupees(then.combined.net))
        )
        if change is None:
            notes.append(
                tc.t(
                    "There were no sales in the comparison period, so no percentage can be given.",
                    "तुलना की अवधि में बिक्री नहीं थी, इसलिए प्रतिशत नहीं बताया जा सकता।",
                )
            )
        else:
            direction = tc.t("up" if change >= 0 else "down", "अधिक" if change >= 0 else "कम")
            figures.append(
                Figure(tc.t("Change", "बदलाव"), f"{'+' if change > 0 else ''}{change}%", direction)
            )
            message += " " + tc.t(
                f"That is {abs(change)}% {direction} compared with {before.label} ({_rupees(then.combined.net)}).",
                f"यह {before.label} ({_rupees(then.combined.net)}) से {abs(change)}% {direction} है।",
            )
        sources.append(_source_sales(before))
    table = None
    if period.days > 1 and len(now.days) <= 62:
        table = Table(
            [tc.t("Date", "तारीख"), tc.t("Net sales", "कुल बिक्री"), tc.t("Sales", "बिक्री")],
            [
                [d.day.strftime("%d %b"), _rupees(d.combined.net), str(d.combined.sales_count)]
                for d in now.days
            ],
        )
    return Answer(
        ANSWERED,
        "get_sales_summary",
        tc.t("Sales", "बिक्री"),
        message,
        figures,
        table,
        sources,
        _period_info(period),
        notes,
        export=export,
    )


def _product_sales(tc: ToolContext, args: ProductSalesArgs) -> Answer:
    period = tc.period(args, "this month")
    rows = analytics_service.product_sales(tc.session, tc.ctx.shop_id, period.start, period.end)
    sources = [_source_sales(period)]
    if not rows:
        return Answer(
            NO_DATA,
            "get_product_sales",
            tc.t("Products", "उत्पाद"),
            tc.t(
                f"No product sales were recorded for {period.label}.",
                f"{period.label} में किसी उत्पाद की बिक्री दर्ज नहीं हुई।",
            ),
            sources=sources,
            period=_period_info(period),
        )
    ordered = (
        sorted(rows, key=lambda r: (-r.quantity, r.name.casefold()))
        if args.order == "top"
        else sorted(rows, key=lambda r: (r.quantity, r.name.casefold()))
    )
    chosen = ordered[: args.limit]
    label = tc.t(
        "best-selling" if args.order == "top" else "slowest-selling",
        "सबसे ज़्यादा बिकने वाले" if args.order == "top" else "सबसे कम बिकने वाले",
    )
    return Answer(
        ANSWERED,
        "get_product_sales",
        tc.t("Products", "उत्पाद"),
        tc.t(
            f"Your {len(chosen)} {label} products by quantity for {period.label}:",
            f"{period.label} के {len(chosen)} {label} उत्पाद (मात्रा के अनुसार):",
        ),
        table=Table(
            ["#", tc.t("Product", "उत्पाद"), tc.t("Quantity sold", "बिकी मात्रा"), tc.t("Revenue", "आय")],
            [
                [str(i), r.name, f"{fmt.quantity(r.quantity)} {r.unit_code}", _rupees(r.revenue)]
                for i, r in enumerate(chosen, 1)
            ],
        ),
        sources=sources,
        period=_period_info(period),
        notes=[
            tc.t(
                "Revenue is after line discounts and offers, before any discount on the whole bill, and less returns.",
                "आय लाइन छूट और ऑफ़र के बाद और वापसी घटाकर है।",
            )
        ],
        follow_ups=[tc.t("Which products have declining sales?", "किन उत्पादों की बिक्री घट रही है?")],
    )


def _profit(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    s = sales_report_service.sales_summary(tc.session, tc.ctx.shop_id, period.start, period.end)
    sources = [_source_sales(period)]
    if s.detailed.sales_count == 0:
        return Answer(
            NO_DATA,
            "get_profit_summary",
            tc.t("Profit", "मुनाफ़ा"),
            tc.t(
                f"There were no product-wise sales in {period.label}, so there is no profit to report.",
                f"{period.label} में कोई उत्पाद-वार बिक्री नहीं थी।",
            ),
            sources=sources,
            period=_period_info(period),
        )
    notes = [
        tc.t(
            "Quick Sales have no product or cost, so they are not part of profit.",
            "क्विक सेल में उत्पाद या लागत नहीं होती, इसलिए मुनाफ़े में शामिल नहीं।",
        )
    ]
    if s.detailed_gross_profit is None:
        return Answer(
            NOT_AVAILABLE,
            "get_profit_summary",
            tc.t("Profit", "मुनाफ़ा"),
            tc.t(
                "Profit Not Available because cost data is missing.", "लागत का डेटा न होने से मुनाफ़ा उपलब्ध नहीं है।"
            ),
            [
                Figure(
                    tc.t("Sales without a known cost", "बिना लागत वाली बिक्री"),
                    str(s.detailed_sales_without_cost),
                )
            ],
            sources=sources,
            period=_period_info(period),
            notes=notes,
            follow_ups=[tc.t("Which products have no cost?", "किन उत्पादों की लागत नहीं है?")],
        )
    if s.detailed_sales_without_cost:
        notes.append(
            tc.t(
                f"{s.detailed_sales_without_cost} sale(s) are left out because a cost was unknown.",
                f"{s.detailed_sales_without_cost} बिक्री छोड़ी गई क्योंकि लागत अज्ञात थी।",
            )
        )
    if s.returns_without_cost:
        notes.append(
            tc.t(
                f"{s.returns_without_cost} return(s) could not be costed and are not adjusted.",
                f"{s.returns_without_cost} वापसी की लागत अज्ञात है।",
            )
        )
    return Answer(
        ANSWERED,
        "get_profit_summary",
        tc.t("Profit", "मुनाफ़ा"),
        tc.t(
            f"Gross profit for {period.label}, where cost data is available, was {_rupees(s.detailed_gross_profit)}.",
            f"{period.label} का सकल मुनाफ़ा (जहाँ लागत उपलब्ध है) {_rupees(s.detailed_gross_profit)} रहा।",
        ),
        [
            Figure(tc.t("Gross profit", "सकल मुनाफ़ा"), _rupees(s.detailed_gross_profit)),
            Figure(tc.t("Product-wise net sales", "उत्पाद-वार बिक्री"), _rupees(s.detailed.net)),
        ],
        sources=sources,
        period=_period_info(period),
        notes=notes,
    )


# --- Inventory ------------------------------------------------------------------------------------------


def _inventory_status(tc: ToolContext, _args: NoArgs) -> Answer:
    rows, total = inventory_service.list_inventory(tc.session, tc.ctx.shop_id, active=True, limit=None)
    source = "Based on the current inventory ledger"
    if total == 0:
        return Answer(
            NO_DATA,
            "get_inventory_status",
            tc.t("Inventory", "स्टॉक"),
            tc.t("You have no active products yet.", "अभी कोई सक्रिय उत्पाद नहीं है।"),
            sources=[source],
        )
    status = inventory_service.StockStatus
    counts = {s: sum(1 for r in rows if r.status is s) for s in status}
    known = [r for r in rows if r.avg_cost is not None and r.current_stock > 0]
    value = sum((r.current_stock * r.avg_cost for r in known if r.avg_cost is not None), ZERO).quantize(
        Decimal("0.01")
    )
    unknown = sum(1 for r in rows if r.avg_cost is None and r.current_stock > 0)
    figures = [
        Figure(tc.t("Active products", "सक्रिय उत्पाद"), str(total)),
        Figure(tc.t("In stock", "स्टॉक में"), str(counts[status.IN_STOCK])),
        Figure(tc.t("Low stock", "कम स्टॉक"), str(counts[status.LOW_STOCK])),
        Figure(tc.t("Out of stock", "स्टॉक खत्म"), str(counts[status.OUT_OF_STOCK])),
        Figure(
            tc.t("Stock value at average cost", "औसत लागत पर स्टॉक मूल्य"),
            _rupees(value) if known else "Not Available",
        ),
    ]
    notes = (
        [
            tc.t(
                f"Cost is unknown for {unknown} product(s) with stock, so they are not in the value.",
                f"{unknown} उत्पादों की लागत अज्ञात है, वे मूल्य में शामिल नहीं।",
            )
        ]
        if unknown
        else []
    )
    return Answer(
        ANSWERED,
        "get_inventory_status",
        tc.t("Inventory", "स्टॉक"),
        tc.t(
            f"You have {total} active products: {counts[status.IN_STOCK]} in stock, {counts[status.LOW_STOCK]} low and {counts[status.OUT_OF_STOCK]} out of stock.",
            f"आपके {total} सक्रिय उत्पाद हैं: {counts[status.IN_STOCK]} स्टॉक में, {counts[status.LOW_STOCK]} कम और {counts[status.OUT_OF_STOCK]} खत्म।",
        ),
        figures,
        sources=[source],
        notes=notes,
    )


def _low_stock(tc: ToolContext, args: LimitArgs) -> Answer:
    status = inventory_service.StockStatus
    rows, _ = inventory_service.list_inventory(tc.session, tc.ctx.shop_id, active=True, limit=None)
    low = [r for r in rows if r.status is not status.IN_STOCK]
    low.sort(key=lambda r: (r.status is not status.OUT_OF_STOCK, r.current_stock, r.name.casefold()))
    source = "Based on the current inventory ledger"
    if not low:
        return Answer(
            NO_DATA,
            "get_low_stock_products",
            tc.t("Low stock", "कम स्टॉक"),
            tc.t(
                "No products are at or below their reorder level.",
                "कोई उत्पाद रीऑर्डर स्तर पर या उससे नीचे नहीं है।",
            ),
            sources=[source],
        )
    shown = low[: args.limit]
    return Answer(
        ANSWERED,
        "get_low_stock_products",
        tc.t("Low stock", "कम स्टॉक"),
        tc.t(
            f"{len(low)} product{'s are' if len(low) != 1 else ' is'} at or below the reorder level.",
            f"{len(low)} उत्पाद रीऑर्डर स्तर पर या उससे नीचे हैं।",
        ),
        table=Table(
            [
                tc.t("Product", "उत्पाद"),
                tc.t("In stock", "स्टॉक"),
                tc.t("Reorder level", "रीऑर्डर स्तर"),
                tc.t("Status", "स्थिति"),
            ],
            [
                [
                    r.name,
                    f"{fmt.quantity(r.current_stock)} {r.unit_code}",
                    f"{fmt.quantity(r.reorder_level)} {r.unit_code}",
                    tc.t(
                        "Out of stock" if r.status is status.OUT_OF_STOCK else "Low stock",
                        "खत्म" if r.status is status.OUT_OF_STOCK else "कम",
                    ),
                ]
                for r in shown
            ],
        ),
        sources=[source],
        notes=[tc.t(f"Showing {len(shown)} of {len(low)}.", f"{len(low)} में से {len(shown)} दिखाए गए।")]
        if len(low) > len(shown)
        else [],
        follow_ups=[tc.t("What should I purchase this week?", "इस हफ्ते मुझे क्या खरीदना चाहिए?")],
    )


# --- Khata, purchases, promotions, online ---------------------------------------------------------------


def _outstanding(tc: ToolContext, args: LimitArgs) -> Answer:
    accounts, _ = khata_service.list_accounts(
        tc.session,
        tc.ctx.shop_id,
        balance=khata_service.BalanceStatus.OUTSTANDING,
        biggest_first=True,
        limit=None,
    )
    source = "Based on the customer Khata ledger"
    if not accounts:
        return Answer(
            NO_DATA,
            "get_customer_outstanding",
            tc.t("Khata", "खाता"),
            tc.t("No customer has an outstanding balance.", "किसी ग्राहक का बकाया नहीं है।"),
            sources=[source],
        )
    total = sum((a.outstanding for a in accounts), ZERO)
    shown = accounts[: args.limit]
    return Answer(
        ANSWERED,
        "get_customer_outstanding",
        tc.t("Khata", "खाता"),
        tc.t(
            f"{len(accounts)} customer{'s owe' if len(accounts) != 1 else ' owes'} you {_rupees(total)} in total.",
            f"{len(accounts)} ग्राहकों का कुल बकाया {_rupees(total)} है।",
        ),
        [
            Figure(tc.t("Total outstanding", "कुल बकाया"), _rupees(total)),
            Figure(tc.t("Customers owing", "बकाया वाले ग्राहक"), str(len(accounts))),
        ],
        Table(
            [tc.t("Customer", "ग्राहक"), tc.t("Outstanding", "बकाया")],
            [[a.customer.name, _rupees(a.outstanding)] for a in shown],
        ),
        [source],
        notes=[tc.t(f"Showing the {len(shown)} largest.", f"सबसे बड़े {len(shown)} दिखाए गए।")]
        if len(accounts) > len(shown)
        else [],
    )


def _purchase_summary(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    p = analytics_service.purchase_totals(tc.session, tc.ctx.shop_id, period.start, period.end)
    source = f"Based on purchase data from {ai_dates.describe(period.start, period.end)}"
    if p.count == 0 and p.returns_count == 0:
        return Answer(
            NO_DATA,
            "get_purchase_summary",
            tc.t("Purchases", "खरीद"),
            tc.t(f"No purchases were posted for {period.label}.", f"{period.label} में कोई खरीद दर्ज नहीं हुई।"),
            sources=[source],
            period=_period_info(period),
        )
    figures = [
        Figure(tc.t("Purchased", "खरीद"), _rupees(p.total)),
        Figure(tc.t("Purchases", "खरीद की संख्या"), str(p.count)),
    ]
    if p.returns_count:
        figures += [
            Figure(tc.t("Sent back to suppliers", "सप्लायर को लौटाया"), _rupees(p.returns_total)),
            Figure(tc.t("Net purchases", "शुद्ध खरीद"), _rupees(p.net)),
        ]
    return Answer(
        ANSWERED,
        "get_purchase_summary",
        tc.t("Purchases", "खरीद"),
        tc.t(
            f"You purchased {_rupees(p.net)} of goods in {period.label} across {p.count} purchase{'s' if p.count != 1 else ''}.",
            f"{period.label} में आपने {_rupees(p.net)} का माल खरीदा ({p.count} खरीद)।",
        ),
        figures,
        Table(
            [tc.t("Supplier", "सप्लायर"), tc.t("Purchases", "खरीद"), tc.t("Total", "कुल")],
            [[s.name, str(s.purchases), _rupees(s.total)] for s in p.by_supplier[:10]],
        ),
        [source],
        _period_info(period),
    )


def _promotion_summary(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    r = sales_report_service.discount_report(
        tc.session, tc.ctx.shop_id, period.start, period.end
    )  # checks the plan
    source = _source_sales(period)
    if r.total_discount == 0 and not r.by_promotion:
        return Answer(
            NO_DATA,
            "get_promotion_summary",
            tc.t("Discounts and offers", "छूट और ऑफ़र"),
            tc.t(
                f"No discounts or offers were used in {period.label}.",
                f"{period.label} में कोई छूट या ऑफ़र इस्तेमाल नहीं हुआ।",
            ),
            sources=[source],
            period=_period_info(period),
        )
    ranked = sorted(r.by_promotion, key=lambda p: (-p.uses, -p.discount, p.name.casefold()))
    best = (
        " "
        + tc.t(
            f"The offer used most was '{ranked[0].name}' ({ranked[0].uses} times, {_rupees(ranked[0].discount)}).",
            f"सबसे ज़्यादा '{ranked[0].name}' चला ({ranked[0].uses} बार)।",
        )
        if ranked
        else ""
    )
    return Answer(
        ANSWERED,
        "get_promotion_summary",
        tc.t("Discounts and offers", "छूट और ऑफ़र"),
        tc.t(
            f"Total discounts in {period.label} were {_rupees(r.total_discount)}.",
            f"{period.label} में कुल छूट {_rupees(r.total_discount)} रही।",
        )
        + best,
        [
            Figure(tc.t("Total discounts", "कुल छूट"), _rupees(r.total_discount)),
            Figure(tc.t("From offers and coupons", "ऑफ़र और कूपन से"), _rupees(r.promotion_discount)),
            Figure(
                tc.t("Line and bill discounts", "लाइन और बिल छूट"), _rupees(r.line_discount + r.bill_discount)
            ),
            Figure(tc.t("Offer applications", "ऑफ़र लगे"), str(r.promotion_applications)),
        ],
        Table(
            [tc.t("Offer", "ऑफ़र"), tc.t("Times used", "बार"), tc.t("Discount given", "दी गई छूट")],
            [[p.name, str(p.uses), _rupees(p.discount)] for p in ranked[:10]],
        )
        if ranked
        else None,
        [source],
        _period_info(period),
        export={
            "kind": "discount-report",
            "filters": {"date_from": period.start.isoformat(), "date_to": period.end.isoformat()},
        },
    )


def _online_orders(tc: ToolContext, _args: PeriodArgs) -> Answer:
    return Answer(
        NOT_AVAILABLE,
        "get_online_order_summary",
        tc.t("Online orders", "ऑनलाइन ऑर्डर"),
        tc.t(
            "Online ordering is not part of this application yet, so there are no online orders or online revenue to report. Nothing has been estimated.",
            "ऑनलाइन ऑर्डर अभी इस ऐप में नहीं है, इसलिए बताने के लिए कोई ऑनलाइन ऑर्डर या आय नहीं है।",
        ),
        sources=[tc.t("Online ordering is planned for a later phase", "ऑनलाइन ऑर्डर बाद के चरण में आएगा")],
    )


def _dashboard(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    sid = tc.ctx.shop_id
    s = sales_report_service.sales_summary(tc.session, sid, period.start, period.end)
    before = ai_dates.previous(period, tc.today)
    then = sales_report_service.sales_summary(tc.session, sid, before.start, before.end)
    rows, _ = inventory_service.list_inventory(tc.session, sid, active=True, limit=None)
    low = sum(1 for r in rows if r.status is not inventory_service.StockStatus.IN_STOCK)
    accounts, _n = khata_service.list_accounts(
        tc.session, sid, balance=khata_service.BalanceStatus.OUTSTANDING, limit=None
    )
    owed = sum((a.outstanding for a in accounts), ZERO)
    p = analytics_service.purchase_totals(tc.session, sid, period.start, period.end)
    change = fmt.percent_change(s.combined.net, then.combined.net)
    figures = [
        Figure(
            tc.t("Net sales", "कुल बिक्री"),
            _rupees(s.combined.net),
            f"{'+' if change and change > 0 else ''}{change}% vs {before.label}"
            if change is not None
            else None,
        ),
        Figure(
            tc.t("Gross profit (where cost is known)", "सकल मुनाफ़ा (जहाँ लागत ज्ञात)"),
            _rupees(s.detailed_gross_profit) if s.detailed_gross_profit is not None else "Not Available",
        ),
        Figure(tc.t("Net purchases", "शुद्ध खरीद"), _rupees(p.net)),
        Figure(tc.t("Products low or out of stock", "कम या खत्म स्टॉक"), str(low)),
        Figure(tc.t("Customer outstanding", "ग्राहक बकाया"), _rupees(owed)),
    ]
    notes = []
    if s.detailed_gross_profit is None and s.detailed.sales_count:
        notes.append(
            tc.t(
                "Profit Not Available because cost data is missing.", "लागत का डेटा न होने से मुनाफ़ा उपलब्ध नहीं है।"
            )
        )
    return Answer(
        ANSWERED,
        "get_business_dashboard",
        tc.t("Business overview", "व्यापार का सार"),
        tc.t(
            f"Here is your business at a glance for {period.label}.",
            f"{period.label} के लिए आपके व्यापार का सार।",
        ),
        figures,
        sources=[
            _source_sales(period),
            "Based on the current inventory ledger",
            "Based on the customer Khata ledger",
        ],
        period=_period_info(period),
        notes=notes,
        export={
            "kind": "sales-summary",
            "filters": {"date_from": period.start.isoformat(), "date_to": period.end.isoformat()},
        },
        follow_ups=[tc.t("Generate my monthly business summary.", "मेरा मासिक व्यापार सार बनाओ।")],
    )


# --- Advanced analytics ---------------------------------------------------------------------------------


def _category_performance(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    before = ai_dates.previous(period, tc.today)
    sid = tc.ctx.shop_id
    now = analytics_service.category_sales(
        analytics_service.product_sales(tc.session, sid, period.start, period.end)
    )
    then = {
        c.category_id: c
        for c in analytics_service.category_sales(
            analytics_service.product_sales(tc.session, sid, before.start, before.end)
        )
    }
    sources = [_source_sales(period), _source_sales(before)]
    if not now and not then:
        return Answer(
            NO_DATA,
            "get_category_performance",
            tc.t("Categories", "श्रेणियाँ"),
            NO_DATA_TEXT,
            sources=sources,
            period=_period_info(period),
        )
    rows, worst = [], None
    seen = set()
    for c in now:
        old = then.get(c.category_id)
        seen.add(c.category_id)
        change = fmt.percent_change(c.revenue, old.revenue) if old else None
        rows.append(
            [
                c.name,
                _rupees(c.revenue),
                _rupees(old.revenue) if old else "—",
                f"{change}%" if change is not None else "—",
            ]
        )
        if change is not None and change <= Decimal("-20") and (worst is None or change < worst[1]):
            worst = (c.name, change)
    for cid, old in then.items():
        if cid not in seen:
            rows.append([old.name, _rupees(ZERO), _rupees(old.revenue), "-100.0%"])
            if worst is None or Decimal("-100") < worst[1]:
                worst = (old.name, Decimal("-100"))
    message = (
        tc.t(
            f"'{worst[0]}' is performing poorly: its revenue is down {abs(worst[1])}% compared with {before.label}.",
            f"'{worst[0]}' कमज़ोर चल रही है: आय {abs(worst[1])}% कम है।",
        )
        if worst
        else tc.t(
            f"No category shows a clear decline (20% or more) compared with {before.label}.",
            "किसी श्रेणी में साफ़ गिरावट (20% या ज़्यादा) नहीं दिखती।",
        )
    )
    return Answer(
        ANSWERED,
        "get_category_performance",
        tc.t("Categories", "श्रेणियाँ"),
        message,
        table=Table(
            [
                tc.t("Category", "श्रेणी"),
                tc.t("Revenue", "आय"),
                tc.t("Previous", "पिछली"),
                tc.t("Change", "बदलाव"),
            ],
            rows,
        ),
        sources=sources,
        period=_period_info(period),
    )


def _declining(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    before = ai_dates.previous(period, tc.today)
    found = insights_service.declining_products(
        tc.session, tc.ctx.shop_id, (period.start, period.end), (before.start, before.end)
    )
    sources = [_source_sales(period), _source_sales(before)]
    if not found:
        return Answer(
            NO_DATA,
            "get_declining_products",
            tc.t("Declining products", "घटती बिक्री"),
            tc.t(
                f"No product with a clear drop in sales (30% or more) was found compared with {before.label}.",
                "किसी उत्पाद की बिक्री में साफ़ गिरावट (30% या ज़्यादा) नहीं मिली।",
            ),
            sources=sources,
            period=_period_info(period),
        )
    return Answer(
        ANSWERED,
        "get_declining_products",
        tc.t("Declining products", "घटती बिक्री"),
        tc.t(
            f"{len(found)} product{'s sold' if len(found) != 1 else ' sold'} at least 30% less than in {before.label}.",
            f"{len(found)} उत्पाद पिछली अवधि से 30% या ज़्यादा कम बिके।",
        ),
        table=Table(
            [tc.t("Product", "उत्पाद"), tc.t("Before", "पहले"), tc.t("Now", "अब"), tc.t("Change", "बदलाव")],
            [
                [
                    d.name,
                    f"{fmt.quantity(d.previous_quantity)} {d.unit_code}",
                    f"{fmt.quantity(d.current_quantity)} {d.unit_code}",
                    f"{d.change_percent}%",
                ]
                for d in found[:20]
            ],
        ),
        sources=sources,
        period=_period_info(period),
        notes=[
            tc.t(
                "Only products that sold at least 3 units in the earlier period are compared.",
                "केवल वे उत्पाद तुलना में हैं जो पहले कम से कम 3 इकाई बिके।",
            )
        ],
    )


def _slow_moving(tc: ToolContext, _args: NoArgs) -> Answer:
    found = insights_service.slow_moving(tc.session, tc.ctx.shop_id, tc.today)
    source = f"Based on the inventory ledger and sales from the last {insights_service.SLOW_DAYS} days"
    if not found:
        return Answer(
            NO_DATA,
            "get_slow_moving_products",
            tc.t("Slow-moving stock", "धीमा बिकने वाला स्टॉक"),
            tc.t(
                "No product has high stock together with low recent sales.",
                "किसी उत्पाद का स्टॉक ज़्यादा और बिक्री कम नहीं है।",
            ),
            sources=[source],
        )
    return Answer(
        ANSWERED,
        "get_slow_moving_products",
        tc.t("Slow-moving stock", "धीमा बिकने वाला स्टॉक"),
        tc.t(
            f"{len(found)} product{'s have' if len(found) != 1 else ' has'} high stock but low recent sales.",
            f"{len(found)} उत्पादों का स्टॉक ज़्यादा है पर बिक्री कम।",
        ),
        table=Table(
            [
                tc.t("Product", "उत्पाद"),
                tc.t("In stock", "स्टॉक"),
                tc.t("Sold (60 days)", "60 दिन में बिका"),
                tc.t("Stock lasts", "स्टॉक चलेगा"),
                tc.t("Stock value", "मूल्य"),
            ],
            [
                [
                    m.name,
                    f"{fmt.quantity(m.stock)} {m.unit_code}",
                    f"{fmt.quantity(m.sold)} {m.unit_code}",
                    f"{m.days_of_cover:.0f} days"
                    if m.days_of_cover is not None
                    else tc.t("no sales", "बिक्री नहीं"),
                    fmt.money(m.stock_value),
                ]
                for m in found[:20]
            ],
        ),
        sources=[source],
        follow_ups=[tc.t("Suggest a promotion idea.", "कोई ऑफ़र का सुझाव दो।")],
    )


def _reorder(tc: ToolContext, _args: NoArgs) -> Answer:
    recs = insights_service.reorder_recommendations(tc.session, tc.ctx.shop_id, tc.today)
    sources = ["Based on the current inventory ledger and sales from the last 30 days"]
    if not recs:
        return Answer(
            NO_DATA,
            "get_reorder_recommendations",
            tc.t("Reorder", "रीऑर्डर"),
            tc.t("No product needs reordering right now.", "अभी किसी उत्पाद को रीऑर्डर की ज़रूरत नहीं।"),
            sources=sources,
        )
    table = Table(
        [
            tc.t("Product", "उत्पाद"),
            tc.t("Current stock", "स्टॉक"),
            tc.t("Reorder level", "रीऑर्डर स्तर"),
            tc.t("Suggested reorder", "सुझाई मात्रा"),
            tc.t("Why", "कारण"),
        ],
        [
            [
                r.name,
                f"{fmt.quantity(r.current_stock)} {r.unit_code}",
                f"{fmt.quantity(r.reorder_level)} {r.unit_code}",
                f"{fmt.quantity(r.suggested_quantity)} {r.unit_code}",
                "; ".join(r.reasons) + (" (limited sales history)" if r.low_history else ""),
            ]
            for r in recs[:30]
        ],
    )
    return Answer(
        ANSWERED,
        "get_reorder_recommendations",
        tc.t("Reorder", "रीऑर्डर"),
        tc.t(
            f"{len(recs)} product{'s need' if len(recs) != 1 else ' needs'} reordering.",
            f"{len(recs)} उत्पादों को रीऑर्डर की ज़रूरत है।",
        ),
        table=table,
        sources=sources,
        badges=[RECOMMENDATION_BADGE],
        notes=[
            tc.t(
                "Suggestions cover about 3 weeks of recent sales plus your reorder level. Nothing has been ordered.",
                "सुझाव लगभग 3 हफ्ते की हाल की बिक्री और रीऑर्डर स्तर पर आधारित हैं। कुछ ऑर्डर नहीं हुआ।",
            )
        ],
        follow_ups=[tc.t("What should I purchase this week?", "इस हफ्ते मुझे क्या खरीदना चाहिए?")],
    )


def _purchase_suggestions(tc: ToolContext, _args: NoArgs) -> Answer:
    groups = insights_service.purchase_suggestions(tc.session, tc.ctx.shop_id, tc.today)
    sources = ["Based on the current inventory ledger, sales from the last 30 days and purchase history"]
    if not groups:
        return Answer(
            NO_DATA,
            "get_purchase_suggestions",
            tc.t("Purchase suggestions", "खरीद के सुझाव"),
            tc.t("Nothing needs to be purchased right now.", "अभी कुछ खरीदने की ज़रूरत नहीं।"),
            sources=sources,
        )
    rows: list[list[str]] = []
    proposals: list[Proposal] = []
    for g in groups:
        for ln in g.lines:
            rows.append(
                [
                    g.supplier_name or tc.t("No preferred supplier", "कोई पसंदीदा सप्लायर नहीं"),
                    ln.name,
                    f"{fmt.quantity(ln.current_stock)} {ln.unit_code}",
                    f"{ln.per_day}/day",
                    f"{fmt.quantity(ln.suggested_quantity)} {ln.unit_code}",
                    fmt.money(ln.unit_cost_used) + (f" ({ln.cost_basis})" if ln.cost_basis else ""),
                    fmt.money(ln.avg_cost),
                ]
            )
        if g.supplier_id is not None:
            proposals.append(
                Proposal(
                    "PURCHASE_DRAFT",
                    "purchase_suggestions",
                    f"Create purchase draft for {g.supplier_name}",
                    {
                        "supplier_id": g.supplier_id,
                        "items": [
                            {
                                "product_id": ln.product_id,
                                "quantity": str(ln.suggested_quantity),
                                "unit_cost": str(ln.unit_cost_used)
                                if ln.unit_cost_used is not None
                                else None,
                            }
                            for ln in g.lines
                        ],
                    },
                )
            )
    without_supplier = sum(len(g.lines) for g in groups if g.supplier_id is None)
    notes = [
        tc.t(
            "These are suggestions. Nothing has been purchased; a draft is created only if you choose to, and posting is a separate step.",
            "ये सुझाव हैं। कुछ खरीदा नहीं गया; ड्राफ़्ट तभी बनेगा जब आप चाहें, और पोस्ट करना अलग कदम है।",
        )
    ]
    if without_supplier:
        notes.append(
            tc.t(
                f"{without_supplier} product(s) have no preferred supplier, so no draft is offered for them.",
                f"{without_supplier} उत्पादों का कोई पसंदीदा सप्लायर नहीं, इनके लिए ड्राफ़्ट नहीं दिया।",
            )
        )
    return Answer(
        ANSWERED,
        "get_purchase_suggestions",
        tc.t("Purchase suggestions", "खरीद के सुझाव"),
        tc.t(
            "Here is a suggested purchase list for this week, by supplier.",
            "इस हफ्ते की सुझाई खरीद सूची (सप्लायर के अनुसार)।",
        ),
        [
            Figure(
                tc.t("Estimated total (priced lines)", "अनुमानित कुल"),
                _rupees(sum((g.estimated_total for g in groups), ZERO)),
            )
        ],
        Table(
            [
                tc.t("Supplier", "सप्लायर"),
                tc.t("Product", "उत्पाद"),
                tc.t("Current stock", "स्टॉक"),
                tc.t("Sales pace", "बिक्री की गति"),
                tc.t("Suggested quantity", "सुझाई मात्रा"),
                tc.t("Latest price", "ताज़ा भाव"),
                tc.t("Average cost", "औसत लागत"),
            ],
            rows,
        ),
        sources,
        badges=[RECOMMENDATION_BADGE],
        notes=notes,
        proposals=proposals,
    )


def _price_comparison(tc: ToolContext, args: PriceArgs) -> Answer:
    sid = tc.ctx.shop_id
    entitlement_service.require_feature(tc.session, sid, "price_intelligence")
    rows, _ = inventory_service.list_inventory(tc.session, sid, q=args.product, active=None, limit=6)
    title = tc.t("Price comparison", "भाव की तुलना")
    if not rows:
        return Answer(
            NO_DATA,
            "get_price_comparison",
            title,
            tc.t(
                f"I could not find a product matching '{args.product}'.",
                f"'{args.product}' नाम का कोई उत्पाद नहीं मिला।",
            ),
        )
    exact = [
        r
        for r in rows
        if r.name.casefold() == args.product.casefold() or r.sku.casefold() == args.product.casefold()
    ]
    chosen = exact[:1] or (rows if len(rows) == 1 else [])
    if not chosen:
        return Answer(
            ANSWERED,
            "get_price_comparison",
            title,
            tc.t("Several products match. Which one do you mean?", "कई उत्पाद मिलते हैं। कौन सा?"),
            table=Table([tc.t("Product", "उत्पाद"), "SKU"], [[r.name, r.sku] for r in rows]),
            follow_ups=[f"Is the price of {r.name} competitive?" for r in rows[:3]],
        )
    product = tc.session.scalar(
        select(Product).where(Product.shop_id == sid, Product.id == chosen[0].product_id)
    )
    assert product is not None
    saved = price_comparison_service.saved_comparison(tc.session, sid, product.id)
    source = (
        "Based on prices this shop has already checked (saved price history); no new outside request was made"
    )
    figures = [Figure(tc.t("Your selling price", "आपका बिक्री भाव"), _rupees(product.selling_price))]
    if product.mrp is not None:
        figures.append(Figure("MRP", _rupees(product.mrp)))
    if not saved.has_barcode:
        return Answer(
            NO_DATA,
            "get_price_comparison",
            title,
            tc.t(
                f"{product.name} has no barcode, so outside prices cannot be matched to it.",
                f"{product.name} में बारकोड नहीं है, इसलिए बाहरी भाव मिलाए नहीं जा सकते।",
            ),
            figures,
            sources=[source],
        )
    if not saved.quotes:
        return Answer(
            NO_DATA,
            "get_price_comparison",
            title,
            tc.t(
                "No matching outside prices have been saved for this product. " + NO_DATA_TEXT, NO_DATA_TEXT
            ),
            figures,
            sources=[source],
            follow_ups=[],
        )
    rupee = [q for q in saved.quotes if q.quote.currency == "INR"]
    other = [q for q in saved.quotes if q.quote.currency != "INR"]
    table = Table(
        [
            tc.t("Price", "भाव"),
            tc.t("Source", "स्रोत"),
            tc.t("Checked", "जाँचा"),
            tc.t("Match", "मिलान"),
            tc.t("Location", "स्थान"),
        ],
        [
            [
                f"{q.quote.currency} {q.quote.price}",
                q.quote.provider,
                (
                    q.quote.observed_on or (q.quote.checked_at.date() if q.quote.checked_at else None)
                ).isoformat()
                if (q.quote.observed_on or q.quote.checked_at)
                else "—",
                f"{q.match.kind.value.title()} ({q.match.basis.replace('_', ' ')}), confidence {q.match.confidence}%"
                + (" — Possible match, confidence low" if q.match.kind.value == "POSSIBLE" else ""),
                q.quote.location_text or "—",
            ]
            for q in saved.quotes[:15]
        ],
    )
    notes = []
    message = ""
    if rupee:
        prices = sorted(q.quote.price for q in rupee)
        low, high = prices[0], prices[-1]
        message = tc.t(
            f"Available comparison data shows a range of {_rupees(low)} to {_rupees(high)}, against your selling price of {_rupees(product.selling_price)}.",
            f"उपलब्ध तुलना का दायरा {_rupees(low)} से {_rupees(high)} है; आपका बिक्री भाव {_rupees(product.selling_price)}।",
        )
        figures += [
            Figure(tc.t("Lowest observed", "सबसे कम"), _rupees(low)),
            Figure(tc.t("Highest observed", "सबसे ज़्यादा"), _rupees(high)),
        ]
        if any(q.match.kind.value == "POSSIBLE" for q in rupee):
            notes.append(
                tc.t(
                    "Some prices are only possible matches (confidence low). Weigh them accordingly.",
                    "कुछ भाव केवल संभावित मिलान हैं (भरोसा कम)।",
                )
            )
    else:
        message = tc.t(
            "Saved prices exist, but none are in rupees, so they cannot be compared with your price.",
            "सहेजे भाव रुपये में नहीं हैं, इसलिए तुलना नहीं हो सकती।",
        )
    if other:
        notes.append(
            tc.t(
                f"{len(other)} price(s) are in another currency and are shown as reported, not converted.",
                f"{len(other)} भाव दूसरी मुद्रा में हैं, बदले नहीं गए।",
            )
        )
    notes.append(
        tc.t(
            "This is information only. Your prices have not been changed and nothing suggests you must change them.",
            "यह केवल जानकारी है। आपके भाव नहीं बदले गए।",
        )
    )
    return Answer(ANSWERED, "get_price_comparison", title, message, figures, table, [source], notes=notes)


def _insights(tc: ToolContext, _args: NoArgs) -> Answer:
    found = insights_service.insights(tc.session, tc.ctx.shop_id, tc.today)
    if not found:
        return Answer(NO_DATA, "get_insights", tc.t("Insights", "अंतर्दृष्टि"), NO_DATA_TEXT)
    return Answer(
        ANSWERED,
        "get_insights",
        tc.t("Insights", "अंतर्दृष्टि"),
        tc.t("Here is what your data shows right now.", "आपका डेटा अभी यह दिखाता है।"),
        table=Table(
            [tc.t("Insight", "अंतर्दृष्टि"), tc.t("Based on", "आधार")],
            [[("⚠ " if i.attention else "") + i.text, i.source] for i in found],
        ),
        sources=sorted({i.source for i in found if i.source}),
        badges=[RECOMMENDATION_BADGE],
    )


def _anomalies(tc: ToolContext, _args: NoArgs) -> Answer:
    found = insights_service.anomalies(tc.session, tc.ctx.shop_id, tc.today)
    notes = [
        tc.t(
            "Online order monitoring is not available because online ordering is not part of this application yet.",
            "ऑनलाइन ऑर्डर की निगरानी उपलब्ध नहीं क्योंकि ऑनलाइन ऑर्डर अभी नहीं है।",
        )
    ]
    source = "Based on the last 5 weeks of sales, returns, discounts and stock adjustments"
    if not found:
        return Answer(
            NO_DATA,
            "get_anomalies",
            tc.t("Unusual activity", "असामान्य गतिविधि"),
            tc.t(
                "No unusual activity was found in your recent data.", "आपके हाल के डेटा में कुछ असामान्य नहीं मिला।"
            ),
            sources=[source],
            notes=notes,
        )
    return Answer(
        ANSWERED,
        "get_anomalies",
        tc.t("Unusual activity", "असामान्य गतिविधि"),
        tc.t(
            f"{len(found)} pattern{'s differ' if len(found) != 1 else ' differs'} from your recent normal. These are notices, not conclusions: each has ordinary explanations.",
            f"{len(found)} पैटर्न आपके सामान्य से अलग हैं। ये सूचनाएँ हैं, निष्कर्ष नहीं।",
        ),
        table=Table(
            [tc.t("What", "क्या"), tc.t("Detail", "विवरण"), tc.t("How to check", "कैसे जाँचें")],
            [[a.title, a.detail, a.check] for a in found],
        ),
        sources=[source],
        notes=notes,
    )


def _promotion_ideas(tc: ToolContext, _args: NoArgs) -> Answer:
    ideas = insights_service.promotion_ideas(tc.session, tc.ctx.shop_id, tc.today)
    source = "Based on the inventory ledger and sales from the last 60 days"
    if not ideas:
        return Answer(
            NO_DATA,
            "get_promotion_ideas",
            tc.t("Promotion ideas", "ऑफ़र के विचार"),
            tc.t("I don't have enough data to suggest a promotion right now.", NO_DATA_TEXT),
            sources=[source],
        )
    proposals = [
        Proposal("PROMOTION_DRAFT", "promotion_ideas", f"Create draft: {i.title}", dict(i.proposal))
        for i in ideas
    ]
    return Answer(
        ANSWERED,
        "get_promotion_ideas",
        tc.t("Promotion ideas", "ऑफ़र के विचार"),
        tc.t(
            "These are ideas, not active offers. You can create a draft to review; it stays inactive until you activate it.",
            "ये विचार हैं, चालू ऑफ़र नहीं। आप ड्राफ़्ट बना सकते हैं; आप चालू करेंगे तभी चलेगा।",
        ),
        table=Table(
            [tc.t("Idea", "विचार"), tc.t("Why", "क्यों"), tc.t("Caution", "सावधानी")],
            [[i.title, i.reason, i.caution or "—"] for i in ideas],
        ),
        sources=[source],
        badges=[RECOMMENDATION_BADGE],
        proposals=proposals,
        notes=[
            tc.t(
                "Seasonal demand is not analysed yet: it needs more than a year of history.",
                "मौसमी माँग का विश्लेषण अभी नहीं होता।",
            )
        ],
    )


def _business_report(tc: ToolContext, args: PeriodArgs) -> Answer:
    base = _dashboard(tc, args)
    found = insights_service.insights(tc.session, tc.ctx.shop_id, tc.today)
    base.tool = "get_business_report"
    base.title = tc.t("Business summary", "व्यापार सार")
    base.message = tc.t(
        f"Business summary for {base.period['label'] if base.period else ''}. Every figure comes from your records.",
        "व्यापार सार। हर आँकड़ा आपके रिकॉर्ड से है।",
    )
    base.badges = []
    if found:
        base.table = Table(
            [tc.t("Notable", "उल्लेखनीय"), tc.t("Based on", "आधार")], [[i.text, i.source] for i in found]
        )
    return base


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in [
        Tool(
            "get_sales_summary",
            "Sales for a period: net, gross, discounts, count, returns; can compare with the previous period.",
            SalesArgs,
            BASIC,
            _sales_summary,
        ),
        Tool(
            "get_product_sales",
            "Best or slowest selling products by quantity for a period.",
            ProductSalesArgs,
            BASIC,
            _product_sales,
        ),
        Tool(
            "get_inventory_status",
            "How many products are in stock, low or out, and the stock value at average cost.",
            NoArgs,
            BASIC,
            _inventory_status,
        ),
        Tool(
            "get_low_stock_products",
            "Products at or below their reorder level.",
            LimitArgs,
            BASIC,
            _low_stock,
        ),
        Tool(
            "get_customer_outstanding",
            "Total Khata outstanding and the customers who owe most.",
            LimitArgs,
            BASIC,
            _outstanding,
        ),
        Tool(
            "get_purchase_summary",
            "Purchases from suppliers for a period, less returns, by supplier.",
            PeriodArgs,
            BASIC,
            _purchase_summary,
        ),
        Tool(
            "get_profit_summary",
            "Gross profit for a period where cost data is known.",
            PeriodArgs,
            BASIC,
            _profit,
        ),
        Tool(
            "get_promotion_summary",
            "Discounts given and how offers and coupons performed in a period.",
            PeriodArgs,
            BASIC,
            _promotion_summary,
        ),
        Tool(
            "get_online_order_summary",
            "Online orders for a period (online ordering is not built yet).",
            PeriodArgs,
            BASIC,
            _online_orders,
        ),
        Tool(
            "get_business_dashboard",
            "A one-screen overview: sales, profit, purchases, stock and Khata.",
            PeriodArgs,
            BASIC,
            _dashboard,
        ),
        Tool(
            "get_category_performance",
            "Sales by category and which categories are declining.",
            PeriodArgs,
            ADVANCED,
            _category_performance,
        ),
        Tool(
            "get_declining_products",
            "Products whose sales fell compared with the previous period.",
            PeriodArgs,
            ADVANCED,
            _declining,
        ),
        Tool(
            "get_slow_moving_products",
            "Products with high stock but low recent sales.",
            NoArgs,
            ADVANCED,
            _slow_moving,
        ),
        Tool(
            "get_reorder_recommendations",
            "Which products to reorder, and how much.",
            NoArgs,
            ADVANCED,
            _reorder,
        ),
        Tool(
            "get_purchase_suggestions",
            "A suggested purchase list by supplier for this week.",
            NoArgs,
            ADVANCED,
            _purchase_suggestions,
        ),
        Tool(
            "get_price_comparison",
            "Saved outside prices for one product compared with the shop's selling price.",
            PriceArgs,
            ADVANCED,
            _price_comparison,
        ),
        Tool("get_insights", "Plain-language insights from the shop's data.", NoArgs, ADVANCED, _insights),
        Tool(
            "get_anomalies",
            "Unusual activity compared with the shop's recent normal.",
            NoArgs,
            ADVANCED,
            _anomalies,
        ),
        Tool(
            "get_promotion_ideas",
            "Ideas for offers from slow stock and the average bill.",
            NoArgs,
            ADVANCED,
            _promotion_ideas,
        ),
        Tool(
            "get_business_report",
            "A monthly-style business summary with notable changes.",
            PeriodArgs,
            ADVANCED,
            _business_report,
        ),
    ]
}


def run_tool(
    session: Session, ctx: RequestContext, name: str, raw_args: dict[str, Any], language: str = "en"
) -> Answer:
    """Validate the arguments, check the plan, run one tool. An unknown tool or a stray argument is refused."""
    tool = TOOLS.get(name)
    if tool is None:
        raise KeyError(name)
    try:
        args = tool.args.model_validate(
            raw_args
        )  # extra="forbid": no shop_id, no sql, nothing that is not declared
    except ValidationError as error:
        first = error.errors()[0]
        raise InvalidInputError(
            f"{'.'.join(str(p) for p in first['loc']) or 'arguments'}: {first['msg']}", field="args"
        ) from None
    entitlement_service.require_feature(session, ctx.shop_id, tool.feature)
    return tool.run(ToolContext(session, ctx, shop_today(get_shop(session, ctx.shop_id)), language), args)


__all__ = ["TOOLS", "Tool", "ToolContext", "run_tool", "EntitlementError"]
