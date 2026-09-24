"""The KPI framework. Every KPI is a *definition* (name, description, formula, source, unit, limitations, the permission
needed to see it) plus a function that reads it from a `Snapshot` of one period. A KPI is never invented: when its source
data does not exist it is NOT_AVAILABLE, and when there is too little data to state it honestly it is INSUFFICIENT_DATA;
neither is ever shown as zero.

Comparison: `compute` reads a snapshot for the period and one for the comparison period, then states the absolute change
and, where meaningful, the percentage change. A percentage against a zero or unknown base is "Insufficient comparison
data", never a fabricated growth figure. For KPIs whose unit is a percentage the change is in percentage points.

Snapshots are cached per (shop, period) for the duration of one computation only (no cross-request cache), so a dashboard
that needs eight KPIs reads each source once.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LoyaltyLedger, Sale, SalePromotion, Unit
from app.models.enums import LoyaltyEntryType, SaleStatus
from app.reporting import facts
from app.reporting.filters import Period, ReportFilters
from app.services import (
    analytics_service,
    cashflow_service,
    expense_service,
    inventory_intelligence_service,
    payables_service,
    pnl_service,
    receivables_service,
    retention_service,
    sales_report_service,
)

ZERO = Decimal("0.00")
NOT_AVAILABLE = "Not Available"
INSUFFICIENT_DATA = "Insufficient Data"
INSUFFICIENT_COST = "Insufficient Cost Data"
INSUFFICIENT_COMPARISON = "Insufficient comparison data"


class Availability(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class Value:
    """A KPI value, or the reason there is none."""

    amount: Decimal | None
    availability: Availability = Availability.AVAILABLE
    reason: str | None = None


def ok(v: Decimal | int) -> Value:
    return Value(Decimal(v))


def na(reason: str) -> Value:
    return Value(None, Availability.NOT_AVAILABLE, reason)


def insufficient(reason: str) -> Value:
    return Value(None, Availability.INSUFFICIENT_DATA, reason)


@dataclass(frozen=True)
class KpiDefinition:
    key: str
    name: str
    group: str
    description: str
    formula: str
    source: str
    unit: str  # money | count | quantity | percent | ratio
    limitations: str
    permission: str  # the permission needed to read it (in addition to analytics access)
    as_of_only: bool = False  # true when only the current position is known, so no comparison is possible


def _pct(n: Decimal, d: Decimal) -> Decimal | None:
    return (n / d * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN) if d > 0 else None


@dataclass
class Snapshot:
    """Every raw figure of one period, worked out once from the existing services."""

    session: Session
    shop_id: int
    period: Period
    today: date
    _cache: dict = field(default_factory=dict)

    def memo(self, key: str, build):  # noqa: ANN001, ANN201
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    # -- building blocks (each one call to an existing service) --
    @property
    def pnl(self) -> pnl_service.ProfitAndLoss:
        return self.memo(
            "pnl", lambda: pnl_service.compute(self.session, self.shop_id, self.period.start, self.period.end)
        )

    @property
    def sales(self):  # noqa: ANN201
        return self.memo(
            "sales",
            lambda: sales_report_service.sales_summary(
                self.session, self.shop_id, self.period.start, self.period.end
            ),
        )

    @property
    def product_rows(self):  # noqa: ANN201
        return self.memo(
            "products",
            lambda: analytics_service.product_sales(
                self.session, self.shop_id, self.period.start, self.period.end
            ),
        )

    @property
    def count_units(self) -> set[str]:
        return self.memo(
            "count_units",
            lambda: {
                c for c in self.session.scalars(select(Unit.code).where(Unit.allows_decimal.is_(False)))
            },
        )

    @property
    def purchasers(self) -> dict[int, Decimal]:
        def build() -> dict[int, Decimal]:
            out: dict[int, Decimal] = {}
            for cid, _, amount, _ in facts.identified_purchases(
                self.session, self.shop_id, self.period.start, self.period.end
            ):
                out[cid] = out.get(cid, ZERO) + amount
            return out

        return self.memo("purchasers", build)

    @property
    def first_dates(self) -> dict[int, date]:
        return self.memo("first", lambda: facts.first_purchase_dates(self.session, self.shop_id))

    @property
    def health(self):  # noqa: ANN201
        end = min(self.period.end, self.today)
        return self.memo(
            "health",
            lambda: inventory_intelligence_service.inventory_health(
                self.session, self.shop_id, end, max(self.period.days, 1)
            ),
        )


# --- KPI value functions ------------------------------------------------------------------------------------------


def _orders(s: Snapshot) -> Value:
    return ok(s.sales.detailed.sales_count + s.sales.quick.sales_count)


def _units(s: Snapshot) -> Value:
    total = sum((r.quantity for r in s.product_rows if r.unit_code in s.count_units), ZERO)
    return ok(total)


def _atv(s: Snapshot) -> Value:
    orders = s.sales.detailed.sales_count + s.sales.quick.sales_count
    if orders == 0:
        return insufficient("No sales in this period")
    return ok(((s.sales.detailed.net + s.sales.quick.net) / orders).quantize(Decimal("0.01")))


def _profit(field_name: str):  # noqa: ANN202
    def read(s: Snapshot) -> Value:
        p = s.pnl
        value = getattr(p, field_name)
        if value is None:
            return na(INSUFFICIENT_COST)
        return ok(value)

    return read


def _margin(s: Snapshot) -> Value:
    p = s.pnl
    if p.gross_margin_pct is None:
        return na(INSUFFICIENT_COST) if p.gross_profit is None else insufficient("No revenue in this period")
    return ok(p.gross_margin_pct)


def _expense_ratio(s: Snapshot) -> Value:
    rev = s.pnl.revenue
    ratio = _pct(s.pnl.operating_expenses, rev)
    return insufficient("No revenue in this period") if ratio is None else ok(ratio)


def _inventory_value(s: Snapshot) -> Value:
    value, missing = inventory_intelligence_service.stock_value(s.session, s.shop_id)
    return na("No stock has a known average cost") if value is None else ok(value)


def _turnover(s: Snapshot) -> Value:
    opening = facts.stock_units_at(s.session, s.shop_id, s.period.start - timedelta(days=1))
    closing = facts.stock_units_at(s.session, s.shop_id, s.period.end)
    average = (opening + closing) / 2
    if average <= 0:
        return insufficient("No average stock on hand in count units")
    sold = _units(s).amount or ZERO
    return ok((sold / average).quantize(Decimal("0.01")))


def _health(field_name: str):  # noqa: ANN202
    return lambda s: ok(getattr(s.health, field_name))


def _new_customers(s: Snapshot) -> Value:
    return ok(sum(1 for cid in s.purchasers if s.period.contains(s.first_dates[cid])))


def _returning(s: Snapshot) -> Value:
    return ok(sum(1 for cid in s.purchasers if s.first_dates[cid] < s.period.start))


def _avg_customer_value(s: Snapshot) -> Value:
    if not s.purchasers:
        return insufficient("No purchases by identified customers in this period")
    return ok((sum(s.purchasers.values(), ZERO) / len(s.purchasers)).quantize(Decimal("0.01")))


def _repeat_rate(s: Snapshot) -> Value:
    r = retention_service.retention_summary(s.session, s.shop_id, s.today)
    return (
        insufficient("No customer has purchased yet")
        if r.repeat_purchase_rate is None
        else ok(r.repeat_purchase_rate)
    )


def _inactive(s: Snapshot) -> Value:
    return ok(retention_service.retention_summary(s.session, s.shop_id, s.today).inactive_customer_count)


def _receivables(s: Snapshot) -> Value:
    return ok(receivables_service.compute(s.session, s.shop_id, s.period.end).total_receivables)


def _payables(s: Snapshot) -> Value:
    return ok(payables_service.compute(s.session, s.shop_id, s.period.end).total_payable)


def _net_cash(s: Snapshot) -> Value:
    return ok(cashflow_service.compute(s.session, s.shop_id, s.period.start, s.period.end).net)


def _expense_value(s: Snapshot) -> Value:
    return ok(expense_service.posted_expense_total(s.session, s.shop_id, s.period.start, s.period.end))


def _return_rate(s: Snapshot) -> Value:
    gross = s.sales.detailed.net + s.sales.quick.net
    rate = _pct(s.sales.returns_total, gross)
    return insufficient("No sales in this period") if rate is None else ok(rate)


def _promo_usage(s: Snapshot) -> Value:
    detailed = s.sales.detailed.sales_count
    if detailed == 0:
        return insufficient("No detailed sales in this period")
    used = (
        s.session.scalar(
            select(func.count(func.distinct(SalePromotion.sale_id)))
            .join(Sale, (Sale.shop_id == SalePromotion.shop_id) & (Sale.id == SalePromotion.sale_id))
            .where(
                SalePromotion.shop_id == s.shop_id,
                Sale.status == SaleStatus.POSTED,
                Sale.sale_date >= s.period.start,
                Sale.sale_date <= s.period.end,
            )
        )
        or 0
    )
    return ok(_pct(Decimal(used), Decimal(detailed)) or ZERO)


def _loyalty(s: Snapshot) -> Value:
    issued = s.session.scalar(
        select(func.coalesce(func.sum(LoyaltyLedger.points_delta), 0)).where(
            LoyaltyLedger.shop_id == s.shop_id,
            LoyaltyLedger.entry_type == LoyaltyEntryType.EARN,
            LoyaltyLedger.entry_date >= s.period.start,
            LoyaltyLedger.entry_date <= s.period.end,
        )
    )
    return ok(int(issued or 0))


def _purchase_value(s: Snapshot) -> Value:
    return ok(analytics_service.purchase_totals(s.session, s.shop_id, s.period.start, s.period.end).net)


def _discount_given(s: Snapshot) -> Value:
    return ok(s.sales.detailed.discount + s.sales.quick.discount)


def _online(_s: Snapshot) -> Value:
    return na("This application has no online orders, so there is nothing to count.")


_D = KpiDefinition
KPIS: dict[str, tuple[KpiDefinition, object]] = {}


def _register(definition: KpiDefinition, fn) -> None:  # noqa: ANN001
    KPIS[definition.key] = (definition, fn)


_S, _P, _I, _C, _F, _O = "Sales", "Profitability", "Inventory", "Customers", "Finance", "Operations"
_register(
    _D(
        "revenue",
        "Revenue",
        _S,
        "Money earned from sales after returns.",
        "Detailed sales + Quick sales - Sales returns",
        "Posted sales, quick sales and sales returns",
        "money",
        "Combined revenue; Quick Sales have no product detail.",
        "REPORT_VIEW",
    ),
    lambda s: ok(s.pnl.revenue),
)
_register(
    _D(
        "orders",
        "Orders",
        _S,
        "Number of posted sales transactions.",
        "Count of posted detailed sales + posted quick sales",
        "Posted sales and quick sales",
        "count",
        "One quick sale is one transaction, however many items it covered.",
        "REPORT_VIEW",
    ),
    _orders,
)
_register(
    _D(
        "units_sold",
        "Units sold",
        _S,
        "Units of count-sold products sold, less returned.",
        "Sum of sold quantity - returned quantity, for products sold by whole units",
        "Detailed sale lines and sales return lines",
        "quantity",
        "Products sold by weight or volume are not summed with pieces. Quick Sales have no product quantity.",
        "REPORT_VIEW",
    ),
    _units,
)
_register(
    _D(
        "average_transaction_value",
        "Average transaction value",
        _S,
        "Average size of a sale.",
        "(Detailed net + Quick net) / number of transactions",
        "Posted sales and quick sales",
        "money",
        "Before returns.",
        "REPORT_VIEW",
    ),
    _atv,
)
_register(
    _D(
        "sales_growth",
        "Sales growth",
        _S,
        "Change in revenue against the comparison period.",
        "(Revenue - comparison revenue) / comparison revenue x 100",
        "Revenue KPI for both periods",
        "percent",
        "Needs a comparison period with revenue above zero.",
        "REPORT_VIEW",
    ),
    lambda s: ok(ZERO),
)  # replaced in compute()
_register(
    _D(
        "cogs",
        "Cost of goods sold",
        _P,
        "Cost of the goods sold.",
        "Cost snapshots stored on detailed-sale lines, less cost of returned goods",
        "Detailed sale line cost snapshots (costing service)",
        "money",
        "Available only when every sale in the period has a known cost; Quick Sales have no cost.",
        "FINANCE_VIEW",
    ),
    _profit("cogs"),
)
_register(
    _D(
        "gross_profit",
        "Gross profit",
        _P,
        "Revenue less cost of goods sold.",
        "Revenue - COGS",
        "Profit and loss",
        "money",
        "Profit Not Available when any cost is unknown.",
        "FINANCE_VIEW",
    ),
    _profit("gross_profit"),
)
_register(
    _D(
        "gross_margin",
        "Gross margin",
        _P,
        "Gross profit as a share of revenue.",
        "Gross profit / Revenue x 100",
        "Profit and loss",
        "percent",
        "Profit Not Available when any cost is unknown.",
        "FINANCE_VIEW",
    ),
    _margin,
)
_register(
    _D(
        "net_profit",
        "Net profit",
        _P,
        "Gross profit less posted expenses.",
        "Gross profit - posted operating expenses",
        "Profit and loss",
        "money",
        "Profit Not Available when any cost is unknown.",
        "FINANCE_VIEW",
    ),
    _profit("net_profit"),
)
_register(
    _D(
        "expense_ratio",
        "Expense ratio",
        _P,
        "Posted expenses as a share of revenue.",
        "Posted expenses / Revenue x 100",
        "Finance ledger and revenue",
        "percent",
        "Only posted expenses count.",
        "FINANCE_VIEW",
    ),
    _expense_ratio,
)
_register(
    _D(
        "inventory_value",
        "Inventory value",
        _I,
        "Stock on hand valued at average cost.",
        "Sum of (current stock x average cost)",
        "Inventory ledger and the costing service",
        "money",
        "Current position only: past stock values are not recorded. Products with no known cost are left out.",
        "INVENTORY_VIEW",
        as_of_only=True,
    ),
    _inventory_value,
)
_register(
    _D(
        "stock_turnover",
        "Stock turnover",
        _I,
        "How many times the average stock was sold in the period.",
        "Units sold / ((opening units + closing units) / 2)",
        "Inventory transaction ledger and detailed sales",
        "ratio",
        "Counts only products sold by whole units; not annualised.",
        "INVENTORY_VIEW",
    ),
    _turnover,
)
_register(
    _D(
        "fast_moving",
        "Fast-moving products",
        _I,
        "Products that sold in the period and still have stock.",
        "Count of products with units sold > 0",
        "Inventory intelligence over the period",
        "count",
        "Stock is the current position.",
        "INVENTORY_VIEW",
        as_of_only=True,
    ),
    _health("fast_moving_count"),
)
_register(
    _D(
        "slow_moving",
        "Slow-moving products",
        _I,
        "Stock that would take unusually long to sell at the period's pace.",
        "Count of products whose days of cover exceed 3x the period",
        "Inventory intelligence over the period",
        "count",
        "A statement about pace, not about quality.",
        "INVENTORY_VIEW",
        as_of_only=True,
    ),
    _health("slow_moving_count"),
)
_register(
    _D(
        "dead_stock",
        "Dead stock",
        _I,
        "Stock with no sales at all in the period.",
        "Count of products with stock > 0 and no units sold",
        "Inventory intelligence over the period",
        "count",
        "Depends on the chosen period length.",
        "INVENTORY_VIEW",
        as_of_only=True,
    ),
    _health("dead_stock_count"),
)
_register(
    _D(
        "stock_out_count",
        "Stock-outs",
        _I,
        "Products currently out of stock.",
        "Count of active products with stock <= 0",
        "Inventory ledger",
        "count",
        "Current position only.",
        "INVENTORY_VIEW",
        as_of_only=True,
    ),
    _health("out_of_stock"),
)
_register(
    _D(
        "new_customers",
        "New customers",
        _C,
        "Customers whose first purchase fell in the period.",
        "Count of identified customers with first purchase in the period",
        "Posted sales and quick sales that name a customer",
        "count",
        "A walk-in sale with no customer is not counted.",
        "CUSTOMER_VIEW",
    ),
    _new_customers,
)
_register(
    _D(
        "returning_customers",
        "Returning customers",
        _C,
        "Customers who bought in the period and had bought before it.",
        "Count of purchasers in the period whose first purchase is before the period",
        "Posted sales and quick sales that name a customer",
        "count",
        "Only identified customers.",
        "CUSTOMER_VIEW",
    ),
    _returning,
)
_register(
    _D(
        "repeat_purchase_rate",
        "Repeat purchase rate",
        _C,
        "Customers who have bought at least twice.",
        "Customers with 2+ purchases / customers with any purchase x 100",
        "Customer purchase history (retention service)",
        "percent",
        "Lifetime figure as of today, not limited to the period.",
        "CRM_ANALYTICS_VIEW",
        as_of_only=True,
    ),
    _repeat_rate,
)
_register(
    _D(
        "average_customer_value",
        "Average customer value",
        _C,
        "Average spend of a purchasing customer in the period.",
        "Sales to identified customers / distinct identified purchasing customers",
        "Posted sales and quick sales that name a customer",
        "money",
        "Only identified customers; before returns.",
        "CUSTOMER_VIEW",
    ),
    _avg_customer_value,
)
_register(
    _D(
        "inactive_customers",
        "Inactive customers",
        _C,
        "Customers who used to buy but have stopped.",
        "Retention service definition (no recent purchase)",
        "Customer purchase history (retention service)",
        "count",
        "Current position as of today.",
        "CRM_ANALYTICS_VIEW",
        as_of_only=True,
    ),
    _inactive,
)
_register(
    _D(
        "receivables",
        "Receivables",
        _F,
        "What customers owe the shop at the end of the period.",
        "Sum of positive khata balances as of the period end",
        "Khata (the customer ledger)",
        "money",
        "Khata is the only source; ageing has no due dates.",
        "FINANCE_VIEW",
    ),
    _receivables,
)
_register(
    _D(
        "payables",
        "Payables",
        _F,
        "What the shop owes suppliers at the end of the period.",
        "Purchases - returns - supplier payments, as of the period end",
        "Purchases, purchase returns and supplier payments",
        "money",
        "Suppliers have no payment terms.",
        "FINANCE_VIEW",
    ),
    _payables,
)
_register(
    _D(
        "net_cash_flow",
        "Net cash flow",
        _F,
        "Money that actually came in less money that went out.",
        "Inflows - outflows (settled amounts)",
        "The financial ledger view",
        "money",
        "A credit sale is not an inflow until it is paid.",
        "FINANCE_VIEW",
    ),
    _net_cash,
)
_register(
    _D(
        "expense_value",
        "Expenses",
        _F,
        "Posted expenses in the period.",
        "Posted expense entries less reversals",
        "The finance ledger",
        "money",
        "Drafts and unapproved expenses are not counted.",
        "FINANCE_EXPENSE_VIEW",
    ),
    _expense_value,
)
_register(
    _D(
        "purchase_value",
        "Purchase value",
        "Suppliers",
        "Money spent on purchases after returns.",
        "Posted purchases - purchase returns",
        "Posted purchases and purchase returns",
        "money",
        "Purchase returns settled in cash or supplier credit both reduce it.",
        "PURCHASE_VIEW",
    ),
    _purchase_value,
)
_register(
    _D(
        "discount_given",
        "Discounts given",
        _O,
        "Discounts allowed on sales (line, bill and offers).",
        "Line + bill + promotion discounts on detailed sales, plus quick-sale discounts",
        "Posted sales and quick sales",
        "money",
        "A quick sale's discount is its single transaction discount.",
        "REPORT_VIEW",
    ),
    _discount_given,
)
_register(
    _D(
        "online_orders",
        "Online orders",
        _O,
        "Orders placed online.",
        "Not available",
        "None: this application has no online orders",
        "count",
        "There is no online-order module.",
        "REPORT_VIEW",
    ),
    _online,
)
_register(
    _D(
        "return_rate",
        "Return rate",
        _O,
        "Refunds as a share of sales.",
        "Sales-return value / (Detailed net + Quick net) x 100",
        "Posted sales and sales returns",
        "percent",
        "By value, not by number of returns.",
        "REPORT_VIEW",
    ),
    _return_rate,
)
_register(
    _D(
        "promotion_usage",
        "Promotion usage",
        _O,
        "Share of detailed sales that used an offer or coupon.",
        "Sales with at least one promotion / detailed sales x 100",
        "Posted sales and their promotion snapshots",
        "percent",
        "Quick Sales cannot use offers.",
        "PROMOTION_VIEW",
    ),
    _promo_usage,
)
_register(
    _D(
        "loyalty_activity",
        "Loyalty points issued",
        _O,
        "Loyalty points earned in the period.",
        "Sum of EARN ledger points dated in the period",
        "The loyalty ledger",
        "count",
        "Points, not money.",
        "LOYALTY_VIEW",
    ),
    _loyalty,
)


@dataclass(frozen=True)
class Change:
    absolute: Decimal | None
    percent: Decimal | None
    note: str | None  # e.g. "Insufficient comparison data"


@dataclass(frozen=True)
class KpiResult:
    definition: KpiDefinition
    period: Period
    comparison_period: Period | None
    current: Value
    previous: Value | None
    change: Change | None


def _change(definition: KpiDefinition, cur: Value, prev: Value | None) -> Change | None:
    if prev is None:
        return None
    if definition.as_of_only:
        return Change(None, None, "Only the current position is known, so no comparison is possible")
    if cur.amount is None or prev.amount is None:
        return Change(None, None, INSUFFICIENT_COMPARISON)
    diff = cur.amount - prev.amount
    if definition.unit == "percent":
        return Change(diff.quantize(Decimal("0.01")), None, "Change is in percentage points")
    if prev.amount == 0:
        return Change(diff, None, INSUFFICIENT_COMPARISON)
    return Change(
        diff, (diff / abs(prev.amount) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN), None
    )


def compute(
    session: Session, shop_id: int, filters: ReportFilters, today: date, keys: list[str] | None = None,
    granted: set[str] | None = None,
) -> tuple[list[KpiResult], list[str]]:  # fmt: skip
    """The requested KPIs (all of them by default). `granted` limits results to KPIs whose permission the caller
    holds; the second return value lists the KPI keys that were hidden for lack of permission."""
    wanted = keys or list(KPIS)
    unknown = [k for k in wanted if k not in KPIS]
    if unknown:
        from app.services.errors import InvalidInputError

        raise InvalidInputError(f"Unknown KPI: {', '.join(unknown)}.", field="keys")
    current = Snapshot(session, shop_id, filters.period, today)
    previous = Snapshot(session, shop_id, filters.comparison, today) if filters.comparison else None
    results, hidden = [], []
    for key in wanted:
        definition, fn = KPIS[key]
        if granted is not None and definition.permission not in granted:
            hidden.append(key)
            continue
        if key == "sales_growth":
            results.append(_growth(definition, current, previous, filters))
            continue
        cur = fn(current)  # type: ignore[operator]
        prev = fn(previous) if previous is not None else None  # type: ignore[operator]
        results.append(
            KpiResult(
                definition, filters.period, filters.comparison, cur, prev, _change(definition, cur, prev)
            )
        )
    return results, hidden


@dataclass(frozen=True)
class KpiReport:
    period: Period
    comparison: Period | None
    kpis: list[KpiResult]
    hidden: list[str]
    notes: list[str]


def report(
    session: Session,
    shop_id: int,
    filters: ReportFilters,
    today: date,
    keys: list[str] | None,
    granted: set[str] | None,
) -> KpiReport:
    results, hidden = compute(session, shop_id, filters, today, keys, granted)
    notes = []
    if filters.supplied():
        notes.append(
            "KPIs are shop-wide for the period; these filters do not narrow them: "
            + ", ".join(filters.supplied())
            + "."
        )
    if hidden:
        notes.append("Some KPIs are hidden because your role does not include the permission to see them.")
    return KpiReport(filters.period, filters.comparison, results, hidden, notes)


def _growth(
    definition: KpiDefinition, cur: Snapshot, prev: Snapshot | None, filters: ReportFilters
) -> KpiResult:
    if prev is None:
        return KpiResult(
            definition, filters.period, None, insufficient("No comparison period chosen"), None, None
        )
    if prev.pnl.revenue <= 0:
        return KpiResult(
            definition, filters.period, filters.comparison, insufficient(INSUFFICIENT_COMPARISON), None, None
        )
    value = ((cur.pnl.revenue - prev.pnl.revenue) / prev.pnl.revenue * 100).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_EVEN
    )
    return KpiResult(definition, filters.period, filters.comparison, ok(value), None, None)


__all__ = ["KPIS", "KpiDefinition", "KpiResult", "Availability", "compute"]
