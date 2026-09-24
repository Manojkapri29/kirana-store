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
    authorization_service,
    campaign_service,
    cashflow_service,
    crm_dashboard_service,
    crm_service,
    entitlement_service,
    expense_service,
    finance_dashboard_service,
    finance_ledger_service,
    inventory_intelligence_service,
    inventory_service,
    khata_service,
    loyalty_service,
    payables_service,
    pnl_service,
    price_comparison_service,
    receivables_service,
    reconciliation_service,
    referral_service,
    retention_service,
    sales_report_service,
    supplier_intelligence_service,
    tax_service,
)
from app.services import ai_format as fmt
from app.services import ai_insights_service as insights_service
from app.services import customer_intelligence_service as cis
from app.services.ai_answer import (
    ANSWERED,
    NO_DATA,
    NO_DATA_TEXT,
    NOT_AVAILABLE,
    NOT_CONFIGURED,
    RECOMMENDATION_BADGE,
    Answer,
    Figure,
    Proposal,
    Table,
)
from app.services.errors import EntitlementError, InvalidInputError, NotFoundError
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


class LedgerArgs(PeriodArgs):
    limit: int = Field(default=15, ge=1, le=50)
    event_type: (
        Literal[
            "SALE",
            "PURCHASE",
            "SALE_RETURN",
            "PURCHASE_RETURN",
            "CUSTOMER_PAYMENT",
            "SUPPLIER_PAYMENT",
            "EXPENSE",
            "OWNER_CAPITAL",
            "OWNER_WITHDRAWAL",
            "ADJUSTMENT",
            "OTHER_INCOME",
        ]
        | None
    ) = None


class CustomerArgs(_Args):
    customer_id: int = Field(description="The customer's id.")


class DaysArgs(_Args):
    days: int = Field(default=60, ge=1, le=730, description="How many days of inactivity counts.")


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


def _dead_stock(tc: ToolContext, _args: NoArgs) -> Answer:
    days = 90
    rows = inventory_intelligence_service.dead_stock(tc.session, tc.ctx.shop_id, tc.today, days)
    source = f"Based on the inventory ledger and sales from the last {days} days"
    if not rows:
        return Answer(
            NO_DATA, "get_dead_stock", tc.t("Dead stock", "डेड स्टॉक"),
            tc.t(f"No product has gone {days} days with stock and no sales at all.", f"कोई उत्पाद {days} दिनों से बिना बिके स्टॉक में नहीं है।"),
            sources=[source],
        )  # fmt: skip
    shown = rows[:15]
    return Answer(
        ANSWERED, "get_dead_stock", tc.t("Dead stock", "डेड स्टॉक"),
        tc.t(f"{len(rows)} product{'s have' if len(rows) != 1 else ' has'} had stock but no sales at all in the last {days} days.", f"{len(rows)} उत्पादों में स्टॉक है पर पिछले {days} दिनों में कोई बिक्री नहीं हुई।"),
        table=Table(
            [tc.t("Product", "उत्पाद"), tc.t("Stock", "स्टॉक"), tc.t("Stock value", "स्टॉक मूल्य")],
            [[r.name, f"{fmt.quantity(r.current_stock)} {r.unit_code}", fmt.money(r.stock_value) if r.stock_value is not None else NOT_AVAILABLE] for r in shown],
        ),
        sources=[source],
        notes=[tc.t(f"Showing {len(shown)} of {len(rows)}.", f"{len(rows)} में से {len(shown)} दिखाए गए।")] if len(rows) > len(shown) else [],
    )  # fmt: skip


def _supplier_analytics(tc: ToolContext, _args: NoArgs) -> Answer:
    rows = [
        a
        for a in supplier_intelligence_service.list_analytics(tc.session, tc.ctx.shop_id, limit=None)
        if a.purchase_count > 0
    ]
    source = "Based on posted purchases"
    if not rows:
        return Answer(
            NO_DATA,
            "get_supplier_analytics",
            tc.t("Suppliers", "सप्लायर"),
            tc.t("No posted purchases yet.", "अभी कोई पोस्ट की गई खरीद नहीं है।"),
            sources=[source],
        )
    shown = rows[:15]
    return Answer(
        ANSWERED, "get_supplier_analytics", tc.t("Suppliers", "सप्लायर"),
        tc.t(f"{len(rows)} supplier{'s have' if len(rows) != 1 else ' has'} at least one posted purchase.", f"{len(rows)} सप्लायर से कम से कम एक खरीद हुई है।"),
        table=Table(
            [tc.t("Supplier", "सप्लायर"), tc.t("Purchases", "खरीद"), tc.t("Total value", "कुल मूल्य"), tc.t("Products supplied", "आपूर्ति किए उत्पाद")],
            [[a.name, str(a.purchase_count), fmt.money(a.total_value), str(a.supplied_product_count)] for a in shown],
        ),
        sources=[source],
        notes=[tc.t("Suppliers are not ranked \"best\": review the figures yourself.", "सप्लायर की \"सर्वश्रेष्ठ\" रैंकिंग नहीं की जाती; आंकड़े खुद देखें।")],
    )  # fmt: skip


# --- CRM (Phase 14): every figure comes from the same service the CRM screens use, never recomputed here ---


