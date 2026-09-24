"""Drill-down: each level is a normal report with `drill` links to the next; totals reconcile down the path; tenant and
permission boundaries hold at every level."""

from datetime import timedelta
from decimal import Decimal

from app.models import Expense
from app.models.enums import ExpenseStatus, FinancePaymentMethod
from tests import factories
from tests.client_helpers import client_with
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import D, make_purchase, make_quick_sale, make_sale

TODAY = today_in_shop_timezone()
API = "/api/v1/analytics/drill"
P = {"preset": "this_month", "compare": "none"}


def _get(client, path, status=200, **params):
    r = client.get(f"{API}/{path}", params={**P, **params})
    assert r.status_code == status, r.text
    return r.json()


def _follow(client, row, **extra):
    d = dict(row["drill"])
    path, level = d.pop("path"), d.pop("level")
    return _get(client, f"{path}/{level}", **d, **extra)


def test_revenue_path_reconciles_month_to_day_to_sale_to_lines(session, tenant_a, client_a):
    make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00", qty=2)
    make_quick_sale(session, tenant_a, "50.00", day=TODAY)
    session.commit()
    months = _get(client_a, "revenue/months")
    (m,) = months["rows"]
    assert m["combined_net"] == "150.00"
    days = _follow(client_a, m)
    (d,) = days["rows"]
    assert d["combined_net"] == "150.00"
    sales = _follow(client_a, d)
    assert sorted(r["total"] for r in sales["rows"]) == ["100.00", "50.00"]
    detailed = next(r for r in sales["rows"] if r["kind"] == "Detailed")
    lines = _follow(client_a, detailed)
    assert lines["rows"][0]["line_total"] == "100.00" and lines["rows"][0]["cost"] == "60.00"
    quick = next(r for r in sales["rows"] if r["kind"] == "Quick")
    q = _follow(client_a, quick)
    assert q["rows"][0]["quantity"] is None and any("no products" in n for n in q["notes"])


def test_a_day_outside_the_period_is_refused(session, tenant_a, client_a):
    assert _get(client_a, "revenue/sales", status=422, day=(TODAY - timedelta(days=400)).isoformat())


def test_inventory_path_category_product_transactions(session, tenant_a, client_a):
    make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
    session.commit()
    cats = _get(client_a, "inventory/categories")
    row = next(r for r in cats["rows"] if r["drill"])
    prods = _follow(client_a, row)
    assert prods["rows"]
    txns = _follow(client_a, prods["rows"][0])
    assert isinstance(txns["rows"], list) and txns["source"].startswith("The inventory transaction ledger")


def test_customer_path_segment_customer_transactions(session, tenant_a, client_a):
    c = factories.make_customer(session, tenant_a.shop, name="Asha")
    session.commit()
    make_quick_sale(session, tenant_a, "70.00", day=TODAY, customer_id=c.id)
    session.commit()
    segs = _get(client_a, "customers/customers")
    (row,) = segs["rows"]
    assert row["customer"] == "Asha"
    txns = _follow(client_a, row)
    assert [r["total"] for r in txns["rows"]] == ["70.00"]
    assert _follow(client_a, txns["rows"][0])["rows"][0]["line_total"] == "70.00"


def test_supplier_path_supplier_purchases_lines(session, tenant_a, client_a):
    s = factories.make_supplier(session, tenant_a.shop, name="Alpha")
    make_purchase(session, tenant_a, s, "300.00", day=TODAY)
    session.commit()
    (row,) = _get(client_a, "suppliers/suppliers")["rows"]
    purchases = _follow(client_a, row)
    assert purchases["rows"][0]["total"] == "300.00"
    assert (
        _follow(client_a, purchases["rows"][0])["rows"] == []
    )  # the helper purchase has no lines: shown empty, not invented


def test_expense_path_category_expense_source(session, tenant_a, client_a):
    from app.services import expense_service
    from tests.conftest import context_for

    ctx = context_for(tenant_a)
    cat = expense_service.create_category(session, ctx, name="Rent")
    session.commit()
    e = expense_service.create_expense(
        session,
        ctx,
        {
            "expense_date": TODAY,
            "category_id": cat.id,
            "amount": D("500.00"),
            "payment_method": FinancePaymentMethod.CASH,
            "description": "Shop rent",
        },
    )
    session.commit()
    expense_service.submit_expense(session, ctx, e.id)
    session.commit()
    expense_service.post_expense(session, ctx, e.id)
    session.commit()
    assert session.get(Expense, e.id).status is ExpenseStatus.POSTED
    (cat_row,) = _get(client_a, "expenses/categories")["rows"]
    assert cat_row["amount"] == "500.00"
    (exp_row,) = _follow(client_a, cat_row)["rows"]
    assert exp_row["amount"] == "500.00"
    source = _follow(client_a, exp_row)
    assert len(source["rows"]) >= 2 and source["rows"][1]["what"] == "Ledger entry"


def test_finance_path_kpi_ledger_source(session, tenant_a, client_a):
    make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
    session.commit()
    kpis = _get(client_a, "finance/kpis")
    revenue = next(r for r in kpis["rows"] if r["kpi"] == "Revenue")
    ledger = _follow(client_a, revenue)
    assert [r["amount"] for r in ledger["rows"]] == ["100.00"]
    source = _follow(client_a, ledger["rows"][0])
    assert source["rows"][0]["line_total"] == "100.00"
    gross = next(r for r in kpis["rows"] if r["kpi"] == "Gross profit")
    assert gross["drill"] is None


def test_unknown_levels_and_missing_parameters_are_refused(session, tenant_a, client_a):
    assert _get(client_a, "revenue/nonsense", status=422)
    assert _get(client_a, "revenue/days", status=422)  # month is needed
    assert _get(client_a, "inventory/transactions", status=422)


def test_other_shops_records_are_not_reachable(session, tenant_a, tenant_b, client_a, client_b):
    s = factories.make_supplier(session, tenant_a.shop, name="Alpha")
    p = make_purchase(session, tenant_a, s, "300.00", day=TODAY)
    q = make_quick_sale(session, tenant_a, "9.00", day=TODAY)
    session.commit()
    assert _get(client_b, "suppliers/purchases", status=404, supplier_id=s.id)
    assert _get(client_b, "suppliers/purchase", status=404, purchase_id=p.id)
    assert _get(client_b, "revenue/sale", status=404, kind="quick", id=q.id if hasattr(q, "id") else 1)


def test_each_path_needs_its_data_permission(session, tenant_a, make_client):
    c = client_with(make_client, tenant_a, ["ANALYTICS_ADVANCED", "REPORT_VIEW"])
    assert c.get(f"{API}/revenue/months", params=P).status_code == 200
    for path in (
        "inventory/categories",
        "expenses/categories",
        "suppliers/suppliers",
        "finance/kpis",
        "customers/segments",
    ):
        assert c.get(f"{API}/{path}", params=P).status_code == 403, path


def test_totals_reconcile_with_the_standard_report(session, tenant_a, client_a):
    make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
    make_quick_sale(session, tenant_a, "50.00", day=TODAY)
    session.commit()
    summary = client_a.get("/api/v1/analytics/sales/summary", params=P).json()
    months = _get(client_a, "revenue/months")
    assert Decimal(months["rows"][0]["combined_net"]) == Decimal(summary["combined_revenue"])
