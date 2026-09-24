"""The KPI framework: formulas, availability (never a fake zero), comparison arithmetic, determinism and the rule that
only posted documents count."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.models.enums import FinancePaymentMethod, PaymentMethod, SaleStatus
from app.reporting import kpis
from app.reporting.filters import CompareMode, build_filters
from app.services import expense_service, khata_service
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import D, make_purchase, make_quick_sale, make_sale, make_sales_return

TODAY = today_in_shop_timezone()


def _filters(days=6, compare=True):
    return build_filters(
        TODAY,
        date_from=TODAY - timedelta(days=days),
        date_to=TODAY,
        compare=CompareMode.PREVIOUS_PERIOD if compare else CompareMode.NONE,
    )


def _kpi(session, tenant, key, filters=None):
    results, _ = kpis.compute(session, tenant.shop.id, filters or _filters(), TODAY, [key])
    return results[0]


def _value(session, tenant, key, **kw):
    return _kpi(session, tenant, key, **kw).current


class TestEveryKpiIsDefined:
    def test_each_definition_carries_the_facts_a_person_needs(self):
        for key, (d, _) in kpis.KPIS.items():
            assert d.key == key
            assert all(
                [d.name, d.description, d.formula, d.source, d.unit, d.limitations, d.permission, d.group]
            ), key
            assert d.unit in {"money", "count", "quantity", "percent", "ratio"}

    def test_the_required_kpis_exist(self):
        required = {
            "revenue",
            "orders",
            "units_sold",
            "average_transaction_value",
            "sales_growth",
            "cogs",
            "gross_profit",
            "gross_margin",
            "net_profit",
            "expense_ratio",
            "inventory_value",
            "stock_turnover",
            "fast_moving",
            "slow_moving",
            "dead_stock",
            "stock_out_count",
            "new_customers",
            "returning_customers",
            "repeat_purchase_rate",
            "average_customer_value",
            "inactive_customers",
            "receivables",
            "payables",
            "net_cash_flow",
            "expense_value",
            "online_orders",
            "return_rate",
            "promotion_usage",
            "loyalty_activity",
        }
        assert required <= set(kpis.KPIS)

    def test_an_unknown_kpi_is_refused(self, session, tenant_a):
        from app.services.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            kpis.compute(session, tenant_a.shop.id, _filters(), TODAY, ["nonsense"])


class TestSalesKpis:
    def test_revenue_orders_and_average_transaction_value(self, session, tenant_a):
        make_sale(session, tenant_a, "300.00", day=TODAY, cogs="100.00")
        make_quick_sale(session, tenant_a, "100.00", day=TODAY)
        session.commit()
        assert _value(session, tenant_a, "revenue").amount == D("400.00")
        assert _value(session, tenant_a, "orders").amount == 2
        assert _value(session, tenant_a, "average_transaction_value").amount == D("200.00")

    def test_returns_reduce_revenue_and_give_a_return_rate(self, session, tenant_a):
        sale = make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        make_sales_return(session, tenant_a, sale, "100.00", day=TODAY, cogs="60.00")
        session.commit()
        assert _value(session, tenant_a, "revenue").amount == D("900.00")
        assert _value(session, tenant_a, "return_rate").amount == D("10.00")

    def test_no_sales_is_insufficient_data_not_a_zero_average(self, session, tenant_a):
        v = _value(session, tenant_a, "average_transaction_value")
        assert v.amount is None and v.availability is kpis.Availability.INSUFFICIENT_DATA
        assert _value(session, tenant_a, "return_rate").amount is None

    def test_units_sold_counts_only_count_units_and_never_quick_sales(self, session, tenant_a):
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="50.00")
        make_quick_sale(session, tenant_a, "500.00", day=TODAY)
        session.commit()
        assert _value(session, tenant_a, "units_sold").amount == D(
            "1.000"
        )  # only the detailed line; the quick sale has none

    def test_drafts_and_voided_sales_never_count(self, session, tenant_a):
        draft = make_sale(session, tenant_a, "999.00", day=TODAY, cogs="1.00")
        draft.status = SaleStatus.VOID
        draft.void_reason = "x"
        session.commit()
        assert _value(session, tenant_a, "revenue").amount == D("0.00")
        assert _value(session, tenant_a, "orders").amount == 0


class TestProfitabilityNeverFabricated:
    def test_missing_cost_is_not_available_never_zero_profit(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "500.00", day=TODAY)
        make_sale(session, tenant_a, "200.00", day=TODAY, cogs=None)
        session.commit()
        for key in ("cogs", "gross_profit", "gross_margin", "net_profit"):
            v = _value(session, tenant_a, key)
            assert (
                v.amount is None
                and v.availability is kpis.Availability.NOT_AVAILABLE
                and v.reason == "Insufficient Cost Data"
            ), key
        assert _value(session, tenant_a, "revenue").amount == D("700.00")  # revenue is still fact

    def test_known_costs_give_exact_profit_margin_and_expense_ratio(self, session, tenant_a):
        ctx = context_for(tenant_a)
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        session.commit()
        cat = expense_service.create_category(session, ctx, name="Rent")
        e = expense_service.create_expense(
            session,
            ctx,
            {
                "expense_date": TODAY,
                "category_id": cat.id,
                "amount": D("100.00"),
                "payment_method": FinancePaymentMethod.CASH,
            },
        )
        expense_service.submit_expense(session, ctx, e.id)
        expense_service.post_expense(session, ctx, e.id)
        session.commit()
        assert _value(session, tenant_a, "gross_profit").amount == D("400.00")
        assert _value(session, tenant_a, "gross_margin").amount == D("40.00")
        assert _value(session, tenant_a, "net_profit").amount == D("300.00")
        assert _value(session, tenant_a, "expense_ratio").amount == D("10.00")
        assert _value(session, tenant_a, "expense_value").amount == D("100.00")

    def test_a_draft_expense_is_not_an_expense(self, session, tenant_a):
        ctx = context_for(tenant_a)
        cat = expense_service.create_category(session, ctx, name="Rent")
        expense_service.create_expense(
            session,
            ctx,
            {
                "expense_date": TODAY,
                "category_id": cat.id,
                "amount": D("100.00"),
                "payment_method": FinancePaymentMethod.CASH,
            },
        )
        session.commit()
        assert _value(session, tenant_a, "expense_value").amount == D("0.00")


class TestComparison:
    def test_absolute_and_percentage_change_are_exact(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "300.00", day=TODAY)
        make_quick_sale(session, tenant_a, "200.00", day=TODAY - timedelta(days=8))
        session.commit()
        r = _kpi(session, tenant_a, "revenue")
        assert (r.current.amount, r.previous.amount) == (D("300.00"), D("200.00"))
        assert (r.change.absolute, r.change.percent) == (D("100.00"), D("50.0"))

    def test_a_decrease_is_negative(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "100.00", day=TODAY)
        make_quick_sale(session, tenant_a, "400.00", day=TODAY - timedelta(days=8))
        session.commit()
        assert _kpi(session, tenant_a, "revenue").change.percent == D("-75.0")

    def test_a_zero_base_gives_insufficient_comparison_data_not_infinite_growth(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "300.00", day=TODAY)
        session.commit()
        r = _kpi(session, tenant_a, "revenue")
        assert (
            r.change.percent is None
            and r.change.note == "Insufficient comparison data"
            and r.change.absolute == D("300.00")
        )
        g = _kpi(session, tenant_a, "sales_growth")
        assert g.current.amount is None and g.current.reason == "Insufficient comparison data"

    def test_sales_growth_is_the_revenue_change(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "150.00", day=TODAY)
        make_quick_sale(session, tenant_a, "100.00", day=TODAY - timedelta(days=8))
        session.commit()
        assert _kpi(session, tenant_a, "sales_growth").current.amount == D("50.0")

    def test_percentage_kpis_change_in_points_and_unknown_sides_are_insufficient(self, session, tenant_a):
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")  # margin 40
        make_sale(session, tenant_a, "1000.00", day=TODAY - timedelta(days=8), cogs="800.00")  # margin 20
        session.commit()
        m = _kpi(session, tenant_a, "gross_margin")
        assert m.change.absolute == D("20.00") and m.change.percent is None
        make_sale(session, tenant_a, "10.00", day=TODAY - timedelta(days=9), cogs=None)
        session.commit()
        m2 = _kpi(session, tenant_a, "gross_profit")
        assert (
            m2.change.absolute is None and m2.change.note == "Insufficient comparison data"
        )  # the previous side has an unknown cost

    def test_no_comparison_requested_means_no_previous(self, session, tenant_a):
        r = _kpi(session, tenant_a, "revenue", filters=_filters(compare=False))
        assert r.previous is None and r.change is None and r.comparison_period is None

    def test_as_of_only_kpis_never_pretend_to_compare(self, session, tenant_a):
        r = _kpi(session, tenant_a, "inventory_value")
        assert r.definition.as_of_only and r.change.absolute is None and "current position" in r.change.note


class TestCustomerKpis:
    def test_new_and_returning_customers_and_average_value(self, session, tenant_a):
        old = factories.make_customer(session, tenant_a.shop, name="Old")
        new = factories.make_customer(session, tenant_a.shop, name="New")
        session.commit()
        make_quick_sale(session, tenant_a, "50.00", day=TODAY - timedelta(days=60), customer_id=old.id)
        make_quick_sale(session, tenant_a, "100.00", day=TODAY, customer_id=old.id)
        make_quick_sale(session, tenant_a, "300.00", day=TODAY, customer_id=new.id)
        make_quick_sale(session, tenant_a, "999.00", day=TODAY)  # no customer: never counted as a customer
        session.commit()
        assert _value(session, tenant_a, "new_customers").amount == 1
        assert _value(session, tenant_a, "returning_customers").amount == 1
        assert _value(session, tenant_a, "average_customer_value").amount == D("200.00")

    def test_no_identified_purchases_is_insufficient_not_zero_value(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "10.00", day=TODAY)
        session.commit()
        assert (
            _value(session, tenant_a, "average_customer_value").availability
            is kpis.Availability.INSUFFICIENT_DATA
        )


class TestFinanceAndOperations:
    def test_receivables_come_from_khata_and_payables_from_purchases(self, session, tenant_a):
        ctx = context_for(tenant_a)
        c = factories.make_customer(session, tenant_a.shop)
        s = factories.make_supplier(session, tenant_a.shop)
        session.commit()
        khata_service.create_opening_balance(session, ctx, c.id, D("250.00"))
        make_purchase(session, tenant_a, s, "400.00", day=TODAY)
        session.commit()
        assert _value(session, tenant_a, "receivables").amount == D("250.00")
        assert _value(session, tenant_a, "payables").amount == D("400.00")

    def test_net_cash_flow_counts_only_money_that_moved(self, session, tenant_a):
        c = factories.make_customer(session, tenant_a.shop)
        session.commit()
        make_sale(
            session,
            tenant_a,
            "500.00",
            day=TODAY,
            paid="200.00",
            customer_id=c.id,
            method=PaymentMethod.CASH,
            cogs="1.00",
        )
        session.commit()
        assert _value(session, tenant_a, "net_cash_flow").amount == D("200.00")

    def test_online_orders_are_not_available_because_none_exist(self, session, tenant_a):
        v = _value(session, tenant_a, "online_orders")
        assert (
            v.amount is None
            and v.availability is kpis.Availability.NOT_AVAILABLE
            and "no online orders" in v.reason
        )

    def test_loyalty_activity_and_promotion_usage(self, session, tenant_a):
        from app.services import loyalty_service

        ctx = context_for(tenant_a)
        c = factories.make_customer(session, tenant_a.shop)
        loyalty_service.configure_program(
            session, ctx, is_active=True, points_per_amount=D("1"), redemption_value=D("1")
        )
        session.commit()
        loyalty_service.grant_reward(
            session,
            ctx,
            customer_id=c.id,
            points=25,
            reference_type="T",
            reference_id=1,
            entry_date=TODAY,
            note="x",
        )
        session.commit()
        assert _value(session, tenant_a, "loyalty_activity").amount == 25
        assert (
            _value(session, tenant_a, "promotion_usage").availability is kpis.Availability.INSUFFICIENT_DATA
        )


class TestInventoryKpis:
    def test_stock_turnover_from_the_ledger_and_insufficient_without_stock(self, session, tenant_a):
        assert _value(session, tenant_a, "stock_turnover").availability is kpis.Availability.INSUFFICIENT_DATA
        assert _value(session, tenant_a, "inventory_value").amount == D(
            "0.00"
        )  # no stock at all: a true zero

    def test_stock_with_no_known_cost_is_not_available_never_a_zero_value(self, session, tenant_a):
        from app.services import inventory_service

        p = factories.make_product(session, tenant_a.shop, tenant_a.category, name="Mystery", sku="SKU-M")
        session.commit()
        inventory_service.record_opening_stock(
            session, context_for(tenant_a), product_id=p.id, quantity=Decimal("5")
        )
        session.commit()
        v = _value(session, tenant_a, "inventory_value")
        assert v.amount is None and v.availability is kpis.Availability.NOT_AVAILABLE

    def test_stock_out_and_inventory_value_follow_the_inventory_service(self, session, tenant_a):
        from app.services import inventory_service

        ctx = context_for(tenant_a)
        p = factories.make_product(session, tenant_a.shop, tenant_a.category, name="Rice", sku="SKU-R")
        session.commit()
        inventory_service.record_opening_stock(
            session, ctx, product_id=p.id, quantity=Decimal("10"), unit_cost=D("20.00")
        )
        session.commit()
        assert _value(session, tenant_a, "inventory_value").amount == D("200.00")
        assert _value(session, tenant_a, "stock_out_count").amount == 0


class TestDeterminismAndPermissions:
    def test_the_same_data_always_gives_the_same_kpis(self, session, tenant_a):
        make_sale(session, tenant_a, "333.33", day=TODAY, cogs="111.11")
        session.commit()
        a, _ = kpis.compute(session, tenant_a.shop.id, _filters(), TODAY)
        b, _ = kpis.compute(session, tenant_a.shop.id, _filters(), TODAY)
        assert [(r.definition.key, r.current, r.previous, r.change) for r in a] == [
            (r.definition.key, r.current, r.previous, r.change) for r in b
        ]

    def test_kpis_the_caller_may_not_see_are_hidden_not_zeroed(self, session, tenant_a):
        results, hidden = kpis.compute(session, tenant_a.shop.id, _filters(), TODAY, granted={"REPORT_VIEW"})
        keys = {r.definition.key for r in results}
        assert "revenue" in keys and "gross_profit" not in keys
        assert {"gross_profit", "receivables", "inventory_value", "new_customers"} <= set(hidden)

    def test_money_results_are_exact_decimals(self, session, tenant_a):
        make_sale(session, tenant_a, "0.10", day=TODAY, cogs="0.03")
        make_sale(session, tenant_a, "0.20", day=TODAY, cogs="0.06")
        session.commit()
        assert _value(session, tenant_a, "revenue").amount == D("0.30")
        assert isinstance(_value(session, tenant_a, "gross_profit").amount, Decimal)

    def test_another_shops_data_never_leaks(self, session, tenant_a, tenant_b):
        make_quick_sale(session, tenant_b, "999.00", day=TODAY)
        session.commit()
        assert _value(session, tenant_a, "revenue").amount == D("0.00")

    def test_date_boundaries_are_inclusive_on_both_ends(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "10.00", day=TODAY - timedelta(days=6))  # first day
        make_quick_sale(session, tenant_a, "20.00", day=TODAY)  # last day
        make_quick_sale(session, tenant_a, "40.00", day=TODAY - timedelta(days=7))  # one day before
        make_quick_sale(session, tenant_a, "80.00", day=TODAY + timedelta(days=1))  # one day after
        session.commit()
        assert _value(session, tenant_a, "revenue").amount == D("30.00")