def _customer_profile(tc: ToolContext, args: CustomerArgs) -> Answer:
    try:
        profile = crm_service.get_profile(tc.session, tc.ctx.shop_id, args.customer_id, tc.today)
    except NotFoundError:
        return Answer(
            NO_DATA,
            "get_customer_profile",
            tc.t("Customer profile", "ग्राहक प्रोफ़ाइल"),
            tc.t("Customer not found.", "ग्राहक नहीं मिला।"),
        )
    a = profile.analytics
    figures = [
        Figure(tc.t("Total purchases", "कुल खरीद"), fmt.money(a.total_purchases)),
        Figure(tc.t("Outstanding", "बकाया"), fmt.money(a.outstanding)),
        Figure(tc.t("Loyalty balance", "लॉयल्टी अंक"), f"{profile.loyalty_balance} pts"),
        Figure(tc.t("Segments", "सेगमेंट"), ", ".join(s.value for s in a.segments) or "—"),
    ]  # fmt: skip
    source = "Based on the customer's sales, khata and loyalty records"
    return Answer(
        ANSWERED,
        "get_customer_profile",
        profile.customer.name,
        tc.t("Customer profile.", "ग्राहक प्रोफ़ाइल।"),
        figures,
        sources=[source],
    )


def _customer_segments(tc: ToolContext, _args: NoArgs) -> Answer:
    rows = cis.list_analytics(tc.session, tc.ctx.shop_id, tc.today, limit=None)
    counts: dict[str, int] = {}
    for r in rows:
        for s in r.segments:
            counts[s.value] = counts.get(s.value, 0) + 1
    source = "Based on every customer's sales and khata history"
    if not counts:
        return Answer(
            NO_DATA,
            "get_customer_segments",
            tc.t("Customer segments", "ग्राहक सेगमेंट"),
            NO_DATA_TEXT,
            sources=[source],
        )
    table = Table(
        [tc.t("Segment", "सेगमेंट"), tc.t("Customers", "ग्राहक")],
        [[k, str(v)] for k, v in sorted(counts.items(), key=lambda kv: -kv[1])],
    )
    return Answer(
        ANSWERED,
        "get_customer_segments",
        tc.t("Customer segments", "ग्राहक सेगमेंट"),
        tc.t(
            f"{len(rows)} customer(s) in total, across {len(counts)} segment(s).",
            f"कुल {len(rows)} ग्राहक, {len(counts)} सेगमेंट में।",
        ),
        table=table,
        sources=[source],
    )


def _retention_summary(tc: ToolContext, _args: NoArgs) -> Answer:
    s = retention_service.retention_summary(tc.session, tc.ctx.shop_id, tc.today)
    source = "Based on every posted sale and quick sale"
    figures = [
        Figure(tc.t("Repeat purchase rate", "दोहराई खरीद दर"), f"{s.repeat_purchase_rate}%" if s.repeat_purchase_rate is not None else NOT_AVAILABLE),
        Figure(tc.t("Customers with purchases", "खरीदने वाले ग्राहक"), str(s.customers_with_purchases)),
        Figure(tc.t("Inactive customers", "निष्क्रिय ग्राहक"), str(s.inactive_customer_count)),
        Figure(tc.t("Reactivated recently", "हाल में सक्रिय हुए"), str(s.reactivated_count)),
        Figure(tc.t("Average days between purchases", "औसत अंतराल (दिन)"), str(s.average_purchase_interval_days) if s.average_purchase_interval_days is not None else NOT_AVAILABLE),
        Figure(tc.t("Cohort retention", "कोहोर्ट रिटेंशन"), f"{s.cohort_retention_rate}%" if isinstance(s.cohort_retention_rate, Decimal) else str(s.cohort_retention_rate)),
    ]  # fmt: skip
    return Answer(
        ANSWERED,
        "get_customer_retention_summary",
        tc.t("Retention summary", "रिटेंशन सारांश"),
        tc.t(f"Figures for the last {s.period_days} days.", f"पिछले {s.period_days} दिनों के आंकड़े।"),
        figures,
        sources=[source],
    )


def _inactive_customers(tc: ToolContext, args: DaysArgs) -> Answer:
    ids = retention_service.reactivation_candidates(
        tc.session, tc.ctx.shop_id, tc.today, inactive_days=args.days
    )
    source = f"Based on posted sales, comparing each customer's last purchase to {args.days} days ago"
    if not ids:
        return Answer(
            NO_DATA,
            "get_inactive_customers",
            tc.t("Inactive customers", "निष्क्रिय ग्राहक"),
            tc.t(
                f"No customer has been inactive for over {args.days} days.",
                f"{args.days} दिनों से कोई ग्राहक निष्क्रिय नहीं है।",
            ),
            sources=[source],
        )
    rows = [
        a
        for a in cis.list_analytics(tc.session, tc.ctx.shop_id, tc.today, limit=None)
        if a.customer_id in set(ids)
    ]
    shown = rows[:15]
    table = Table(
        [tc.t("Customer", "ग्राहक"), tc.t("Days since last purchase", "अंतिम खरीद से दिन")],
        [[r.name, str(r.days_since_last_purchase)] for r in shown],
    )
    return Answer(
        ANSWERED,
        "get_inactive_customers",
        tc.t("Inactive customers", "निष्क्रिय ग्राहक"),
        tc.t(
            f"{len(ids)} customer(s) have not purchased in over {args.days} days.",
            f"{len(ids)} ग्राहकों ने {args.days} दिनों से खरीदारी नहीं की।",
        ),
        table=table,
        sources=[source],
        notes=[tc.t(f"Showing {len(shown)} of {len(ids)}.", "")] if len(ids) > len(shown) else [],
    )


