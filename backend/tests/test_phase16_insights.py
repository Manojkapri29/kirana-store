"""Cross-module insights: factual wording, honest gaps, permission-hidden insights are not computed."""

from datetime import timedelta

from tests.client_helpers import client_with
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import make_quick_sale, make_sale

TODAY = today_in_shop_timezone()
API = "/api/v1/analytics/insights"
P = {"preset": "this_month", "compare": "previous_period"}
CAUSAL = ("because", "caused", "due to", "led to", "resulted in", "should", "will ")


def _get(client, **params):
    r = client.get(API, params={**P, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _by_key(body):
    return {i["key"]: i for i in body["insights"]}


def test_online_repeat_is_not_available_never_zero(session, tenant_a, client_a):
    i = _by_key(_get(client_a))["online_repeat_customers"]
    assert i["availability"] == "NOT_AVAILABLE" and "not analysed separately" in i["statement"]


def test_empty_shop_reports_insufficient_data_not_zeros(session, tenant_a, client_a):
    ins = _by_key(_get(client_a))
    assert ins["high_sales_low_stock"]["availability"] == "INSUFFICIENT_DATA"
    assert ins["promotion_associated_revenue"]["availability"] == "INSUFFICIENT_DATA"
    assert ins["sales_and_purchase_cost"]["availability"] == "INSUFFICIENT_DATA"


def test_revenue_and_profit_states_facts_and_hides_profit_without_cost(session, tenant_a, client_a):
    make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
    session.commit()
    i = _by_key(_get(client_a))["revenue_and_gross_profit"]
    assert i["availability"] == "AVAILABLE" and "1000.00" in i["statement"] and "400.00" in i["statement"]
    make_quick_sale(session, tenant_a, "500.00", day=TODAY)
    session.commit()
    j = _by_key(_get(client_a))["revenue_and_gross_profit"]
    assert j["availability"] == "NOT_AVAILABLE" and "Profit Not Available" in j["statement"]


def test_no_insight_claims_causation(session, tenant_a, client_a):
    make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
    make_sale(session, tenant_a, "800.00", day=TODAY - timedelta(days=40), cogs="500.00")
    session.commit()
    body = _get(client_a)
    for i in body["insights"]:
        text = " ".join([i["statement"], *i["limitations"]]).lower()
        assert not any(w in i["statement"].lower() for w in CAUSAL), i["statement"]
        assert "does not show that one caused" in " ".join(i["limitations"]).lower()
        assert text
    assert "caused" in body["caution"]


def test_hidden_insights_are_listed_not_computed(session, tenant_a, make_client):
    c = client_with(make_client, tenant_a, ["ANALYTICS_ADVANCED", "REPORT_VIEW"])
    body = _get(c)
    keys = set(_by_key(body))
    assert "revenue_and_gross_profit" in body["hidden"] and "revenue_and_gross_profit" not in keys
    assert "promotion_associated_revenue" in keys


def test_needs_advanced_analytics_permission(session, tenant_a, make_client):
    c = client_with(make_client, tenant_a, ["ANALYTICS_VIEW", "REPORT_VIEW"])
    assert c.get(API, params=P).status_code == 403


def test_cross_shop_isolation(session, tenant_a, tenant_b, client_b):
    make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
    session.commit()
    i = _by_key(_get(client_b))["revenue_and_gross_profit"]
    assert "1000.00" not in i["statement"]
