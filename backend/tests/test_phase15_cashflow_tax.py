"""Cash flow from real movements, and the tax reporting foundation (configurable, never assumed)."""

from datetime import timedelta

import pytest

from app.models.enums import (
    CashFlowClass,
    FinanceEventType,
    FinancePaymentMethod,
    FlowDirection,
    PaymentMethod,
    RefundMode,
    TaxType,
)
from app.services import cashflow_service, expense_service, finance_ledger_service, tax_service
from app.services.errors import ConflictError, InvalidInputError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import D, make_purchase, make_quick_sale, make_sale, make_sales_return

TODAY = today_in_shop_timezone()


def _flow(session, tenant, start=None, end=None):
    return cashflow_service.compute(session, tenant.shop.id, start or TODAY, end or TODAY)


class TestCashFlow:
    def test_inflows_outflows_and_net_from_settled_money_only(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        supplier = factories.make_supplier(session, tenant_a.shop)
        session.commit()
        sale = make_sale(
            session, tenant_a, "1000.00", day=TODAY, paid="400.00", customer_id=customer.id
        )  # 400 in
        make_quick_sale(session, tenant_a, "100.00", day=TODAY, method=PaymentMethod.UPI)  # 100 in
        make_sales_return(session, tenant_a, sale, "50.00", day=TODAY, mode=RefundMode.CASH)  # 50 out
        make_purchase(session, tenant_a, supplier, "700.00", day=TODAY)  # unpaid: nothing moved
        session.commit()
        finance_ledger_service.record_entry(
            session,
            ctx,
            event_type=FinanceEventType.SUPPLIER_PAYMENT,
            direction=FlowDirection.OUT,
            amount=D("200.00"),
            payment_method=FinancePaymentMethod.BANK_TRANSFER,
            entry_date=TODAY,
            supplier_id=supplier.id,
        )

        r = _flow(session, tenant_a)

        assert (r.inflow, r.outflow, r.net) == (D("500.00"), D("250.00"), D("250.00"))
        assert r.by_method["CASH"].net == D("350.00") and r.by_method["UPI"].net == D("100.00")
        assert r.by_method["BANK_TRANSFER"].net == D("-200.00")

    def test_classification_is_only_where_justified(self, session, tenant_a):
        ctx = context_for(tenant_a)
        make_quick_sale(session, tenant_a, "100.00", day=TODAY)
        session.commit()
        for event, direction in (
            (FinanceEventType.OWNER_CAPITAL, FlowDirection.IN),
            (FinanceEventType.OWNER_WITHDRAWAL, FlowDirection.OUT),
        ):
            finance_ledger_service.record_entry(
                session,
                ctx,
                event_type=event,
                direction=direction,
                amount=D("30.00"),
                payment_method=FinancePaymentMethod.CASH,
                entry_date=TODAY,
            )
        finance_ledger_service.record_adjustment(
            session,
            ctx,
            direction=FlowDirection.IN,
            amount=D("7.00"),
            payment_method=FinancePaymentMethod.CASH,
            entry_date=TODAY,
            note="Found",
        )
        cat = expense_service.create_category(
            session, ctx, name="New shelving", cash_flow_class=CashFlowClass.INVESTING
        )
        e = expense_service.create_expense(
            session,
            ctx,
            {
                "expense_date": TODAY,
                "category_id": cat.id,
                "amount": D("90.00"),
                "payment_method": FinancePaymentMethod.CASH,
            },
        )
        expense_service.submit_expense(session, ctx, e.id)
        expense_service.post_expense(session, ctx, e.id)
        session.commit()

        r = _flow(session, tenant_a)

        assert r.by_class["OPERATING"].inflow == D("100.00")
        assert (r.by_class["FINANCING"].inflow, r.by_class["FINANCING"].outflow) == (D("30.00"), D("30.00"))
        assert r.by_class["INVESTING"].outflow == D("90.00")
        assert r.by_class["UNCLASSIFIED"].inflow == D("7.00") and r.unclassified_amount == D("7.00")
        assert any("UNCLASSIFIED" in n for n in r.notes)

    def test_a_reversal_nets_a_voided_expense_to_zero(self, session, tenant_a):
        ctx = context_for(tenant_a)
        cat = expense_service.create_category(session, ctx, name="Rent")
        e = expense_service.create_expense(
            session,
            ctx,
            {
                "expense_date": TODAY,
                "category_id": cat.id,
                "amount": D("80.00"),
                "payment_method": FinancePaymentMethod.CASH,
            },
        )
        expense_service.submit_expense(session, ctx, e.id)
        expense_service.post_expense(session, ctx, e.id)
        expense_service.void_expense(session, ctx, e.id, "Mistake", today=TODAY)
        session.commit()
        assert _flow(session, tenant_a).net == D("0.00")

    def test_trend_buckets_by_day_week_month(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "10.00", day=TODAY)
        make_quick_sale(session, tenant_a, "20.00", day=TODAY - timedelta(days=1))
        session.commit()
        days = cashflow_service.trend(session, tenant_a.shop.id, TODAY - timedelta(days=1), TODAY, "day")
        assert [p.net for p in days] == [D("20.00"), D("10.00")]
        month = cashflow_service.trend(session, tenant_a.shop.id, TODAY.replace(day=1), TODAY, "month")
        assert len(month) == 1 and month[0].net >= D("0.00")

    def test_no_activity_is_a_plain_zero_report(self, session, tenant_a):
        r = _flow(session, tenant_a)
        assert (r.inflow, r.outflow, r.net) == (D("0.00"),) * 3

    def test_api_and_tenant_isolation(self, session, tenant_a, tenant_b, client_a, client_b):
        make_quick_sale(session, tenant_a, "10.00", day=TODAY)
        session.commit()
        assert (
            client_a.get(
                "/api/v1/finance/cash-flow",
                params={"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()},
            ).json()["net"]
            == "10.00"
        )
        assert (
            client_b.get(
                "/api/v1/finance/cash-flow",
                params={"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()},
            ).json()["net"]
            == "0.00"
        )


class TestTaxConfiguration:
    def test_nothing_is_calculated_until_tax_is_configured(self, session, tenant_a):
        make_sale(session, tenant_a, "118.00", day=TODAY, cogs="50.00")
        session.commit()
        s = tax_service.summary(session, tenant_a.shop.id, TODAY, TODAY)
        assert s.status == "NOT_CONFIGURED" and s.tax_collected is None and s.net_tax is None

    def test_rates_are_configurable_validated_and_never_deleted(self, session, tenant_a):
        ctx = context_for(tenant_a)
        r = tax_service.create_rate(session, ctx, name="Standard", rate_percent=D("18"))
        assert r.rate_bp == 1800
        for bad in (D("100.01"), D("-1"), D("1.234"), 5.5):
            with pytest.raises(InvalidInputError):
                tax_service.create_rate(session, ctx, name=f"x{bad}", rate_percent=bad)
        with pytest.raises(ConflictError):
            tax_service.create_rate(session, ctx, name="Another default", rate_percent=D("5"))
        assert tax_service.update_rate(session, ctx, r.id, is_active=False).is_active is False

    def test_no_country_rates_are_built_in(self, session, tenant_a):
        assert tax_service.list_rates(session, tenant_a.shop.id) == []
        assert tax_service.get_settings(session, tenant_a.shop.id) is None


class TestTaxSummary:
    def _configure(self, session, ctx, inclusive=True, rate="18"):
        tax_service.configure(
            session,
            ctx,
            tax_type=TaxType.GST,
            registration_number="27ABCDE1234F1Z5",
            location_state="MH",
            prices_include_tax=inclusive,
        )
        tax_service.create_rate(session, ctx, name="Standard", rate_percent=D(rate))
        session.commit()

    def test_inclusive_prices_split_the_line_into_taxable_and_tax(self, session, tenant_a):
        ctx = context_for(tenant_a)
        self._configure(session, ctx)
        make_sale(session, tenant_a, "118.00", day=TODAY, cogs="50.00")
        session.commit()
        s = tax_service.summary(session, tenant_a.shop.id, TODAY, TODAY)
        assert (s.sales.taxable_amount, s.sales.tax_amount) == (D("100.00"), D("18.00"))
        assert s.tax_collected == D("18.00") and s.status == "CONFIGURED" and s.registration_number

    def test_exclusive_prices_add_tax_on_top(self, session, tenant_a):
        ctx = context_for(tenant_a)
        self._configure(session, ctx, inclusive=False)
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="50.00")
        session.commit()
        s = tax_service.summary(session, tenant_a.shop.id, TODAY, TODAY)
        assert (s.sales.taxable_amount, s.sales.tax_amount) == (D("100.00"), D("18.00"))

    def test_returns_reduce_tax_collected_and_purchases_give_tax_paid(self, session, tenant_a):
        ctx = context_for(tenant_a)
        self._configure(session, ctx)
        supplier = factories.make_supplier(session, tenant_a.shop)
        sale = make_sale(session, tenant_a, "236.00", day=TODAY, cogs="100.00")
        make_sales_return(session, tenant_a, sale, "118.00", day=TODAY, cogs="50.00")
        session.commit()
        from app.models import PurchaseItem

        purchase = make_purchase(session, tenant_a, supplier, "59.00", day=TODAY)
        product = factories.make_product(
            session, tenant_a.shop, tenant_a.category, name="Bought", sku="SKU-BUY"
        )
        session.add(
            PurchaseItem(
                shop_id=tenant_a.shop.id,
                purchase_id=purchase.id,
                product_id=product.id,
                unit_id=product.unit_id,
                quantity=D(1),
                unit_cost=D("59.00"),
                line_total=D("59.00"),
            )
        )
        session.commit()
        s = tax_service.summary(session, tenant_a.shop.id, TODAY, TODAY)
        assert s.sales.tax_amount == D("36.00") and s.sales_returns.tax_amount == D("18.00")
        assert s.tax_collected == D("18.00") and s.tax_paid == D("9.00") and s.net_tax == D("9.00")

    def test_a_category_rate_overrides_the_default_and_unrated_lines_stay_untaxed(self, session, tenant_a):
        ctx = context_for(tenant_a)
        tax_service.configure(
            session,
            ctx,
            tax_type=TaxType.VAT,
            registration_number=None,
            location_state=None,
            prices_include_tax=False,
        )
        tax_service.create_rate(
            session, ctx, name="Special", rate_percent=D("5"), category_id=tenant_a.category.id
        )
        session.commit()
        make_sale(session, tenant_a, "200.00", day=TODAY, cogs="50.00")
        session.commit()
        s = tax_service.summary(session, tenant_a.shop.id, TODAY, TODAY)
        assert s.sales.tax_amount == D("10.00") and s.sales.unclassified_amount == D("0.00")
        tax_service.update_rate(
            session, ctx, tax_service.list_rates(session, tenant_a.shop.id)[0].id, is_active=False
        )
        session.commit()
        s = tax_service.summary(session, tenant_a.shop.id, TODAY, TODAY)
        assert s.sales.tax_amount == D("0.00") and s.sales.unclassified_amount == D(
            "200.00"
        )  # never an assumed rate

    def test_quick_sales_are_not_available_for_tax_never_guessed(self, session, tenant_a):
        self._configure(session, context_for(tenant_a))
        make_quick_sale(session, tenant_a, "500.00", day=TODAY)
        session.commit()
        s = tax_service.summary(session, tenant_a.shop.id, TODAY, TODAY)
        assert s.quick_sales_amount == D("500.00") and s.sales.tax_amount == D("0.00")
        assert any("Quick Sales" in n for n in s.notes)

    def test_a_bill_discount_is_spread_so_the_base_matches_what_was_paid(self, session, tenant_a):
        from app.models import SaleItem

        ctx = context_for(tenant_a)
        self._configure(session, ctx, inclusive=True)
        sale = make_sale(session, tenant_a, "100.00", day=TODAY, cogs="10.00")
        sale.subtotal = D("118.00")
        sale.discount = D("18.00")
        sale.total_amount = D("100.00")
        item = session.query(SaleItem).filter_by(sale_id=sale.id).one()
        item.line_total = D("118.00")
        session.commit()
        s = tax_service.summary(session, tenant_a.shop.id, TODAY, TODAY)
        assert s.sales.buckets[0].gross_amount == D("100.00")  # what the customer really paid

    def test_the_report_never_claims_compliance(self, session, tenant_a):
        s = tax_service.summary(session, tenant_a.shop.id, TODAY, TODAY)
        assert "not a tax return" in s.disclaimer and "compliance" in s.disclaimer

    def test_api_configuration_and_isolation(self, session, tenant_a, tenant_b, client_a, client_b):
        assert client_a.get("/api/v1/finance/tax/settings").json() is None
        put = client_a.put(
            "/api/v1/finance/tax/settings", json={"tax_type": "GST", "prices_include_tax": True}
        )
        assert put.status_code == 200
        rate = client_a.post("/api/v1/finance/tax/rates", json={"name": "Std", "rate_percent": "12.5"})
        assert (
            rate.status_code == 201
            and rate.json()["rate_bp"] == 1250
            and rate.json()["rate_percent"] == "12.5"
        )
        assert client_a.get("/api/v1/finance/tax/summary").json()["status"] == "CONFIGURED"
        assert client_b.get("/api/v1/finance/tax/summary").json()["status"] == "NOT_CONFIGURED"
        assert client_b.get("/api/v1/finance/tax/rates").json() == []