def _reactivation_candidates(tc: ToolContext, args: DaysArgs) -> Answer:
    return _inactive_customers(tc, args)  # the reactivation pool is exactly the inactive-customer list


def _loyalty_summary(tc: ToolContext, _args: NoArgs) -> Answer:
    program = loyalty_service.get_program(tc.session, tc.ctx.shop_id)
    summary = loyalty_service.points_summary(tc.session, tc.ctx.shop_id)
    source = "Based on the loyalty ledger"
    if program is None:
        return Answer(
            NOT_CONFIGURED,
            "get_loyalty_summary",
            tc.t("Loyalty", "लॉयल्टी"),
            tc.t(
                "No loyalty program has been configured for this shop.",
                "इस दुकान के लिए कोई लॉयल्टी प्रोग्राम सेट नहीं है।",
            ),
        )
    figures = [
        Figure(tc.t("Program active", "प्रोग्राम सक्रिय"), tc.t("Yes", "हाँ") if program.is_active else tc.t("No", "नहीं")),
        Figure(tc.t("Points issued", "जारी अंक"), str(summary["points_issued"])),
        Figure(tc.t("Points redeemed", "भुनाए गए अंक"), str(summary["points_redeemed"])),
        Figure(tc.t("Points outstanding", "शेष अंक"), str(summary["points_outstanding"])),
    ]  # fmt: skip
    return Answer(
        ANSWERED,
        "get_loyalty_summary",
        tc.t("Loyalty summary", "लॉयल्टी सारांश"),
        tc.t("Loyalty program summary.", "लॉयल्टी प्रोग्राम सारांश।"),
        figures,
        sources=[source],
    )


def _campaign_summary(tc: ToolContext, _args: NoArgs) -> Answer:
    rows, total = campaign_service.list_campaigns(tc.session, tc.ctx.shop_id, limit=1000)
    source = "Based on the shop's campaigns"
    if not rows:
        return Answer(
            NO_DATA,
            "get_campaign_summary",
            tc.t("Campaigns", "अभियान"),
            tc.t("No campaigns yet.", "अभी कोई अभियान नहीं है।"),
            sources=[source],
        )
    counts: dict[str, int] = {}
    for c in rows:
        counts[c.status.value] = counts.get(c.status.value, 0) + 1
    table = Table(
        [tc.t("Status", "स्थिति"), tc.t("Campaigns", "अभियान")], [[k, str(v)] for k, v in counts.items()]
    )
    return Answer(
        ANSWERED,
        "get_campaign_summary",
        tc.t("Campaign summary", "अभियान सारांश"),
        tc.t(f"{total} campaign(s) in total.", f"कुल {total} अभियान।"),
        table=table,
        sources=[source],
    )


def _referral_summary(tc: ToolContext, _args: NoArgs) -> Answer:
    events = referral_service.list_events(tc.session, tc.ctx.shop_id, limit=1000)
    source = "Based on the shop's referral events"
    if not events:
        return Answer(
            NO_DATA,
            "get_referral_summary",
            tc.t("Referrals", "रेफ़रल"),
            tc.t("No referrals recorded yet.", "अभी कोई रेफ़रल दर्ज नहीं है।"),
            sources=[source],
        )
    rewarded = sum(1 for e in events if e.status.value == "REWARDED")
    pending = sum(1 for e in events if e.status.value == "PENDING")
    figures = [
        Figure(tc.t("Total referrals", "कुल रेफ़रल"), str(len(events))),
        Figure(tc.t("Rewarded", "पुरस्कृत"), str(rewarded)),
        Figure(tc.t("Pending", "लंबित"), str(pending)),
    ]  # fmt: skip
    return Answer(
        ANSWERED,
        "get_referral_summary",
        tc.t("Referral summary", "रेफ़रल सारांश"),
        tc.t("Referral summary.", "रेफ़रल सारांश।"),
        figures,
        sources=[source],
    )


def _customer_growth_dashboard(tc: ToolContext, _args: NoArgs) -> Answer:
    d = crm_dashboard_service.build(tc.session, tc.ctx.shop_id, tc.today)
    source = "Based on the CRM dashboard's own figures (customer, revenue, retention, loyalty, campaign and referral services)"
    figures = [
        Figure(tc.t("Total customers", "कुल ग्राहक"), str(d.customers.total_customers)),
        Figure(tc.t("New customers", "नए ग्राहक"), str(d.customers.new_customers)),
        Figure(tc.t("Active customers", "सक्रिय ग्राहक"), str(d.customers.active_customers)),
        Figure(tc.t("Customer revenue", "ग्राहक राजस्व"), fmt.money(d.revenue.customer_revenue)),
        Figure(tc.t("Repeat purchase rate", "दोहराई खरीद दर"), f"{d.retention.repeat_purchase_rate}%" if d.retention.repeat_purchase_rate is not None else NOT_AVAILABLE),
        Figure(tc.t("Loyalty points outstanding", "शेष लॉयल्टी अंक"), str(d.loyalty.points_outstanding)),
        Figure(tc.t("Running campaigns", "चल रहे अभियान"), str(d.campaigns.running)),
        Figure(tc.t("Successful referrals", "सफल रेफ़रल"), str(d.referrals.successful_referrals)),
    ]  # fmt: skip
    return Answer(
        ANSWERED,
        "get_customer_growth_dashboard",
        tc.t("Customer growth dashboard", "ग्राहक वृद्धि डैशबोर्ड"),
        tc.t(f"Figures for the last {d.period_days} days.", f"पिछले {d.period_days} दिनों के आंकड़े।"),
        figures,
        sources=[source],
    )


