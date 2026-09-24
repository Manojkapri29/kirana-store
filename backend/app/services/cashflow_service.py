"""Cash-flow reporting from real money movements (the financial ledger view), not from invoices.

Only money that actually moved counts: `settled_amount` of every ledger row (a credit sale's unpaid part has not
moved yet, so it is not a cash inflow until the customer pays). Inflows minus outflows is the net cash flow.

Classification is kept to what is justified:
  OPERATING  - sales, customer payments, purchases, expenses, supplier payments, returns, other income
  FINANCING  - owner capital and owner withdrawals
  INVESTING  - only when an expense category is explicitly set to it (there is no asset module)
  OTHER      - only when a category or entry is explicitly set to it
  UNCLASSIFIED - adjustments and anything else with no justified class: shown as such, never forced into a class
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.enums import FlowDirection
from app.services import finance_ledger_service, pnl_service
from app.services.finance_ledger_service import LedgerRow

ZERO = Decimal("0.00")
UNCLASSIFIED = "UNCLASSIFIED"
CLASSES = ("OPERATING", "INVESTING", "FINANCING", "OTHER", UNCLASSIFIED)


@dataclass(frozen=True)
class Flow:
    inflow: Decimal
    outflow: Decimal

    @property
    def net(self) -> Decimal:
        return self.inflow - self.outflow


def _add(flow: Flow, row: LedgerRow) -> Flow:
    if row.direction is FlowDirection.IN:
        return Flow(flow.inflow + row.settled_amount, flow.outflow)
    return Flow(flow.inflow, flow.outflow + row.settled_amount)


@dataclass(frozen=True)
class CashFlowReport:
    date_from: date
    date_to: date
    inflow: Decimal
    outflow: Decimal
    net: Decimal
    by_class: dict[str, Flow]
    by_event: dict[str, Flow]
    by_method: dict[str, Flow]
    unclassified_amount: Decimal  # gross movement (in + out) with no justified class
    basis: str = (
        "Actual money movements: the settled part of each sale, purchase, return, payment, expense and entry."
    )
    notes: list[str] = field(default_factory=list)


def compute(session: Session, shop_id: int, date_from: date, date_to: date) -> CashFlowReport:
    rows = [
        r
        for r in finance_ledger_service.movements(session, shop_id, date_from, date_to)
        if r.settled_amount > 0
    ]
    by_class = {c: Flow(ZERO, ZERO) for c in CLASSES}
    by_event: dict[str, Flow] = {}
    by_method: dict[str, Flow] = {}
    total = Flow(ZERO, ZERO)
    for r in rows:
        klass = r.cash_flow_class.value if r.cash_flow_class else UNCLASSIFIED
        by_class[klass] = _add(by_class[klass], r)
        by_event[r.event_type.value] = _add(by_event.get(r.event_type.value, Flow(ZERO, ZERO)), r)
        by_method[r.payment_method] = _add(by_method.get(r.payment_method, Flow(ZERO, ZERO)), r)
        total = _add(total, r)
    unclassified = by_class[UNCLASSIFIED].inflow + by_class[UNCLASSIFIED].outflow
    notes = []
    if unclassified > 0:
        notes.append("Some movements (adjustments) have no justified class and are shown as UNCLASSIFIED.")
    if "NOT_RECORDED" in by_method:
        notes.append("Some movements have no payment method recorded; they are grouped as NOT_RECORDED.")
    return CashFlowReport(
        date_from,
        date_to,
        total.inflow,
        total.outflow,
        total.net,
        by_class,
        by_event,
        by_method,
        unclassified,
        notes=notes,
    )


@dataclass(frozen=True)
class FlowPoint:
    period_start: date
    period_end: date
    inflow: Decimal
    outflow: Decimal
    net: Decimal


def trend(
    session: Session, shop_id: int, date_from: date, date_to: date, granularity: str = "day"
) -> list[FlowPoint]:
    out = []
    for a, b in pnl_service._buckets(date_from, date_to, granularity):  # noqa: SLF001 - one bucketing rule
        r = compute(session, shop_id, a, b)
        out.append(FlowPoint(a, b, r.inflow, r.outflow, r.net))
    return out
