"""Cross-cutting critical checks for the analytics layer: same input gives the same output, pages add up, the executive dashboard
and the files agree, and nothing an analytics endpoint does writes anything."""

from datetime import timedelta

from sqlalchemy import func, select

from app.models import AuditLog, Sale
from tests import factories
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import make_purchase, make_quick_sale, make_sale

TODAY = today_in_shop_timezone()
API = "/api/v1/analytics"
P = {"preset": "this_month", "compare": "previous_period"}

GETS = [
    "kpis",
    "executive",
    "insights",
    "sales/summary",
    "sales/trend",
    "sales/products",
    "sales/categories",
    "sales/channels",
    "sales/payment-methods",
    "sales/discounts",
    "sales/promotions",
    "inventory/summary",
    "inventory/stock",
    "inventory/turnover",
    "inventory/movers",
    "inventory/aging",
    "inventory/movement",
    "inventory/purchase-vs-sales",
    "inventory/adjustments",
    "inventory/count-variance",
    "inventory/reorder",
    "inventory/stock-outs",
    "customers/overview",
    "customers/list",
    "customers/segments",
    "customers/loyalty",
    "customers/campaigns",
    "customers/referrals",
    "cohorts",
    "suppliers/overview",
    "suppliers/spend",
    "suppliers/products",
    "suppliers/returns",
    "finance/summary",
    "finance/trend",
    "finance/expenses",
    "finance/payment-mix",
    "finance/receivables-aging",
    "finance/payables-aging",
    "finance/tax",
    "builder/datasets",
    "reports",
]


def _seed(session, tenant):
    c = factories.make_customer(session, tenant.shop, name="Asha")
    s = factories.make_supplier(session, tenant.shop, name="Alpha")
    session.commit()
    make_sale(session, tenant, "1000.00", day=TODAY, cogs="600.00", customer_id=c.id)
    make_sale(session, tenant, "700.00", day=TODAY - timedelta(days=40), cogs="400.00")
    make_quick_sale(session, tenant, "250.00", day=TODAY)
    make_purchase(session, tenant, s, "300.00", day=TODAY)
    session.commit()


def test_every_analytics_read_is_deterministic(session, tenant_a, client_a):
    _seed(session, tenant_a)
    for path in GETS:
        first = client_a.get(f"{API}/{path}", params=P)
        assert first.status_code == 200, (path, first.text[:200])
        assert client_a.get(f"{API}/{path}", params=P).json() == first.json(), path


def test_no_analytics_read_writes_anything(session, tenant_a, client_a):
    _seed(session, tenant_a)
    tables = (Sale, AuditLog)
    before = tuple(session.scalar(select(func.count()).select_from(t)) for t in tables)
    for path in GETS:
        client_a.get(f"{API}/{path}", params=P)
    session.expire_all()
    assert before == tuple(session.scalar(select(func.count()).select_from(t)) for t in tables)


def test_pages_add_up_to_the_whole_report(session, tenant_a, client_a):
    for i in range(7):
        make_quick_sale(session, tenant_a, f"{10 + i}.00", day=TODAY - timedelta(days=i % 3))
    session.commit()
    params = {"preset": "this_month", "compare": "none", "bucket": "day"}
    whole = client_a.get(f"{API}/sales/trend", params={**params, "limit": 200}).json()
    seen = []
    for offset in range(0, whole["total"], 2):
        page = client_a.get(f"{API}/sales/trend", params={**params, "limit": 2, "offset": offset}).json()
        assert page["total"] == whole["total"] and len(page["rows"]) <= 2
        seen += page["rows"]
    assert seen == whole["rows"]


def test_a_limit_above_the_maximum_is_refused_not_clamped_silently(session, tenant_a, client_a):
    assert client_a.get(f"{API}/sales/trend", params={**P, "limit": 5000}).status_code == 422


def test_the_executive_dashboard_and_the_kpi_endpoint_agree(session, tenant_a, client_a):
    _seed(session, tenant_a)
    dash = client_a.get(f"{API}/executive", params=P).json()
    kpis = {
        k["definition"]["key"]: k["current"] for k in client_a.get(f"{API}/kpis", params=P).json()["kpis"]
    }
    seen = 0
    for section in dash["sections"]:
        for k in section["kpis"]:
            assert k["current"] == kpis[k["definition"]["key"]], k["definition"]["key"]
            seen += 1
    assert seen >= 25


