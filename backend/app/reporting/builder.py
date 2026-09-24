"""Custom report builder over an ALLOWLIST. Nothing a user sends is ever turned into SQL or evaluated.

A definition names a dataset from `DATASETS`, fields from that dataset, filters, grouping and aggregations from fixed lists of
operators. Every name is looked up in the registry below; anything not listed is rejected before any data is read. Rows come
from the same reporting providers the standard screens use (so the numbers agree), are limited to `MAX_ROWS`, and grouping and
aggregating happen in Python on those rows with exact `Decimal` arithmetic. There are no free joins: a dataset's related names
(customer, supplier) are already resolved by its provider.

Aggregations: SUM, AVG, MIN, MAX (numbers; MIN/MAX also dates) and COUNT (any field). A SUM/AVG/MIN/MAX over a group that
contains an unknown value (a blank profit, for instance) is itself Not Available, never computed as if the blank were zero.
Datasets that need data the system does not hold (Online Orders) are listed as unavailable and cannot be run.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, Purchase, QuickSale, Sale, Supplier
from app.models.enums import PurchaseStatus, SaleStatus
from app.reporting import (
    cohorts as _cohorts,  # noqa: F401  (documented dependency: cohorts stay a standard report, not a dataset)
)
from app.reporting import customers as customer_reports
from app.reporting import finance as finance_reports
from app.reporting import inventory as inventory_reports
from app.reporting import sales as sales_reports
from app.reporting import suppliers as supplier_reports
from app.reporting.filters import ReportFilters, ReportTable
from app.services.errors import ForbiddenError, InvalidInputError

MAX_ROWS = 20_000
MAX_FIELDS, MAX_GROUPS, MAX_AGGREGATIONS, MAX_FILTERS, MAX_IN = 20, 5, 10, 10, 50
NUMERIC = {"integer", "money", "quantity", "percent"}
FILTER_OPS = {
    "text": {"eq", "ne", "contains", "in"},
    "date": {"eq", "ne", "gt", "gte", "lt", "lte"},
    "integer": {"eq", "ne", "gt", "gte", "lt", "lte", "in"},
    "money": {"eq", "ne", "gt", "gte", "lt", "lte"},
    "quantity": {"eq", "ne", "gt", "gte", "lt", "lte"},
    "percent": {"eq", "ne", "gt", "gte", "lt", "lte"},
}
AGG_OPS = ("SUM", "AVG", "MIN", "MAX", "COUNT")
DEFINITION_KEYS = {"columns", "group_by", "aggregations", "filters", "sort"}
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str  # text | date | integer | money | quantity | percent
    permission: str | None = None  # an extra permission needed to see this one field


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    permissions: tuple[str, ...]  # ALL are needed to read the dataset
    fields: tuple[Field, ...]
    provider: Callable[[Session, int, ReportFilters, date], list[dict]] | None
    source: str
    notes: tuple[str, ...] = ()
    unavailable: str | None = None
    period_applies: bool = True


def _f(key: str, label: str, kind: str, permission: str | None = None) -> Field:
    return Field(key, label, kind, permission)


def _wide(f: ReportFilters) -> ReportFilters:
    return ReportFilters(period=f.period, comparison=None, limit=MAX_ROWS)


# --- Providers: each returns plain rows (dicts) for the period ----------------------------------------------------


def _sales_rows(session: Session, shop_id: int, f: ReportFilters, today: date) -> list[dict]:
    names = dict(session.execute(select(Customer.id, Customer.name).where(Customer.shop_id == shop_id)).all())
    rows: list[dict] = []
    for model, kind, no_col, paid_col in (
        (Sale, "Detailed", Sale.invoice_no, Sale.amount_paid),
        (QuickSale, "Quick", QuickSale.quick_no, QuickSale.amount_paid),
    ):
        q = (
            select(
                model.sale_date,
                no_col,
                model.customer_id,
                model.payment_method,
                model.discount,
                model.total_amount,
                paid_col,
            )
            .where(
                model.shop_id == shop_id,
                model.status == SaleStatus.POSTED,
                model.sale_date >= f.period.start,
                model.sale_date <= f.period.end,
            )
            .order_by(model.sale_date, model.id)
            .limit(MAX_ROWS)
        )
        for day, no, cid, method, discount, total, paid in session.execute(q):
            rows.append(
                {
                    "sale_date": day,
                    "document_no": no or "",
                    "kind": kind,
                    "customer": names.get(cid, "") if cid else "",
                    "payment_method": method.value if method else "",
                    "discount": discount,
                    "total_amount": total,
                    "amount_paid": paid,
                }
            )
    rows.sort(key=lambda r: (r["sale_date"], r["document_no"]))
    return rows[:MAX_ROWS]


def _purchase_rows(session: Session, shop_id: int, f: ReportFilters, today: date) -> list[dict]:
    q = (
        select(
            Purchase.purchase_date,
            Purchase.purchase_no,
            Supplier.name,
            Purchase.payment_method,
            Purchase.total_amount,
            Purchase.amount_paid,
        )
        .join(Supplier, (Supplier.shop_id == Purchase.shop_id) & (Supplier.id == Purchase.supplier_id))
        .where(
            Purchase.shop_id == shop_id,
            Purchase.status == PurchaseStatus.POSTED,
            Purchase.purchase_date >= f.period.start,
            Purchase.purchase_date <= f.period.end,
        )
        .order_by(Purchase.purchase_date, Purchase.id)
        .limit(MAX_ROWS)
    )
    return [
        {
            "purchase_date": d,
            "purchase_no": no or "",
            "supplier": name,
            "payment_method": m.value if m else "",
            "total_amount": total,
            "amount_paid": paid,
        }
        for d, no, name, m, total, paid in session.execute(q)
    ]


def _table_rows(fn: Callable[..., ReportTable]) -> Callable[[Session, int, ReportFilters, date], list[dict]]:
    return lambda session, shop_id, f, today: fn(session, shop_id, _wide(f)).rows


DATASETS: dict[str, Dataset] = {}


def _dataset(ds: Dataset) -> None:
    DATASETS[ds.key] = ds


_dataset(
    Dataset(
        "sales",
        "Sales",
        ("REPORT_VIEW",),
        (
            _f("sale_date", "Sale date", "date"),
            _f("document_no", "Invoice / quick sale number", "text"),
            _f("kind", "Kind (Detailed or Quick)", "text"),
            _f("customer", "Customer", "text", "CUSTOMER_VIEW"),
            _f("payment_method", "Payment method", "text"),
            _f("discount", "Discount", "money"),
            _f("total_amount", "Total", "money"),
            _f("amount_paid", "Amount paid", "money"),
        ),
        _sales_rows,
        "Posted detailed sales and quick sales (one row per bill)",
        ("Quick Sales are money-only: they have no products.", "Draft and voided bills are never included."),
    )
)
_dataset(
    Dataset(
        "purchases",
        "Purchases",
        ("PURCHASE_VIEW",),
        (
            _f("purchase_date", "Purchase date", "date"),
            _f("purchase_no", "Purchase number", "text"),
            _f("supplier", "Supplier", "text", "SUPPLIER_VIEW"),
            _f("payment_method", "Payment method", "text"),
            _f("total_amount", "Total", "money"),
            _f("amount_paid", "Amount paid", "money"),
        ),
        _purchase_rows,
        "Posted purchases (one row per purchase)",
    )
)
_dataset(
    Dataset(
        "inventory",
        "Inventory",
        ("INVENTORY_VIEW",),
        (
            _f("product", "Product", "text"),
            _f("sku", "SKU", "text"),
            _f("category", "Category", "text"),
            _f("unit", "Unit", "text"),
            _f("current_stock", "Current stock", "quantity"),
            _f("reorder_level", "Reorder level", "quantity"),
            _f("status", "Status", "text"),
            _f("average_cost", "Average cost", "money"),
            _f("stock_value", "Stock value", "money"),
        ),
        _table_rows(inventory_reports.stock),
        "Inventory ledger stock and average cost (the position now)",
        ("Stock and value are the current position, not limited to the period.",),
        period_applies=False,
    )
)
_dataset(
    Dataset(
        "customers",
        "Customers",
        ("CUSTOMER_VIEW", "CRM_ANALYTICS_VIEW"),
        (
            _f("customer", "Customer", "text"),
            _f("purchases", "Purchases", "integer"),
            _f("detailed", "Detailed bills", "integer"),
            _f("quick", "Quick sales", "integer"),
            _f("revenue", "Revenue", "money"),
            _f("average_purchase", "Average purchase", "money"),
            _f("last_purchase", "Last purchase", "date"),
            _f("segments", "Segments", "text"),
        ),
        lambda s, shop, f, today: customer_reports.customers(s, shop, _wide(f), today).rows,
        "Posted sales that name a customer; CRM segments",
    )
)
_dataset(
    Dataset(
        "suppliers",
        "Suppliers",
        ("SUPPLIER_VIEW", "PURCHASE_VIEW"),
        (
            _f("supplier", "Supplier", "text"),
            _f("purchases", "Purchases", "integer"),
            _f("purchase_value", "Purchase value", "money"),
            _f("purchase_returns", "Returns", "money"),
            _f("net_spend", "Net spend", "money"),
            _f("share_pct", "Share of spend %", "percent"),
            _f("product_count", "Products", "integer"),
            _f("average_purchase_value", "Average purchase", "money"),
            _f("last_purchase", "Last purchase", "date"),
        ),
        _table_rows(supplier_reports.suppliers),
        "Posted purchases and purchase returns",
        ("There is no quality, reliability or delivery data.",),
    )
)
_dataset(
    Dataset(
        "finance",
        "Finance",
        ("FINANCE_VIEW",),
        (
            _f("period", "Period start", "text"),
            _f("revenue", "Revenue", "money"),
            _f("cogs", "Cost of goods sold", "money"),
            _f("gross_profit", "Gross profit", "money"),
            _f("operating_expenses", "Expenses", "money"),
            _f("net_profit", "Net profit", "money"),
            _f("net_cash_flow", "Net cash flow", "money"),
        ),
        lambda s, shop, f, today: (
            finance_reports.trend(s, shop, _wide(f), finance_reports.auto_bucket(f)).rows
        ),
        "Profit and loss and the financial ledger, per day, week or month",
        (
            "Rows are days for a period of about two months or less, weeks up to about a year, otherwise months.",
            "A blank profit is Profit Not Available (Insufficient Cost Data); SUM or AVG over it is also Not Available.",
        ),
    )
)
_dataset(
    Dataset(
        "expenses",
        "Expenses",
        ("FINANCE_EXPENSE_VIEW",),
        (
            _f("category", "Category", "text"),
            _f("amount", "Posted expenses", "money"),
            _f("previous_amount", "Previous period", "money"),
            _f("change", "Change", "money"),
        ),
        lambda s, shop, f, today: (
            finance_reports.expenses(
                s, shop, ReportFilters(period=f.period, comparison=f.comparison, limit=MAX_ROWS)
            ).rows
        ),
        "The finance ledger (posted expenses less reversals), per category",
    )
)
_dataset(
    Dataset(
        "online_orders",
        "Online Orders",
        ("REPORT_VIEW",),
        (_f("order_date", "Order date", "date"), _f("total_amount", "Total", "money")),
        None,
        "Online orders",
        unavailable="Not Available in the report builder. Use the Online orders screen, or the online orders KPI on the analytics dashboard.",
    )
)
_dataset(
    Dataset(
        "promotions",
        "Promotions",
        ("REPORT_VIEW",),
        (
            _f("promotion", "Promotion", "text"),
            _f("bills", "Bills", "integer"),
            _f("bill_revenue", "Revenue of those bills", "money"),
            _f("discount_given", "Discount given", "money"),
        ),
        _table_rows(sales_reports.promotions),
        "Posted detailed sales and their promotion snapshots",
        ("Revenue is that of whole bills that used the promotion; it is not revenue caused by it.",),
    )
)
_dataset(
    Dataset(
        "crm",
        "CRM segments",
        ("CRM_ANALYTICS_VIEW",),
        (
            _f("segment", "Segment", "text"),
            _f("customers", "Purchasing customers", "integer"),
            _f("purchases", "Purchases", "integer"),
            _f("revenue", "Revenue", "money"),
        ),
        lambda s, shop, f, today: customer_reports.segments(s, shop, _wide(f), today).rows,
        "CRM segments and identified purchases",
        ("Segments overlap; do not add the rows.",),
    )
)
_dataset(
    Dataset(
        "loyalty",
        "Loyalty",
        ("LOYALTY_VIEW",),
        (
            _f("entry_type", "Entry type", "text"),
            _f("entries", "Entries", "integer"),
            _f("points", "Points (signed)", "integer"),
        ),
        _table_rows(customer_reports.loyalty),
        "The loyalty ledger",
        ("Points, not money.",),
    )
)


# --- Definition ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Aggregation:
    field: str
    op: str
    key: str
    label: str


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class Query:
    dataset: Dataset
    columns: list[str]
    group_by: list[str]
    aggregations: list[Aggregation]
    filters: list[Condition]
    sort: list[tuple[str, bool]]  # (output key, descending)
    fields: dict[str, Field] = field(default_factory=dict)


def _bad(message: str, where: str) -> InvalidInputError:
    return InvalidInputError(message, field=where)


def visible_fields(ds: Dataset, granted: set[str] | None) -> list[Field]:
    return [f for f in ds.fields if granted is None or f.permission is None or f.permission in granted]


def catalog(granted: set[str] | None) -> list[dict]:
    """What the builder screen offers: only datasets and fields the caller is allowed to use."""
    out = []
    for ds in DATASETS.values():
        allowed = granted is None or set(ds.permissions) <= granted
        out.append(
            {
                "key": ds.key,
                "label": ds.label,
                "available": allowed and ds.unavailable is None,
                "reason": ds.unavailable
                or (None if allowed else "Your role does not include the permission to read this data."),
                "source": ds.source,
                "notes": list(ds.notes),
                "period_applies": ds.period_applies,
                "fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "kind": f.kind,
                        "operators": sorted(FILTER_OPS[f.kind]),
                        "aggregations": list(_aggs_for(f.kind)),
                    }
                    for f in visible_fields(ds, granted)
                ]
                if allowed
                else [],
            }
        )
    return out


def _aggs_for(kind: str) -> tuple[str, ...]:
    if kind in NUMERIC:
        return AGG_OPS
    return ("COUNT", "MIN", "MAX") if kind == "date" else ("COUNT",)


def _as_list(definition: dict, key: str, limit: int) -> list:
    value = definition.get(key) or []
    if not isinstance(value, list):
        raise _bad(f"'{key}' must be a list.", key)
    if len(value) > limit:
        raise _bad(f"'{key}' can have at most {limit} entries.", key)
    return value


def _value(kind: str, raw: Any, where: str) -> Any:
    try:
        if kind == "date":
            return raw if isinstance(raw, date) else date.fromisoformat(str(raw))
        if kind in NUMERIC:
            if isinstance(raw, bool):
                raise ValueError
            return Decimal(str(raw))
        if not isinstance(raw, str):
            raise ValueError
        return raw
    except (ValueError, InvalidOperation) as exc:
        raise _bad(f"'{raw}' is not a valid {kind} value.", where) from exc


def validate(definition: dict, dataset_key: str, granted: set[str] | None) -> Query:
    """Turn a JSON definition into a Query, or raise. Unknown datasets, fields, operators, aggregations and keys are refused; so
    are datasets and fields the caller has no permission to read (they are reported as not existing, not as hidden)."""
    if not isinstance(definition, dict):
        raise _bad("The report definition must be an object.", "definition")
    extra = set(definition) - DEFINITION_KEYS
    if extra:
        raise _bad(f"Unknown definition key(s): {', '.join(sorted(map(str, extra)))}.", "definition")
    ds = DATASETS.get(dataset_key)
    if ds is None:
        raise _bad(f"Unknown dataset '{dataset_key}'.", "dataset")
    if granted is not None and not set(ds.permissions) <= granted:
        raise ForbiddenError(f"Your role cannot read the {ds.label} dataset.")
    if ds.unavailable:
        raise _bad(ds.unavailable, "dataset")
    fields = {f.key: f for f in visible_fields(ds, granted)}

    def need(key: Any, where: str) -> Field:
        if not isinstance(key, str) or key not in fields:
            raise _bad(f"'{key}' is not a field of the {ds.label} dataset.", where)
        return fields[key]

    columns = [need(c, "columns").key for c in _as_list(definition, "columns", MAX_FIELDS)]
    group_by = [need(c, "group_by").key for c in _as_list(definition, "group_by", MAX_GROUPS)]
    if len(set(columns)) != len(columns) or len(set(group_by)) != len(group_by):
        raise _bad("A field can be listed only once.", "columns")
    aggregations: list[Aggregation] = []
    for i, a in enumerate(_as_list(definition, "aggregations", MAX_AGGREGATIONS)):
        if not isinstance(a, dict) or set(a) - {"field", "op", "label"}:
            raise _bad(
                "Each aggregation needs only 'field' and 'op' (and an optional 'label').",
                f"aggregations[{i}]",
            )
        f = need(a.get("field"), f"aggregations[{i}].field")
        op = str(a.get("op", "")).upper()
        if op not in _aggs_for(f.kind):
            raise _bad(
                f"{op or 'That operation'} cannot be applied to '{f.label}'. Allowed: {', '.join(_aggs_for(f.kind))}.",
                f"aggregations[{i}].op",
            )
        label = str(a.get("label") or f"{op.title()} of {f.label}")[:60]
        aggregations.append(Aggregation(f.key, op, f"{op.lower()}_{f.key}", label))
    if len({a.key for a in aggregations}) != len(aggregations):
        raise _bad("The same aggregation is listed twice.", "aggregations")
    if aggregations and columns:
        raise _bad("Choose either detail 'columns' or 'group_by' with 'aggregations', not both.", "columns")
    conditions: list[Condition] = []
    for i, c in enumerate(_as_list(definition, "filters", MAX_FILTERS)):
        if not isinstance(c, dict) or set(c) != {"field", "op", "value"}:
            raise _bad("Each filter needs exactly 'field', 'op' and 'value'.", f"filters[{i}]")
        f = need(c["field"], f"filters[{i}].field")
        op = c["op"]
        if op not in FILTER_OPS[f.kind]:
            raise _bad(
                f"'{op}' cannot be used on '{f.label}'. Allowed: {', '.join(sorted(FILTER_OPS[f.kind]))}.",
                f"filters[{i}].op",
            )
        if op == "in":
            if not isinstance(c["value"], list) or not 0 < len(c["value"]) <= MAX_IN:
                raise _bad(f"'in' needs a list of 1 to {MAX_IN} values.", f"filters[{i}].value")
            value: Any = [_value(f.kind, v, f"filters[{i}].value") for v in c["value"]]
        else:
            value = _value(f.kind, c["value"], f"filters[{i}].value")
        conditions.append(Condition(f.key, op, value))
    if not columns and not group_by and not aggregations:
        columns = list(fields)  # default: every field the caller may see
    out_keys = columns or [*group_by, *[a.key for a in aggregations]]
    sort: list[tuple[str, bool]] = []
    for i, s in enumerate(_as_list(definition, "sort", 3)):
        if not isinstance(s, dict) or set(s) - {"field", "direction"}:
            raise _bad("Each sort needs 'field' and optionally 'direction' (asc or desc).", f"sort[{i}]")
        key, direction = s.get("field"), str(s.get("direction", "asc")).lower()
        if key not in out_keys or direction not in ("asc", "desc"):
            raise _bad(
                f"Sort by one of the report's own columns: {', '.join(out_keys)}; direction asc or desc.",
                f"sort[{i}]",
            )
        sort.append((key, direction == "desc"))
    return Query(ds, columns, group_by, aggregations, conditions, sort, fields)


# --- Execution ----------------------------------------------------------------------------------------------------


def _matches(row: dict, c: Condition) -> bool:
    v = row.get(c.field)
    if v is None:
        return False  # an unknown value never satisfies a condition
    if c.op == "eq":
        return v == c.value
    if c.op == "ne":
        return v != c.value
    if c.op == "gt":
        return v > c.value
    if c.op == "gte":
        return v >= c.value
    if c.op == "lt":
        return v < c.value
    if c.op == "lte":
        return v <= c.value
    if c.op == "contains":
        return c.value.casefold() in str(v).casefold()
    return v in c.value


def _aggregate(op: str, kind: str, values: list) -> Any:
    if op == "COUNT":
        return sum(1 for v in values if v is not None)
    if not values or any(v is None for v in values):
        return None  # an unknown input makes the total unknown; it is never counted as zero
    if op == "MIN":
        return min(values)
    if op == "MAX":
        return max(values)
    total = sum((Decimal(v) for v in values), Decimal(0))
    if op == "SUM":
        return total.quantize(Decimal("0.01")) if kind == "money" else total
    return (total / len(values)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def run(session: Session, shop_id: int, q: Query, f: ReportFilters, today: date) -> ReportTable:
    """Read the dataset's rows for the period, then filter, group, aggregate and sort them. The result is a `ReportTable` like
    every other report, so it exports and drills down the same way."""
    rows = q.dataset.provider(session, shop_id, f, today) if q.dataset.provider else []
    truncated = len(rows) >= MAX_ROWS
    rows = [r for r in rows if all(_matches(r, c) for c in q.filters)]
    label = {k: fld.label for k, fld in q.fields.items()}
    kind = {k: fld.kind for k, fld in q.fields.items()}
    if q.columns:
        columns = [(k, label[k], kind[k]) for k in q.columns]
        out = [{k: r.get(k) for k in q.columns} for r in rows]
    else:
        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            groups.setdefault(tuple(r.get(k) for k in q.group_by), []).append(r)
        if not q.group_by and q.aggregations:
            groups = {(): rows}
        columns = [(k, label[k], kind[k]) for k in q.group_by]
        for a in q.aggregations:
            columns.append((a.key, a.label, "integer" if a.op == "COUNT" else kind[a.field]))
        out = []
        for key, members in groups.items():
            row = dict(zip(q.group_by, key, strict=True))
            for a in q.aggregations:
                row[a.key] = _aggregate(a.op, kind[a.field], [m.get(a.field) for m in members])
            out.append(row)
        if not q.sort:
            out.sort(key=lambda r: tuple((r.get(k) is None, str(r.get(k))) for k in q.group_by))
    for key, desc in reversed(q.sort):  # stable sorts, last key first; unknown values always go last
        known = sorted(
            (r for r in out if r.get(key) is not None), key=lambda r, key=key: r[key], reverse=desc
        )
        out = known + [r for r in out if r.get(key) is None]
    notes = list(q.dataset.notes)
    if truncated:
        notes.append(
            f"Only the first {MAX_ROWS} rows of the period were read. Narrow the dates for a complete report."
        )
    if any(v is None for r in out for v in r.values()):
        notes.append("A blank cell is Not Available: the value is unknown, not zero.")
    if not q.dataset.period_applies:
        notes.append("This dataset is the current position; the date range does not apply to it.")
    return ReportTable(
        columns=columns,
        rows=out[f.offset : f.offset + f.limit],
        total=len(out),
        limit=f.limit,
        offset=f.offset,
        title=f"Custom report: {q.dataset.label}",
        period=f.period,
        comparison=None,
        notes=notes,
        filters_applied=[],
        filters_ignored=[],
        source=q.dataset.source,
    )
