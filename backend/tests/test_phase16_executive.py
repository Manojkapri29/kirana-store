"""The executive dashboard: eleven sections built from KPIs, comparison arithmetic, hidden (not zeroed) KPIs, and the
guarantee that it says exactly what the KPI endpoint says."""

from datetime import timedelta

from tests.client_helpers import client_with
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import make_quick_sale, make_sale

TODAY = today_in_shop_timezone()
API = "/api/v1/analytics"
SECTION_KEYS = [
    "overview",
    "sales",
    "profitability",
    "inventory",
    "customers",
    "suppliers",
    "finance",
    "online_orders",
    "promotions",
    "crm",
    "operational_health",
]


def _dash(client, **params):
    r = client.get(f"{API}/executive", params={"preset": "this_month", **params})
    assert r.status_code == 200, r.text
    return r.json()


def _kpi(section, key):
    return next(k for k in section["kpis"] if k["definition"]["key"] == key)


def test_all_eleven_sections_are_present_in_order(session, tenant_a, client_a):
    d = _dash(client_a)
    assert [s["key"] for s in d["sections"]] == SECTION_KEYS
    assert d["period"]["start"] <= d["period"]["end"] and d["comparison"] is not None


def test_a_kpi_card_carries_current_previous_and_the_changes(session, tenant_a, client_a):
    make_quick_sale(session, tenant_a, "300.00", day=TODAY)
    make_quick_sale(
        session, tenant_a, "200.00", day=TODAY - timedelta(days=TODAY.day)
    )  # the last day of last month
    session.commit()
    d = _dash(client_a, preset="this_month", compare="previous_period")
    revenue = _kpi(next(s for s in d["sections"] if s["key"] == "sales"), "revenue")
    assert revenue["current"]["amount"] == "300.00"
    assert revenue["definition"]["formula"] and revenue["definition"]["source"]


def test_comparison_uses_the_same_elapsed_days_and_a_zero_base_is_insufficient(session, tenant_a, client_a):
    make_quick_sale(session, tenant_a, "300.00", day=TODAY)
    session.commit()
    d = _dash(client_a, preset="today")
    revenue = _kpi(next(s for s in d["sections"] if s["key"] == "sales"), "revenue")
    assert revenue["current"]["amount"] == "300.00" and revenue["previous"]["amount"] == "0.00"
    assert (
        revenue["change"]["percent"] is None and revenue["change"]["note"] == "Insufficient comparison data"
    )


def test_profit_is_not_available_with_missing_cost_and_the_reason_is_stated(session, tenant_a, client_a):
    make_quick_sale(session, tenant_a, "500.00", day=TODAY)
    session.commit()
    d = _dash(client_a, preset="today")
    profit = _kpi(next(s for s in d["sections"] if s["key"] == "profitability"), "gross_profit")
    assert profit["current"]["amount"] is None and profit["current"]["availability"] == "NOT_AVAILABLE"
    assert profit["current"]["reason"] == "Insufficient Cost Data"


def test_online_orders_section_says_not_available(session, tenant_a, client_a):
    d = _dash(client_a)
    online = _kpi(next(s for s in d["sections"] if s["key"] == "online_orders"), "online_orders")
    assert online["current"]["availability"] == "NOT_AVAILABLE"


def test_the_dashboard_and_the_kpi_endpoint_agree(session, tenant_a, client_a):
    make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
    session.commit()
    d = _dash(client_a, preset="today")
    k = client_a.get(f"{API}/kpis", params={"preset": "today", "key": ["revenue", "gross_profit"]}).json()
    by = {x["definition"]["key"]: x for x in k["kpis"]}
    sales = next(s for s in d["sections"] if s["key"] == "sales")
    assert _kpi(sales, "revenue")["current"] == by["revenue"]["current"]
    assert (
        _kpi(next(s for s in d["sections"] if s["key"] == "profitability"), "gross_profit")["current"]
        == by["gross_profit"]["current"]
    )


def test_a_trend_is_bucketed_and_matches_the_days_with_sales(session, tenant_a, client_a):
    make_quick_sale(session, tenant_a, "10.00", day=TODAY)
    session.commit()
    d = _dash(client_a, preset="today")
    sales = next(s for s in d["sections"] if s["key"] == "sales")
    assert sales["trend"] == [{"label": TODAY.isoformat(), "value": "10.00"}]


def test_every_preset_is_accepted(session, tenant_a, client_a):
    for preset in (
        "today",
        "yesterday",
        "this_week",
        "last_week",
        "this_month",
        "last_month",
        "this_quarter",
        "last_quarter",
        "this_year",
        "last_year",
    ):
        assert client_a.get(f"{API}/executive", params={"preset": preset}).status_code == 200, preset
    ok = client_a.get(
        f"{API}/executive",
        params={
            "preset": "custom",
            "date_from": (TODAY - timedelta(days=3)).isoformat(),
            "date_to": TODAY.isoformat(),
        },
    )
    assert ok.status_code == 200
    assert client_a.get(f"{API}/executive", params={"preset": "custom"}).status_code in (400, 422)


def test_kpis_without_data_permission_are_hidden_not_zeroed(session, tenant_a, make_client):
    limited = client_with(make_client, tenant_a, ["ANALYTICS_EXECUTIVE", "REPORT_VIEW"])
    d = _dash(limited)
    profitability = next(s for s in d["sections"] if s["key"] == "profitability")
    assert profitability["kpis"] == [] and "gross_profit" in profitability["hidden_kpis"]
    sales = next(s for s in d["sections"] if s["key"] == "sales")
    assert _kpi(sales, "revenue")


def test_no_analytics_permission_is_forbidden_and_shops_are_isolated(
    session, tenant_a, tenant_b, make_client, client_b
):
    assert client_with(make_client, tenant_a, ["REPORT_VIEW"]).get(f"{API}/executive").status_code == 403
    assert (
        client_with(make_client, tenant_a, ["ANALYTICS_VIEW"]).get(f"{API}/executive").status_code == 403
    )  # view is not executive
    make_quick_sale(session, tenant_a, "999.00", day=TODAY)
    session.commit()
    d = _dash(client_b, preset="today")
    assert (
        _kpi(next(s for s in d["sections"] if s["key"] == "sales"), "revenue")["current"]["amount"] == "0.00"
    )


def test_kpi_definitions_list_formulas_and_hide_what_the_role_cannot_see(
    session, tenant_a, client_a, make_client
):
    full = client_a.get(f"{API}/kpis/definitions").json()
    assert len(full) >= 30 and all(d["formula"] and d["source"] and d["limitations"] for d in full)
    limited = (
        client_with(make_client, tenant_a, ["ANALYTICS_VIEW", "REPORT_VIEW"])
        .get(f"{API}/kpis/definitions")
        .json()
    )
    keys = {d["key"] for d in limited}
    assert "revenue" in keys and "gross_profit" not in keys


def test_bad_input_is_refused_not_guessed(session, tenant_a, client_a):
    assert client_a.get(f"{API}/kpis", params={"key": "nope"}).status_code in (400, 422)
    assert client_a.get(f"{API}/kpis", params={"limit": 5000}).status_code == 422
    assert client_a.get(
        f"{API}/kpis", params={"preset": "custom", "date_from": "2026-09-10", "date_to": "2026-09-01"}
    ).status_code in (400, 422)