def test_sales_by_channel_keeps_quick_and_detailed_apart_and_never_double_counts(session, tenant_a, client_a):
    _seed(session, tenant_a)
    rows = {
        r["channel"]: r
        for r in client_a.get(f"{API}/sales/channels", params={**P, "compare": "none"}).json()["rows"]
    }
    summary = client_a.get(f"{API}/sales/summary", params={**P, "compare": "none"}).json()
    assert (
        summary["combined_revenue"] == "1250.00"
        and summary["quick_revenue"] == "250.00"
        and summary["detailed_revenue"] == "1000.00"
    )
    assert rows["Quick sales"]["revenue"] == "250.00" and rows["Detailed sales"]["revenue"] == "1000.00"
    assert rows["Online sales"]["revenue"] is None  # not connected: unknown, never zero
    assert rows["Combined revenue (before returns)"]["revenue"] == "1250.00"


JUNK = [
    {"preset": "nonsense"},
    {"date_from": "not-a-date", "date_to": "2026-01-01", "preset": "custom"},
    {"preset": "custom", "date_from": "2026-05-01", "date_to": "2026-01-01"},
    {"product_id": "abc"},
    {"category_id": "-1"},
    {"limit": "0"},
    {"offset": "-5"},
    {"compare": "sideways"},
    {"channel": "MAGIC"},
    {"bucket": "hour"},
    {"kind": "weird"},
    {"months": "999"},
    {"month": "2026-13"},
    {"day": "x"},
    {"id": "abc", "kind": "quick"},
    {"category_id": "abc"},
    {"customer_id": "1; DROP TABLE sales"},
    {"brand": "'; DROP TABLE sales; --"},
    {"payment_method": "\x00"},
    {"key": "x' OR '1'='1"},
    {"columns": "a,b"},
    {"format": "exe"},
]


def test_junk_input_is_refused_with_a_client_error_never_a_server_error(session, tenant_a, client_a):
    """Every analytics read and every drill level, with bad parameters: the answer is 2xx or 4xx, never 5xx."""
    from app.core.permissions import ROUTE_RULES

    make_quick_sale(session, tenant_a, "10.00", day=TODAY)
    session.commit()
    paths = [
        r.template
        for r in ROUTE_RULES
        if r.method == "GET" and r.template.startswith("/analytics") and r.template != "/analytics/overview"
    ]
    levels = {
        "revenue": ["months", "days", "sales", "sale"],
        "inventory": ["categories", "products", "transactions"],
        "customers": ["segments", "customers", "transactions"],
        "expenses": ["categories", "expenses", "source"],
        "suppliers": ["suppliers", "purchases", "purchase"],
        "finance": ["kpis", "ledger", "source"],
    }
    urls = []
    for template in paths:
        if "{level}" in template:
            family = template.split("/")[3]
            urls += [template.replace("{level}", lv) for lv in levels[family]]
        elif "{report_key}" in template:
            urls += [
                template.replace("{report_key}", k) for k in ("kpis", "sales-trend-day", "saved-abc", "nope")
            ]
        elif "{report_id}" in template:
            urls.append(template.replace("{report_id}", "999999"))
        else:
            urls.append(template)
    assert len(urls) > 60
    for url in urls:
        for params in [{}, *JUNK]:
            r = client_a.get(f"/api/v1{url}", params=params)
            assert r.status_code < 500, (url, params, r.status_code, r.text[:200])


def test_bad_filter_values_are_named_refusals(session, tenant_a, client_a):
    for params in (
        {"payment_method": "BITCOIN"},
        {"payment_method": "\x00"},
        {"brand": "a\x00b"},
        {"brand": "x" * 101},
    ):
        r = client_a.get(f"{API}/sales/summary", params=params)
        assert r.status_code == 422, (params, r.status_code)
    assert (
        client_a.get(f"{API}/sales/summary", params={"payment_method": "upi"}).status_code == 200
    )  # case-insensitive
    assert (
        client_a.post(
            f"{API}/reports", json={"name": "a\x00b", "dataset": "sales", "definition": {}}
        ).status_code
        == 422
    )


def test_drill_parameters_that_are_not_numbers_or_dates_are_refused(session, tenant_a, client_a):
    assert client_a.get(f"{API}/drill/inventory/products", params={"category_id": "abc"}).status_code == 422
    assert client_a.get(f"{API}/drill/revenue/sales", params={"day": "x"}).status_code == 422
    assert client_a.get(f"{API}/drill/revenue/days", params={"month": "2026-13"}).status_code == 422
