"""Customer/CRM analytics and cohort retention: only identified purchases, factual segments, documented cohort definitions,
no extrapolation."""

from datetime import date, timedelta

from tests import factories
from tests.client_helpers import client_with
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import make_quick_sale

TODAY = today_in_shop_timezone()
API = "/api/v1/analytics"
P = {"preset": "this_month", "compare": "none"}


def _get(client, path, **params):
    r = client.get(f"{API}/{path}", params={**P, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _customer(session, tenant, name):
    c = factories.make_customer(session, tenant.shop, name=name)
    session.commit()
    return c


def _months_ago(n):
    y, m = divmod(TODAY.year * 12 + TODAY.month - 1 - n, 12)
    return date(y, m + 1, 15)


class TestOverview:
    def test_new_returning_value_and_frequency(self, session, tenant_a, client_a):
        old, new = _customer(session, tenant_a, "Old"), _customer(session, tenant_a, "New")
        make_quick_sale(session, tenant_a, "50.00", day=TODAY - timedelta(days=90), customer_id=old.id)
        make_quick_sale(session, tenant_a, "100.00", day=TODAY, customer_id=old.id)
        make_quick_sale(session, tenant_a, "300.00", day=TODAY, customer_id=new.id)
        make_quick_sale(session, tenant_a, "700.00", day=TODAY)  # a walk-in: belongs to nobody
        session.commit()
        o = _get(client_a, "customers/overview")
        assert (o["new_customers"], o["returning_customers"], o["purchasing_customers"]) == (1, 1, 2)
        assert o["average_customer_value"] == "200.00" and o["purchase_frequency"] == "1.00"
        assert o["revenue_from_identified_customers"] == "400.00"
        assert o["online_customer_activity"]["placed"] == 0 and "already inside" in o["online_note"]  # counted from the shop's own online orders; none here

    def test_an_empty_period_has_no_average_and_no_frequency(self, session, tenant_a, client_a):
        o = _get(client_a, "customers/overview")
        assert o["average_customer_value"] is None and o["purchase_frequency"] is None

    def test_customer_list_is_sorted_by_revenue_and_paginated(self, session, tenant_a, client_a):
        for i in range(4):
            c = _customer(session, tenant_a, f"C{i}")
            make_quick_sale(session, tenant_a, f"{10 * (i + 1)}.00", day=TODAY, customer_id=c.id)
        session.commit()
        t = _get(client_a, "customers/list", limit=2)
        assert [r["customer"] for r in t["rows"]] == ["C3", "C2"] and t["total"] == 4
        assert _get(client_a, "customers/list", customer_id=t["rows"][1]["customer_id"])["total"] == 1

    def test_segments_overlap_and_say_so(self, session, tenant_a, client_a):
        c = _customer(session, tenant_a, "Rani")
        make_quick_sale(session, tenant_a, "80.00", day=TODAY, customer_id=c.id)
        session.commit()
        t = _get(client_a, "customers/segments")
        assert t["rows"] and all(r["revenue"] == "80.00" for r in t["rows"])
        assert any("overlap" in n for n in t["notes"]) and any("not caused" in n for n in t["notes"])

    def test_no_sensitive_attribute_appears_in_any_customer_report(self, session, tenant_a, client_a):
        c = _customer(session, tenant_a, "Rani")
        make_quick_sale(session, tenant_a, "80.00", day=TODAY, customer_id=c.id)
        session.commit()
        text = str(
            [_get(client_a, p) for p in ("customers/list", "customers/segments", "customers/overview")]
        ).lower()
        assert not any(
            w in text
            for w in ("religion", "caste", "gender", "ethnic", "health", "income level", "political")
        )

    def test_campaigns_are_honest_about_delivery_and_loyalty_referrals_respond(
        self, session, tenant_a, client_a
    ):
        from app.models.enums import NotificationChannel
        from app.services import campaign_service
        from tests.conftest import context_for

        ctx = context_for(tenant_a)
        _customer(session, tenant_a, "Rani")
        campaign = campaign_service.create(
            session,
            ctx,
            name="Diwali",
            description=None,
            channel=NotificationChannel.IN_APP,
            target_group_id=None,
            target_segment="NEW",
            promotion_id=None,
            message_template="Hi",
        )
        session.commit()
        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()
        t = _get(client_a, "customers/campaigns")
        assert t["rows"][0]["sent"] == 0 and any("Not Configured" in n for n in t["notes"])
        for path in ("customers/loyalty", "customers/referrals"):
            assert client_a.get(f"{API}/{path}", params=P).status_code == 200


class TestCohorts:
    def _buy(self, session, tenant, customer, when, amount="100.00"):
        make_quick_sale(session, tenant, amount, day=when, customer_id=customer.id)

    def test_retention_repeat_revenue_and_average_by_month(self, session, tenant_a, client_a):
        a, b, c = (_customer(session, tenant_a, n) for n in "ABC")
        m2, m1 = _months_ago(2), _months_ago(1)
        for cust in (a, b, c):
            self._buy(
                session, tenant_a, cust, m2
            )  # first purchase: the cohort three customers strong, two months ago
        self._buy(session, tenant_a, a, m1, "50.00")  # A returns in month 1
        self._buy(session, tenant_a, b, m1, "70.00")  # B returns in month 1
        self._buy(session, tenant_a, a, m2 + timedelta(days=1), "10.00")  # A's second purchase inside month 0
        session.commit()
        t = _get(
            client_a,
            "cohorts",
            preset="custom",
            date_from=m2.replace(day=1).isoformat(),
            date_to=m2.isoformat(),
        )
        rows = {r["month_offset"]: r for r in t["rows"]}
        assert (
            rows[0]["cohort_size"] == 3
            and rows[0]["active_customers"] == 3
            and rows[0]["retention_pct"] == "100.00"
        )
        assert (
            rows[0]["repeat_purchasers"] == 1 and rows[0]["revenue"] == "310.00" and rows[0]["purchases"] == 4
        )
        assert (
            rows[1]["active_customers"] == 2
            and rows[1]["retention_pct"] == "66.67"
            and rows[1]["repeat_purchasers"] == 2
        )
        assert rows[1]["revenue"] == "120.00" and rows[1]["average_purchase_value"] == "60.00"
        assert (
            rows[2]["active_customers"] == 0 and rows[2]["partial_month"] is True
        )  # the current month: partial, not extrapolated
        assert max(rows) == 2  # never extended past the current month
        assert rows[0]["note"] == "Small cohort: read with care"

    def test_cohorts_are_grouped_by_first_purchase_month_not_by_later_ones(self, session, tenant_a, client_a):
        a = _customer(session, tenant_a, "A")
        self._buy(session, tenant_a, a, _months_ago(3))
        self._buy(session, tenant_a, a, _months_ago(1))
        session.commit()
        t = _get(
            client_a,
            "cohorts",
            preset="custom",
            date_from=_months_ago(3).replace(day=1).isoformat(),
            date_to=TODAY.isoformat(),
        )
        assert {r["cohort"] for r in t["rows"]} == {_months_ago(3).strftime("%Y-%m")}

    def test_walk_in_sales_are_in_no_cohort_and_no_data_gives_no_rows(self, session, tenant_a, client_a):
        make_quick_sale(session, tenant_a, "10.00", day=TODAY)
        session.commit()
        assert _get(client_a, "cohorts")["rows"] == []

    def test_definitions_are_documented_in_the_response(self, session, tenant_a, client_a):
        assert any("first-ever purchase" in n for n in _get(client_a, "cohorts")["notes"])

    def test_bad_month_counts_are_refused(self, session, tenant_a, client_a):
        assert client_a.get(f"{API}/cohorts", params={**P, "months": 99}).status_code == 422


class TestAccess:
    def test_needs_advanced_analytics_and_crm_analytics_permission(self, session, tenant_a, make_client):
        assert (
            client_with(make_client, tenant_a, ["ANALYTICS_ADVANCED"])
            .get(f"{API}/customers/overview")
            .status_code
            == 403
        )
        assert (
            client_with(make_client, tenant_a, ["CRM_ANALYTICS_VIEW"]).get(f"{API}/cohorts").status_code
            == 403
        )
        assert (
            client_with(make_client, tenant_a, ["ANALYTICS_ADVANCED", "CRM_ANALYTICS_VIEW"])
            .get(f"{API}/cohorts")
            .status_code
            == 200
        )

    def test_shops_are_isolated(self, session, tenant_a, tenant_b, client_b):
        c = _customer(session, tenant_a, "A")
        make_quick_sale(session, tenant_a, "100.00", day=TODAY, customer_id=c.id)
        session.commit()
        assert _get(client_b, "customers/list")["rows"] == [] and _get(client_b, "cohorts")["rows"] == []