# --- Finance (Phase 15): read-only. Every figure comes from the same service the finance screens use. --------

_FIN_NOTE = (
    "Figures are read from the shop's own posted records. Nothing here changes any record.",
    "आंकड़े दुकान के अपने पोस्ट किए रिकॉर्ड से लिए गए हैं। इससे कोई रिकॉर्ड नहीं बदलता।",
)


def _fin_answer(tc: ToolContext, tool: str, title: tuple[str, str], message: tuple[str, str], figures: list[Figure],
                sources: list[str], period: ai_dates.Period | None = None, notes: list[str] | None = None,
                table: Table | None = None, status: str = ANSWERED) -> Answer:  # fmt: skip
    return Answer(
        status,
        tool,
        tc.t(*title),
        tc.t(*message),
        figures,
        table=table,
        sources=sources,
        period=_period_info(period) if period else None,
        notes=[*(notes or []), tc.t(*_FIN_NOTE)],
    )


def _na(value: Decimal | None) -> str:
    return fmt.money(value)


def _revenue_summary(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    p = pnl_service.compute(tc.session, tc.ctx.shop_id, period.start, period.end)
    notes = [
        tc.t(
            "Calculation: Detailed Sales + Quick Sales - Sales Returns, by posted date. There are no online orders in this application, so none are added.",
            "गणना: विस्तृत बिक्री + क्विक सेल - बिक्री वापसी। इस ऐप में ऑनलाइन ऑर्डर नहीं हैं, इसलिए कुछ नहीं जोड़ा गया।",
        )
    ]
    if p.quick_sales_have_no_cost:
        notes.append(
            tc.t(
                "Limitation: Quick Sales count as revenue but have no product cost.",
                "सीमा: क्विक सेल राजस्व में गिनी जाती है पर उसकी उत्पाद लागत नहीं होती।",
            )
        )
    figures = [
        Figure(tc.t("Detailed sales", "विस्तृत बिक्री"), _rupees(p.detailed_sales)),
        Figure(tc.t("Quick sales", "क्विक सेल"), _rupees(p.quick_sales)),
        Figure(tc.t("Sales returns", "बिक्री वापसी"), _rupees(p.sales_returns)),
        Figure(tc.t("Net revenue", "शुद्ध राजस्व"), _rupees(p.revenue)),
    ]
    return _fin_answer(
        tc,
        "get_revenue_summary",
        ("Revenue", "राजस्व"),
        (f"Revenue for {period.label}.", f"{period.label} का राजस्व।"),
        figures,
        ["Based on posted sales, quick sales and sales returns"],
        period,
        notes,
    )


def _pnl_summary(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    p = pnl_service.compute(tc.session, tc.ctx.shop_id, period.start, period.end)
    figures = [
        Figure(tc.t("Revenue", "राजस्व"), _rupees(p.revenue)),
        Figure(tc.t("Cost of goods sold", "बेचे माल की लागत"), _na(p.cogs)),
        Figure(tc.t("Gross profit", "सकल मुनाफ़ा"), _na(p.gross_profit)),
        Figure(tc.t("Operating expenses (posted)", "संचालन खर्च (पोस्ट किए)"), _rupees(p.operating_expenses)),
        Figure(tc.t("Net profit", "शुद्ध मुनाफ़ा"), _na(p.net_profit)),
        Figure(
            tc.t("Gross margin", "सकल मार्जिन"),
            f"{p.gross_margin_pct}%" if p.gross_margin_pct is not None else fmt.money(None),
        ),
    ]
    notes = [
        tc.t(
            "Calculation: Gross profit = Revenue - cost of goods sold; Net profit = Gross profit - posted expenses; Gross margin = Gross profit / Revenue x 100.",
            "गणना: सकल मुनाफ़ा = राजस्व - माल की लागत; शुद्ध मुनाफ़ा = सकल मुनाफ़ा - पोस्ट किए खर्च।",
        )
    ]
    notes += p.notes
    if p.costed_sales.status == "PARTIAL":
        notes.append(
            tc.t(
                f"On the detailed sales whose cost is known ({p.costed_sales.coverage_pct}% of revenue) gross profit is {_rupees(p.costed_sales.gross_profit)}. This covers only that part of the business.",
                f"जिन बिक्री की लागत ज्ञात है ({p.costed_sales.coverage_pct}% राजस्व) उन पर सकल मुनाफ़ा {_rupees(p.costed_sales.gross_profit)} है। यह केवल उतना हिस्सा है।",
            )
        )
    status = ANSWERED if p.status == "ACTUAL" else NOT_AVAILABLE
    msg = (
        (f"Profit and loss for {period.label}.", f"{period.label} का लाभ-हानि।")
        if status == ANSWERED
        else (
            f"Profit Not Available: Insufficient Cost Data for {period.label}.",
            "मुनाफ़ा उपलब्ध नहीं: लागत का डेटा अपर्याप्त।",
        )
    )
    return _fin_answer(
        tc,
        "get_pnl_summary",
        ("Profit and loss", "लाभ-हानि"),
        msg,
        figures,
        ["Based on posted sales, returns, stored cost snapshots and posted expenses"],
        period,
        notes,
        status=status,
    )


def _expense_summary(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    total = expense_service.posted_expense_total(tc.session, tc.ctx.shop_id, period.start, period.end)
    rows = expense_service.expenses_by_category(tc.session, tc.ctx.shop_id, period.start, period.end)
    listed, _ = expense_service.list_expenses(
        tc.session, tc.ctx.shop_id, date_from=period.start, date_to=period.end, limit=500
    )
    waiting = sum(1 for e in listed if e.status.value in ("DRAFT", "SUBMITTED", "APPROVED"))
    if not rows and not waiting:
        return Answer(
            NO_DATA,
            "get_expense_summary",
            tc.t("Expenses", "खर्च"),
            tc.t(f"No expenses recorded for {period.label}.", f"{period.label} के लिए कोई खर्च दर्ज नहीं।"),
            period=_period_info(period),
        )
    table = (
        Table(
            [tc.t("Category", "श्रेणी"), tc.t("Posted amount", "पोस्ट राशि")],
            [[n, _rupees(a)] for _, n, a in rows[:10]],
        )
        if rows
        else None
    )
    notes = [
        tc.t(
            "Only POSTED expenses count (a voided one is netted out). Drafts, submitted, approved and rejected expenses are not included.",
            "केवल पोस्ट किए खर्च गिने जाते हैं (रद्द किया खर्च घटाया जाता है)।",
        )
    ]
    figures = [
        Figure(tc.t("Posted expenses", "पोस्ट किए खर्च"), _rupees(total)),
        Figure(
            tc.t("Waiting to be posted", "पोस्ट होने की प्रतीक्षा में"),
            str(waiting),
            tc.t("not counted", "गिने नहीं गए"),
        ),
    ]
    return _fin_answer(
        tc,
        "get_expense_summary",
        ("Expenses", "खर्च"),
        (f"Posted expenses for {period.label}.", f"{period.label} के पोस्ट किए खर्च।"),
        figures,
        ["Based on the finance ledger's expense entries"],
        period,
        notes,
        table,
    )


def _cash_flow_summary(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    r = cashflow_service.compute(tc.session, tc.ctx.shop_id, period.start, period.end)
    figures = [
        Figure(tc.t("Cash inflow", "नकदी आवक"), _rupees(r.inflow)),
        Figure(tc.t("Cash outflow", "नकदी जावक"), _rupees(r.outflow)),
        Figure(tc.t("Net cash flow", "शुद्ध नकदी प्रवाह"), _rupees(r.net)),
    ]
    rows = [
        [k, _rupees(v.inflow), _rupees(v.outflow)] for k, v in r.by_class.items() if v.inflow or v.outflow
    ]
    table = Table([tc.t("Class", "वर्ग"), tc.t("In", "आवक"), tc.t("Out", "जावक")], rows) if rows else None
    notes = [
        tc.t(
            "Calculation: money that actually moved (the settled part of each record), inflows minus outflows. A credit sale is not an inflow until it is paid.",
            "गणना: वास्तव में आया-गया पैसा; उधार बिक्री भुगतान होने तक आवक नहीं।",
        ),
        *r.notes,
    ]
    return _fin_answer(
        tc,
        "get_cash_flow_summary",
        ("Cash flow", "नकदी प्रवाह"),
        (f"Cash flow for {period.label}.", f"{period.label} का नकदी प्रवाह।"),
        figures,
        ["Based on the financial ledger view"],
        period,
        notes,
        table,
    )


def _receivables_summary(tc: ToolContext, _args: NoArgs) -> Answer:
    r = receivables_service.compute(tc.session, tc.ctx.shop_id, tc.today)
    if not r.customers:
        return Answer(
            NO_DATA,
            "get_receivables_summary",
            tc.t("Receivables", "प्राप्य"),
            tc.t("No customer owes anything right now.", "अभी किसी ग्राहक पर बकाया नहीं।"),
        )
    table = Table(
        [tc.t("Customer", "ग्राहक"), tc.t("Owes", "बकाया"), tc.t("Oldest charge (days)", "सबसे पुराना (दिन)")],
        [
            [c.name, _rupees(c.balance), str(c.oldest_open_days) if c.oldest_open_days is not None else "—"]
            for c in r.customers
            if c.balance > 0
        ][:10],
    )
    figures = [
        Figure(tc.t("Total receivables", "कुल प्राप्य"), _rupees(r.total_receivables)),
        Figure(tc.t("Customer advances", "ग्राहक अग्रिम"), _rupees(r.total_advances)),
    ]
    figures += [Figure(f"{b} {tc.t('days', 'दिन')}", _rupees(v)) for b, v in r.aging.items()]
    notes = [tc.t(r.methodology, "कोई देय तिथि दर्ज नहीं है; उम्र हर प्रविष्टि की अपनी तारीख से गिनी जाती है।")]
    return _fin_answer(
        tc,
        "get_receivables_summary",
        ("Receivables", "प्राप्य"),
        ("What customers owe, from Khata.", "ग्राहकों का बकाया, खाता से।"),
        figures,
        ["Based on the Khata ledger (the source of truth for customer balances)"],
        None,
        notes,
        table,
    )


def _payables_summary(tc: ToolContext, _args: NoArgs) -> Answer:
    r = payables_service.compute(tc.session, tc.ctx.shop_id, tc.today)
    if not r.suppliers:
        return Answer(
            NO_DATA,
            "get_payables_summary",
            tc.t("Payables", "देय"),
            tc.t("There are no supplier purchases yet.", "अभी कोई सप्लायर खरीद नहीं।"),
        )
    table = Table(
        [tc.t("Supplier", "सप्लायर"), tc.t("Payable", "देय")],
        [[x.name, _rupees(x.balance)] for x in r.suppliers if x.balance > 0][:10],
    )
    figures = [
        Figure(tc.t("Total payable", "कुल देय"), _rupees(r.total_payable)),
        Figure(tc.t("Supplier advances", "सप्लायर अग्रिम"), _rupees(r.total_advances)),
    ]
    figures += [Figure(f"{b} {tc.t('days', 'दिन')}", _rupees(v)) for b, v in r.aging.items()]
    notes = [tc.t(r.methodology, "सप्लायर की भुगतान शर्तें दर्ज नहीं हैं; उम्र खरीद की अपनी तारीख से गिनी जाती है।")]
    return _fin_answer(
        tc,
        "get_payables_summary",
        ("Payables", "देय"),
        ("What you owe suppliers.", "सप्लायरों को देय राशि।"),
        figures,
        ["Based on posted purchases, purchase returns and supplier payments"],
        None,
        notes,
        table,
    )


def _financial_ledger(tc: ToolContext, args: LedgerArgs) -> Answer:
    from app.models.enums import FinanceEventType

    period = tc.period(args, "this month")
    kinds = [FinanceEventType(args.event_type)] if args.event_type else None
    rows = finance_ledger_service.list_ledger(
        tc.session, tc.ctx.shop_id, period.start, period.end, event_types=kinds
    )
    if not rows:
        return Answer(
            NO_DATA,
            "get_financial_ledger",
            tc.t("Financial ledger", "वित्तीय खाता-बही"),
            NO_DATA_TEXT,
            period=_period_info(period),
        )
    cols = [
        tc.t("Date", "तारीख"),
        tc.t("Type", "प्रकार"),
        tc.t("Reference", "संदर्भ"),
        tc.t("Amount", "राशि"),
        tc.t("Settled", "निपटाया"),
        tc.t("Method", "तरीका"),
    ]
    body = [
        [
            r.entry_date.isoformat(),
            r.event_type.value,
            r.reference or f"{r.source_type}#{r.source_id}",
            _rupees(r.amount),
            _rupees(r.settled_amount),
            r.payment_method,
        ]
        for r in rows[: args.limit]
    ]
    total_in = sum((r.settled_amount for r in rows if r.direction.value == "IN"), ZERO)
    total_out = sum((r.settled_amount for r in rows if r.direction.value == "OUT"), ZERO)
    notes = [
        tc.t(
            f"Showing {min(len(rows), args.limit)} of {len(rows)} rows, newest first. Every row points to its source document.",
            f"{len(rows)} में से {min(len(rows), args.limit)} पंक्तियाँ, नई पहले।",
        )
    ]
    figures = [
        Figure(tc.t("Money in", "आवक"), _rupees(total_in)),
        Figure(tc.t("Money out", "जावक"), _rupees(total_out)),
    ]
    return _fin_answer(
        tc,
        "get_financial_ledger",
        ("Financial ledger", "वित्तीय खाता-बही"),
        (f"Money events for {period.label}.", f"{period.label} की धन-प्रविष्टियाँ।"),
        figures,
        [
            "Based on the financial ledger view over sales, purchases, returns, khata payments and finance entries"
        ],
        period,
        notes,
        Table(cols, body),
    )


def _tax_summary(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    t = tax_service.summary(tc.session, tc.ctx.shop_id, period.start, period.end)
    if t.status != "CONFIGURED":
        return Answer(
            NOT_CONFIGURED,
            "get_tax_summary",
            tc.t("Tax summary", "कर सारांश"),
            tc.t(
                "Tax has not been configured for this shop, so no tax can be calculated.",
                "इस दुकान के लिए कर सेट नहीं है, इसलिए कर की गणना नहीं हो सकती।",
            ),
            period=_period_info(period),
        )
    figures = [
        Figure(tc.t("Taxable sales", "कर योग्य बिक्री"), _rupees(t.sales.taxable_amount)),
        Figure(tc.t("Tax collected (net of returns)", "एकत्र कर (वापसी घटाकर)"), _rupees(t.tax_collected)),
        Figure(tc.t("Taxable purchases", "कर योग्य खरीद"), _rupees(t.purchases.taxable_amount)),
        Figure(tc.t("Tax paid (net of returns)", "चुकाया कर (वापसी घटाकर)"), _rupees(t.tax_paid)),
        Figure(tc.t("Indicative net tax", "संकेतात्मक शुद्ध कर"), _rupees(t.net_tax)),
    ]
    notes = [t.methodology, t.disclaimer, *t.notes]
    return _fin_answer(
        tc,
        "get_tax_summary",
        ("Tax summary", "कर सारांश"),
        (f"Tax reporting foundation figures for {period.label}.", f"{period.label} के कर रिपोर्टिंग आंकड़े।"),
        figures,
        ["Based on the shop's configured tax rates and its posted sales and purchases"],
        period,
        notes,
    )


def _reconciliation_summary(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    r = reconciliation_service.summary(tc.session, tc.ctx.shop_id, period.start, period.end)
    figures = [
        Figure(k.replace("_", " ").title(), f"{r.counts[k]} ({_rupees(r.amounts[k])})") for k in r.counts
    ]
    notes = [
        tc.t(
            "Bank Integration Not Configured: nothing is matched to a bank statement automatically.",
            "बैंक एकीकरण सेट नहीं है: कुछ भी बैंक स्टेटमेंट से अपने-आप नहीं मिलाया जाता।",
        )
    ]
    return _fin_answer(
        tc,
        "get_reconciliation_summary",
        ("Reconciliation", "मिलान"),
        (f"Electronic payment records for {period.label}.", f"{period.label} के इलेक्ट्रॉनिक भुगतान।"),
        figures,
        ["Based on the shop's own payment records and its manual review marks"],
        period,
        notes,
    )


def _financial_dashboard(tc: ToolContext, args: PeriodArgs) -> Answer:
    period = tc.period(args, "this month")
    d = finance_dashboard_service.build(tc.session, tc.ctx.shop_id, period.start, period.end, today=tc.today)
    p = d.pnl
    figures = [
        Figure(tc.t("Revenue", "राजस्व"), _rupees(p.revenue)),
        Figure(tc.t("Gross profit", "सकल मुनाफ़ा"), _na(p.gross_profit)),
        Figure(tc.t("Net profit", "शुद्ध मुनाफ़ा"), _na(p.net_profit)),
        Figure(tc.t("Operating expenses", "संचालन खर्च"), _rupees(p.operating_expenses)),
        Figure(tc.t("Cash inflow", "नकदी आवक"), _rupees(d.cash_flow.inflow)),
        Figure(tc.t("Cash outflow", "नकदी जावक"), _rupees(d.cash_flow.outflow)),
        Figure(tc.t("Customer outstanding", "ग्राहक बकाया"), _rupees(d.customer_outstanding)),
        Figure(tc.t("Supplier outstanding", "सप्लायर देय"), _rupees(d.supplier_outstanding)),
        Figure(tc.t("Alerts to review", "देखने योग्य अलर्ट"), str(d.alert_count)),
    ]
    return _fin_answer(
        tc,
        "get_financial_dashboard",
        ("Finance dashboard", "वित्त डैशबोर्ड"),
        (f"Finance overview for {period.label}.", f"{period.label} का वित्त सारांश।"),
        figures,
        ["Based on the finance dashboard's own services (profit and loss, cash flow, khata, purchases)"],
        period,
        list(d.notes),
    )


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


# What a person must be allowed to see for the assistant to answer from that data. The assistant never widens access: a
# tool the person could not open themselves is refused for them too. (A test lists every tool, so a new one cannot skip this.)
TOOL_PERMISSION: dict[str, str] = {
    "get_sales_summary": "REPORT_VIEW", "get_product_sales": "REPORT_VIEW", "get_profit_summary": "REPORT_VIEW",
    "get_business_dashboard": "REPORT_VIEW", "get_category_performance": "REPORT_VIEW",
    "get_declining_products": "REPORT_VIEW", "get_business_report": "REPORT_VIEW", "get_insights": "REPORT_VIEW",
    "get_anomalies": "REPORT_VIEW", "get_inventory_status": "INVENTORY_VIEW", "get_low_stock_products": "INVENTORY_VIEW",
    "get_slow_moving_products": "INVENTORY_VIEW", "get_reorder_recommendations": "INVENTORY_VIEW",
    "get_customer_outstanding": "KHATA_VIEW", "get_purchase_summary": "PURCHASE_VIEW",
    "get_purchase_suggestions": "PURCHASE_VIEW", "get_promotion_summary": "PROMOTION_VIEW",
    "get_promotion_ideas": "PROMOTION_VIEW", "get_online_order_summary": "ONLINE_ORDER_VIEW",
    "get_price_comparison": "PRICE_INTELLIGENCE_USE",
    "get_dead_stock": "INVENTORY_VIEW",
    "get_supplier_analytics": "REPORT_VIEW",
    "get_customer_profile": "CRM_VIEW",
    "get_customer_segments": "CRM_VIEW",
    "get_customer_retention_summary": "CRM_ANALYTICS_VIEW",
    "get_inactive_customers": "CRM_VIEW",
    "get_reactivation_candidates": "CRM_ANALYTICS_VIEW",
    "get_loyalty_summary": "LOYALTY_VIEW",
    "get_campaign_summary": "CAMPAIGN_VIEW",
    "get_referral_summary": "REFERRAL_VIEW",
    "get_customer_growth_dashboard": "CRM_ANALYTICS_VIEW",
    "get_revenue_summary": "FINANCE_VIEW",
    "get_pnl_summary": "FINANCE_VIEW",
    "get_expense_summary": "FINANCE_EXPENSE_VIEW",
    "get_cash_flow_summary": "FINANCE_VIEW",
    "get_receivables_summary": "FINANCE_VIEW",
    "get_payables_summary": "FINANCE_VIEW",
    "get_financial_ledger": "FINANCE_VIEW",
    "get_tax_summary": "FINANCE_VIEW",
    "get_reconciliation_summary": "FINANCE_VIEW",
    "get_financial_dashboard": "FINANCE_VIEW",
}  # fmt: skip

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
        Tool(
            "get_dead_stock",
            "Products with stock but no sales at all in the last 90 days.",
            NoArgs,
            BASIC,
            _dead_stock,
        ),
        Tool(
            "get_supplier_analytics",
            "Purchase totals, frequency and product count per supplier, from posted purchases.",
            NoArgs,
            ADVANCED,
            _supplier_analytics,
        ),
        Tool(
            "get_customer_profile",
            "One customer's purchases, outstanding balance, loyalty balance and segments.",
            CustomerArgs,
            BASIC,
            _customer_profile,
        ),
        Tool(
            "get_customer_segments",
            "How many customers are in each factual segment (new, active, inactive, credit, etc).",
            NoArgs,
            BASIC,
            _customer_segments,
        ),
        Tool(
            "get_customer_retention_summary",
            "Repeat purchase rate, inactive/reactivated counts, average purchase interval, cohort retention.",
            NoArgs,
            ADVANCED,
            _retention_summary,
        ),
        Tool(
            "get_inactive_customers",
            "Customers who have not purchased in a given number of days.",
            DaysArgs,
            BASIC,
            _inactive_customers,
        ),
        Tool(
            "get_reactivation_candidates",
            "The pool a reactivation campaign would target: customers inactive beyond a given number of days.",
            DaysArgs,
            BASIC,
            _reactivation_candidates,
        ),
        Tool(
            "get_loyalty_summary",
            "The loyalty program's status and points issued/redeemed/outstanding.",
            NoArgs,
            BASIC,
            _loyalty_summary,
        ),
        Tool(
            "get_campaign_summary",
            "How many campaigns exist, by status.",
            NoArgs,
            BASIC,
            _campaign_summary,
        ),
        Tool(
            "get_referral_summary",
            "Total, rewarded and pending referrals.",
            NoArgs,
            BASIC,
            _referral_summary,
        ),
        Tool(
            "get_customer_growth_dashboard",
            "The CRM dashboard's headline figures: customers, revenue, retention, loyalty, campaigns, referrals.",
            NoArgs,
            ADVANCED,
            _customer_growth_dashboard,
        ),
        Tool(
            "get_revenue_summary",
            "Revenue for a period: detailed sales + quick sales - returns.",
            PeriodArgs,
            ADVANCED,
            _revenue_summary,
        ),
        Tool(
            "get_pnl_summary",
            "Profit and loss for a period (revenue, cost, gross and net profit, expenses); says when cost data is missing.",
            PeriodArgs,
            ADVANCED,
            _pnl_summary,
        ),
        Tool(
            "get_expense_summary",
            "Posted expenses for a period, by category.",
            PeriodArgs,
            ADVANCED,
            _expense_summary,
        ),
        Tool(
            "get_cash_flow_summary",
            "Cash inflows, outflows and net cash flow for a period.",
            PeriodArgs,
            ADVANCED,
            _cash_flow_summary,
        ),
        Tool(
            "get_receivables_summary",
            "What customers owe (from Khata) with ageing.",
            NoArgs,
            ADVANCED,
            _receivables_summary,
        ),
        Tool(
            "get_payables_summary",
            "What the shop owes suppliers, with ageing.",
            NoArgs,
            ADVANCED,
            _payables_summary,
        ),
        Tool(
            "get_financial_ledger",
            "Recent money events (sales, purchases, payments, expenses...) with their source documents.",
            LedgerArgs,
            ADVANCED,
            _financial_ledger,
        ),
        Tool(
            "get_tax_summary",
            "Tax collected and paid from the shop's configured tax rates (a reporting foundation, not a return).",
            PeriodArgs,
            ADVANCED,
            _tax_summary,
        ),
        Tool(
            "get_reconciliation_summary",
            "How many electronic payment records are matched, partial, unmatched or need review.",
            PeriodArgs,
            ADVANCED,
            _reconciliation_summary,
        ),
        Tool(
            "get_financial_dashboard",
            "The finance dashboard's headline figures for a period.",
            PeriodArgs,
            ADVANCED,
            _financial_dashboard,
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
    authorization_service.require(ctx, TOOL_PERMISSION[name])
    entitlement_service.require_feature(session, ctx.shop_id, tool.feature)
    return tool.run(ToolContext(session, ctx, shop_today(get_shop(session, ctx.shop_id)), language), args)


__all__ = ["TOOL_PERMISSION", "TOOLS", "Tool", "ToolContext", "run_tool", "EntitlementError"]
