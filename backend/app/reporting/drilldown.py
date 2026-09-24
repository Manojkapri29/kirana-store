"""Drill-down paths. Each level is a normal report table; every row carries `drill`, the parameters that open the next level
(or None at the last level), so a screen never has to build a query. Each level re-checks the shop and the period, so a
drill link cannot reach another shop's record or a record outside the caller's permissions (the route requires the data
permission of the whole path).

Paths (level names):
  revenue    months > days > sales > sale                      (sales and quick sales; a quick sale has no lines)
  inventory  categories > products > transactions             (the inventory ledger is the source)
  customers  segments > customers > transactions              (only purchases that name the customer)
  expenses   categories > expenses > source                    (posted expenses; source = the expense and its ledger entries)
  suppliers  suppliers > purchases > purchase                  (posted purchases and their lines)
  finance    kpis > ledger > source                            (a finance figure > the ledger rows behind it > the source document)
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    Customer,
    Expense,
    ExpenseCategory,
    FinanceEntry,
    InventoryTransaction,
    Product,
    Purchase,
    PurchaseItem,
    QuickSale,
    Sale,
    SaleItem,
    Supplier,
)
from app.models.enums import ExpenseStatus, PurchaseStatus, SaleStatus
from app.reporting import customers as customer_reports
from app.reporting import finance as finance_reports
from app.reporting import inventory as inventory_reports
from app.reporting import sales as sales_reports
from app.reporting import suppliers as supplier_reports
from app.reporting.filters import ReportFilters, ReportTable
from app.services import finance_ledger_service
from app.services.errors import InvalidInputError, NotFoundError

LEVELS = {
    "revenue": ("months", "days", "sales", "sale"),
    "inventory": ("categories", "products", "transactions"),
    "customers": ("segments", "customers", "transactions"),
    "expenses": ("categories", "expenses", "source"),
    "suppliers": ("suppliers", "purchases", "purchase"),
    "finance": ("kpis", "ledger", "source"),
}
ZERO = Decimal("0.00")


def _with_drill(t: ReportTable, make) -> ReportTable:  # noqa: ANN001
    for r in t.rows:
        r["drill"] = make(r)
    t.notes = [*t.notes, "Open a row to see what is behind it."]
    return t


def _make(f: ReportFilters, title: str, columns, rows, source: str, notes=None) -> ReportTable:  # noqa: ANN001
    return sales_reports._table(f, title, columns, rows, set(), source, notes)  # noqa: SLF001


def _month_bounds(month: str) -> tuple[date, date]:
    try:
        first = date.fromisoformat(f"{month}-01")
    except ValueError as exc:
        raise InvalidInputError("Use a month like 2026-09.", field="month") from exc
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return first, nxt.fromordinal(nxt.toordinal() - 1)


def _need(value, name: str):  # noqa: ANN001, ANN202
    if value is None or value == "":
        raise InvalidInputError(f"'{name}' is needed at this level.", field=name)
    return value


def _int(value, name: str) -> int:  # noqa: ANN001
    """A required whole number from a drill parameter; text like 'abc' is a clear refusal, not a server error."""
    try:
        return int(_need(value, name))
    except (TypeError, ValueError):
        raise InvalidInputError(f"'{name}' must be a whole number.", field=name) from None


def _day(value, name: str) -> date:  # noqa: ANN001
    try:
        return date.fromisoformat(_need(value, name))
    except (TypeError, ValueError):
        raise InvalidInputError(f"'{name}' must be a date like 2026-09-24.", field=name) from None


def _narrow(f: ReportFilters, start: date, end: date) -> ReportFilters:
    """The drilled period stays inside the period the person chose."""
    from app.reporting.filters import Period

    s, e = max(start, f.period.start), min(end, f.period.end)
    if s > e:
        raise InvalidInputError("That range is outside the chosen period.", field="day")
    return ReportFilters(period=Period(s, e, f"{s} to {e}"), comparison=None, limit=f.limit, offset=f.offset)


# --- revenue --------------------------------------------------------------------------------------------------------


def revenue(session: Session, shop_id: int, f: ReportFilters, level: str, p: dict) -> ReportTable:
    if level == "months":
        t = sales_reports.trend(session, shop_id, f, "month")
        return _with_drill(t, lambda r: {"path": "revenue", "level": "days", "month": r["period"]})
    if level == "days":
        start, end = _month_bounds(_need(p.get("month"), "month"))
        t = sales_reports.trend(session, shop_id, _narrow(f, start, end), "day")
        return _with_drill(t, lambda r: {"path": "revenue", "level": "sales", "day": r["period"]})
    if level == "sales":
        day = _day(p.get("day"), "day")
        g = _narrow(f, day, day)
        names = dict(
            session.execute(select(Customer.id, Customer.name).where(Customer.shop_id == shop_id)).all()
        )
        rows = []
        for kind, model, no_col in (
            ("detailed", Sale, Sale.invoice_no),
            ("quick", QuickSale, QuickSale.quick_no),
        ):
            for sid, no, cid, method, total in session.execute(
                select(model.id, no_col, model.customer_id, model.payment_method, model.total_amount)
                .where(model.shop_id == shop_id, model.status == SaleStatus.POSTED, model.sale_date == day)
                .order_by(model.id)
            ):
                rows.append(
                    {
                        "kind": kind.title(),
                        "document_no": no or "",
                        "customer": names.get(cid, "") if cid else "",
                        "payment_method": method.value if method else "",
                        "total": total,
                        "drill": {"path": "revenue", "level": "sale", "kind": kind, "id": sid},
                    }
                )
        cols = [
            ("kind", "Kind", "text"),
            ("document_no", "Number", "text"),
            ("customer", "Customer", "text"),
            ("payment_method", "Payment method", "text"),
            ("total", "Total", "money"),
        ]
        return _make(g, f"Sales on {day}", cols, rows, "Posted sales and quick sales")
    if level == "sale":
        kind, sid = _need(p.get("kind"), "kind"), _int(p.get("id"), "id")
        if kind == "quick":
            q = session.scalar(select(QuickSale).where(QuickSale.shop_id == shop_id, QuickSale.id == sid))
            if q is None:
                raise NotFoundError("Quick sale not found")
            rows = [
                {
                    "item": "Quick Sale (money only)",
                    "quantity": None,
                    "unit_price": None,
                    "line_total": q.total_amount,
                    "cost": None,
                    "drill": None,
                }
            ]
            notes = ["A Quick Sale records money only: it has no products, so there are no lines to show."]
        elif kind == "detailed":
            if session.scalar(select(Sale.id).where(Sale.shop_id == shop_id, Sale.id == sid)) is None:
                raise NotFoundError("Sale not found")
            rows = [
                {
                    "item": name,
                    "quantity": qty,
                    "unit_price": price,
                    "line_total": total - promo,
                    "cost": cogs,
                    "drill": None,
                }
                for name, qty, price, total, promo, cogs in session.execute(
                    select(
                        Product.name,
                        SaleItem.quantity,
                        SaleItem.unit_price,
                        SaleItem.line_total,
                        SaleItem.promotion_discount,
                        SaleItem.cogs_amount,
                    )
                    .join(
                        Product, (Product.shop_id == SaleItem.shop_id) & (Product.id == SaleItem.product_id)
                    )
                    .where(SaleItem.shop_id == shop_id, SaleItem.sale_id == sid)
                    .order_by(SaleItem.id)
                )
            ]
            notes = ["Cost is blank where it was not recorded: it is Not Available, not zero."]
        else:
            raise InvalidInputError("kind must be detailed or quick.", field="kind")
        cols = [
            ("item", "Item", "text"),
            ("quantity", "Quantity", "quantity"),
            ("unit_price", "Unit price", "money"),
            ("line_total", "Line total", "money"),
            ("cost", "Cost", "money"),
        ]
        return _make(f, "Bill details", cols, rows, "The posted document", notes)
    raise InvalidInputError(f"Unknown level '{level}'.", field="level")


# --- inventory ------------------------------------------------------------------------------------------------------


def inventory(session: Session, shop_id: int, f: ReportFilters, level: str, p: dict) -> ReportTable:
    if level == "categories":
        t = inventory_reports.category_summary(session, shop_id, f)
        return _with_drill(
            t,
            lambda r: (
                {"path": "inventory", "level": "products", "category_id": r.get("category_id")}
                if r.get("category_id")
                else None
            ),
        )
    if level == "products":
        cid = _int(p.get("category_id"), "category_id")
        if session.scalar(select(Category.id).where(Category.shop_id == shop_id, Category.id == cid)) is None:
            raise NotFoundError("Category not found")
        g = ReportFilters(period=f.period, comparison=None, category_id=cid, limit=f.limit, offset=f.offset)
        return _with_drill(
            inventory_reports.stock(session, shop_id, g),
            lambda r: {"path": "inventory", "level": "transactions", "product_id": r["product_id"]},
        )
    if level == "transactions":
        pid = _int(p.get("product_id"), "product_id")
        if session.scalar(select(Product.id).where(Product.shop_id == shop_id, Product.id == pid)) is None:
            raise NotFoundError("Product not found")
        rows = [
            {
                "date": d,
                "type": t.value,
                "quantity_change": q,
                "unit_cost": c,
                "reference": f"{rt.value} #{rid}" if rt and rid else "",
                "reason": r.value if r else "",
                "note": n or "",
                "drill": None,
            }
            for d, t, q, c, rt, rid, r, n in session.execute(
                select(
                    InventoryTransaction.txn_date,
                    InventoryTransaction.txn_type,
                    InventoryTransaction.qty_delta,
                    InventoryTransaction.unit_cost,
                    InventoryTransaction.reference_type,
                    InventoryTransaction.reference_id,
                    InventoryTransaction.reason_code,
                    InventoryTransaction.note,
                )
                .where(
                    InventoryTransaction.shop_id == shop_id,
                    InventoryTransaction.product_id == pid,
                    InventoryTransaction.txn_date >= f.period.start,
                    InventoryTransaction.txn_date <= f.period.end,
                )
                .order_by(InventoryTransaction.txn_date, InventoryTransaction.id)
            )
        ]
        cols = [
            ("date", "Date", "date"),
            ("type", "Type", "text"),
            ("quantity_change", "Quantity change", "quantity"),
            ("unit_cost", "Unit cost", "money"),
            ("reference", "Source", "text"),
            ("reason", "Reason", "text"),
            ("note", "Note", "text"),
        ]
        return _make(
            f,
            "Stock movements",
            cols,
            rows,
            "The inventory transaction ledger",
            ["Every stock change is a ledger row; corrections appear as reversals, never edits."],
        )
    raise InvalidInputError(f"Unknown level '{level}'.", field="level")


# --- customers ------------------------------------------------------------------------------------------------------


def customers(
    session: Session, shop_id: int, f: ReportFilters, level: str, p: dict, today: date
) -> ReportTable:
    if level == "segments":
        return _with_drill(
            customer_reports.segments(session, shop_id, f, today),
            lambda r: {"path": "customers", "level": "customers", "segment": r["segment"]},
        )
    if level == "customers":
        seg = p.get("segment")
        wide = ReportFilters(period=f.period, comparison=None, limit=200)
        t = customer_reports.customers(session, shop_id, wide, today)
        if seg:
            t.rows = [r for r in t.rows if seg in [s.strip() for s in (r["segments"] or "").split(",")]]
            t.total = len(t.rows)
        return _with_drill(
            t, lambda r: {"path": "customers", "level": "transactions", "customer_id": r["customer_id"]}
        )
    if level == "transactions":
        cid = _int(p.get("customer_id"), "customer_id")
        name = session.scalar(select(Customer.name).where(Customer.shop_id == shop_id, Customer.id == cid))
        if name is None:
            raise NotFoundError("Customer not found")
        rows = []
        for kind, model, no_col in (
            ("Detailed", Sale, Sale.invoice_no),
            ("Quick", QuickSale, QuickSale.quick_no),
        ):
            for sid, d, no, total in session.execute(
                select(model.id, model.sale_date, no_col, model.total_amount).where(
                    model.shop_id == shop_id,
                    model.customer_id == cid,
                    model.status == SaleStatus.POSTED,
                    model.sale_date >= f.period.start,
                    model.sale_date <= f.period.end,
                )
            ):
                rows.append(
                    {
                        "date": d,
                        "kind": kind,
                        "document_no": no or "",
                        "total": total,
                        "drill": {"path": "revenue", "level": "sale", "kind": kind.lower(), "id": sid},
                    }
                )
        rows.sort(key=lambda r: (r["date"], r["document_no"]))
        cols = [
            ("date", "Date", "date"),
            ("kind", "Kind", "text"),
            ("document_no", "Number", "text"),
            ("total", "Total", "money"),
        ]
        return _make(
            f, f"Purchases by {name}", cols, rows, "Posted sales and quick sales naming this customer"
        )
    raise InvalidInputError(f"Unknown level '{level}'.", field="level")


# --- expenses -------------------------------------------------------------------------------------------------------


def expenses(session: Session, shop_id: int, f: ReportFilters, level: str, p: dict) -> ReportTable:
    if level == "categories":
        t = finance_reports.expenses(session, shop_id, f)
        return _with_drill(
            t, lambda r: {"path": "expenses", "level": "expenses", "category_id": r["category_id"]}
        )
    if level == "expenses":
        cid = _int(p.get("category_id"), "category_id")
        if (
            session.scalar(
                select(ExpenseCategory.id).where(
                    ExpenseCategory.shop_id == shop_id, ExpenseCategory.id == cid
                )
            )
            is None
        ):
            raise NotFoundError("Expense category not found")
        rows = [
            {
                "date": d,
                "expense_no": no,
                "payee": payee or "",
                "description": desc or "",
                "amount": amt,
                "payment_method": m.value,
                "drill": {"path": "expenses", "level": "source", "expense_id": eid},
            }
            for eid, d, no, payee, desc, amt, m in session.execute(
                select(
                    Expense.id,
                    Expense.expense_date,
                    Expense.expense_no,
                    Expense.payee,
                    Expense.description,
                    Expense.amount,
                    Expense.payment_method,
                )
                .where(
                    Expense.shop_id == shop_id,
                    Expense.category_id == cid,
                    Expense.status == ExpenseStatus.POSTED,
                    Expense.expense_date >= f.period.start,
                    Expense.expense_date <= f.period.end,
                )
                .order_by(Expense.expense_date, Expense.id)
            )
        ]
        cols = [
            ("date", "Date", "date"),
            ("expense_no", "Number", "text"),
            ("payee", "Payee", "text"),
            ("description", "Description", "text"),
            ("amount", "Amount", "money"),
            ("payment_method", "Paid by", "text"),
        ]
        return _make(
            f,
            "Posted expenses",
            cols,
            rows,
            "Posted expenses",
            ["Only posted expenses are listed; drafts, submitted and rejected ones are not expenses yet."],
        )
    if level == "source":
        eid = _int(p.get("expense_id"), "expense_id")
        e = session.scalar(select(Expense).where(Expense.shop_id == shop_id, Expense.id == eid))
        if e is None:
            raise NotFoundError("Expense not found")
        rows = [
            {
                "what": f"Expense {e.expense_no}",
                "date": e.expense_date,
                "amount": e.amount,
                "detail": f"{e.status.value}; {e.description or 'no description'}",
                "drill": None,
            }
        ]
        for fe in session.scalars(
            select(FinanceEntry)
            .where(
                FinanceEntry.shop_id == shop_id,
                FinanceEntry.reference_type == "EXPENSE",
                FinanceEntry.reference_id == eid,
            )
            .order_by(FinanceEntry.id)
        ):
            rows.append(
                {
                    "what": "Reversal entry" if fe.reverses_entry_id else "Ledger entry",
                    "date": fe.entry_date,
                    "amount": fe.amount,
                    "detail": f"{fe.direction.value}; {fe.payment_method.value}",
                    "drill": None,
                }
            )
        cols = [
            ("what", "Record", "text"),
            ("date", "Date", "date"),
            ("amount", "Amount", "money"),
            ("detail", "Detail", "text"),
        ]
        return _make(f, "Expense and its ledger entries", cols, rows, "The expense and the finance ledger")
    raise InvalidInputError(f"Unknown level '{level}'.", field="level")


# --- suppliers ------------------------------------------------------------------------------------------------------


def suppliers(session: Session, shop_id: int, f: ReportFilters, level: str, p: dict) -> ReportTable:
    if level == "suppliers":
        return _with_drill(
            supplier_reports.suppliers(session, shop_id, f),
            lambda r: {"path": "suppliers", "level": "purchases", "supplier_id": r["supplier_id"]},
        )
    if level == "purchases":
        sid = _int(p.get("supplier_id"), "supplier_id")
        if session.scalar(select(Supplier.id).where(Supplier.shop_id == shop_id, Supplier.id == sid)) is None:
            raise NotFoundError("Supplier not found")
        rows = [
            {
                "date": d,
                "purchase_no": no or "",
                "total": total,
                "paid": paid,
                "drill": {"path": "suppliers", "level": "purchase", "purchase_id": pid},
            }
            for pid, d, no, total, paid in session.execute(
                select(
                    Purchase.id,
                    Purchase.purchase_date,
                    Purchase.purchase_no,
                    Purchase.total_amount,
                    Purchase.amount_paid,
                )
                .where(
                    Purchase.shop_id == shop_id,
                    Purchase.supplier_id == sid,
                    Purchase.status == PurchaseStatus.POSTED,
                    Purchase.purchase_date >= f.period.start,
                    Purchase.purchase_date <= f.period.end,
                )
                .order_by(Purchase.purchase_date, Purchase.id)
            )
        ]
        cols = [
            ("date", "Date", "date"),
            ("purchase_no", "Number", "text"),
            ("total", "Total", "money"),
            ("paid", "Paid", "money"),
        ]
        return _make(f, "Purchases from the supplier", cols, rows, "Posted purchases")
    if level == "purchase":
        pid = _int(p.get("purchase_id"), "purchase_id")
        if session.scalar(select(Purchase.id).where(Purchase.shop_id == shop_id, Purchase.id == pid)) is None:
            raise NotFoundError("Purchase not found")
        rows = [
            {"product": name, "quantity": q, "unit_cost": c, "line_total": t, "drill": None}
            for name, q, c, t in session.execute(
                select(Product.name, PurchaseItem.quantity, PurchaseItem.unit_cost, PurchaseItem.line_total)
                .join(
                    Product,
                    (Product.shop_id == PurchaseItem.shop_id) & (Product.id == PurchaseItem.product_id),
                )
                .where(PurchaseItem.shop_id == shop_id, PurchaseItem.purchase_id == pid)
                .order_by(PurchaseItem.id)
            )
        ]
        cols = [
            ("product", "Product", "text"),
            ("quantity", "Quantity", "quantity"),
            ("unit_cost", "Unit cost", "money"),
            ("line_total", "Line total", "money"),
        ]
        return _make(f, "Purchase lines", cols, rows, "The posted purchase")
    raise InvalidInputError(f"Unknown level '{level}'.", field="level")


# --- finance --------------------------------------------------------------------------------------------------------


def finance(session: Session, shop_id: int, f: ReportFilters, level: str, p: dict) -> ReportTable:
    if level == "kpis":
        s = finance_reports.summary(session, shop_id, f)
        pnl = s["pnl"]
        rows = [
            {
                "kpi": "Revenue",
                "value": pnl["revenue"],
                "note": "",
                "drill": {"path": "finance", "level": "ledger", "kpi": "revenue"},
            },
            {
                "kpi": "Cost of goods sold",
                "value": pnl["cogs"],
                "note": "" if pnl["cogs"] is not None else "Not Available",
                "drill": None,
            },
            {
                "kpi": "Gross profit",
                "value": pnl["gross_profit"],
                "note": s["profit_message"] or "",
                "drill": None,
            },
            {
                "kpi": "Posted expenses",
                "value": pnl["operating_expenses"],
                "note": "",
                "drill": {"path": "finance", "level": "ledger", "kpi": "expenses"},
            },
            {
                "kpi": "Net cash flow",
                "value": s["net_cash_flow"],
                "note": "",
                "drill": {"path": "finance", "level": "ledger", "kpi": "cash"},
            },
        ]
        cols = [("kpi", "Figure", "text"), ("value", "Value", "money"), ("note", "Note", "text")]
        return _make(f, "Finance figures", cols, rows, "Profit and loss and the financial ledger")
    if level == "ledger":
        kpi = _need(p.get("kpi"), "kpi")
        if kpi not in ("revenue", "expenses", "cash"):
            raise InvalidInputError("kpi must be revenue, expenses or cash.", field="kpi")
        wanted = {"revenue": {"SALE", "SALE_RETURN"}, "expenses": {"EXPENSE"}}.get(kpi)
        rows = []
        for r in finance_ledger_service.movements(session, shop_id, f.period.start, f.period.end):
            if wanted is not None and r.event_type.value not in wanted:
                continue
            rows.append(
                {
                    "date": r.entry_date,
                    "event": r.event_type.value,
                    "reference": r.reference or "",
                    "amount": r.amount,
                    "settled": r.settled_amount,
                    "direction": r.direction.value,
                    "status": r.status,
                    "drill": {
                        "path": "finance",
                        "level": "source",
                        "source_type": r.source_type,
                        "source_id": r.source_id,
                    },
                }
            )
        cols = [
            ("date", "Date", "date"),
            ("event", "Event", "text"),
            ("reference", "Reference", "text"),
            ("amount", "Amount", "money"),
            ("settled", "Settled", "money"),
            ("direction", "Direction", "text"),
            ("status", "Status", "text"),
        ]
        return _make(
            f,
            "Ledger rows behind the figure",
            cols,
            rows,
            "The financial ledger",
            ["A reversed row stays visible with the reversal that cancels it."],
        )
    if level == "source":
        stype, sid = _need(p.get("source_type"), "source_type"), _int(p.get("source_id"), "source_id")
        mapping = {"SALE": ("revenue", "sale", "detailed"), "QUICK_SALE": ("revenue", "sale", "quick")}
        if stype in mapping:
            return revenue(session, shop_id, f, "sale", {"kind": mapping[stype][2], "id": sid})
        if stype == "EXPENSE":
            return expenses(session, shop_id, f, "source", {"expense_id": sid})
        if stype == "PURCHASE":
            return suppliers(session, shop_id, f, "purchase", {"purchase_id": sid})
        rows = [
            {
                "what": stype,
                "detail": f"Record #{sid}. Its own screen shows the full document.",
                "drill": None,
            }
        ]
        return _make(
            f,
            "Source record",
            [("what", "Source", "text"), ("detail", "Detail", "text")],
            rows,
            "The financial ledger",
        )
    raise InvalidInputError(f"Unknown level '{level}'.", field="level")
