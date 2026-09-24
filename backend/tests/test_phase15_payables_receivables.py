"""Accounts payable and receivable: built from purchases, returns, payments and khata; ageing that says how it
is measured and never invents a due date."""

from datetime import timedelta

from app.models.enums import (
    FinanceEventType,
    FinancePaymentMethod,
    FlowDirection,
    PaymentMethod,
    SupplierCreditMode,
)
from app.services import finance_ledger_service, khata_service, payables_service, receivables_service
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import D, make_purchase, make_purchase_return

TODAY = today_in_shop_timezone()


def _pay(session, ctx, supplier, amount, day=TODAY):
    finance_ledger_service.record_entry(
        session,
        ctx,
        event_type=FinanceEventType.SUPPLIER_PAYMENT,
        direction=FlowDirection.OUT,
        amount=D(amount),
        payment_method=FinancePaymentMethod.UPI,
        entry_date=day,
        supplier_id=supplier.id,
    )
    session.commit()


class TestPayables:
    def test_balance_is_purchases_less_returns_less_payments(self, session, tenant_a):
        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        p = make_purchase(session, tenant_a, supplier, "1000.00", day=TODAY - timedelta(days=5))
        make_purchase_return(session, tenant_a, p, "100.00", day=TODAY - timedelta(days=4))
        session.commit()
        _pay(session, ctx, supplier, "300.00")

        r = payables_service.compute(session, tenant_a.shop.id, TODAY)

        row = r.suppliers[0]
        assert (row.total_purchases, row.purchase_returns, row.payments_made, row.balance) == (
            D("1000.00"),
            D("100.00"),
            D("300.00"),
            D("600.00"),
        )
        assert r.total_payable == D("600.00")

    def test_a_cash_refund_on_a_return_is_added_back(self, session, tenant_a):
        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        p = make_purchase(session, tenant_a, supplier, "1000.00", day=TODAY)
        session.commit()
        _pay(session, ctx, supplier, "1000.00")
        make_purchase_return(session, tenant_a, p, "200.00", day=TODAY, mode=SupplierCreditMode.CASH)
        session.commit()

        assert payables_service.compute(session, tenant_a.shop.id, TODAY).suppliers[0].balance == D("0.00")

    def test_overpaying_shows_an_advance_not_a_negative_payable(self, session, tenant_a):
        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        make_purchase(session, tenant_a, supplier, "100.00", day=TODAY)
        session.commit()
        _pay(session, ctx, supplier, "150.00")

        r = payables_service.compute(session, tenant_a.shop.id, TODAY)

        assert (
            r.suppliers[0].advance == D("50.00")
            and r.total_payable == D("0.00")
            and r.total_advances == D("50.00")
        )

    def test_a_reversed_supplier_payment_restores_the_payable(self, session, tenant_a):
        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        make_purchase(session, tenant_a, supplier, "500.00", day=TODAY)
        session.commit()
        _pay(session, ctx, supplier, "500.00")
        entry = finance_ledger_service.list_ledger(session, tenant_a.shop.id, TODAY, TODAY)[0]
        finance_ledger_service.reverse_entry(session, ctx, entry.source_id, "Bounced", today=TODAY)
        session.commit()

        assert payables_service.compute(session, tenant_a.shop.id, TODAY).total_payable == D("500.00")

    def test_ageing_applies_payments_to_the_oldest_purchase_first(self, session, tenant_a):
        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        make_purchase(session, tenant_a, supplier, "100.00", day=TODAY - timedelta(days=100))  # 90+
        make_purchase(session, tenant_a, supplier, "100.00", day=TODAY - timedelta(days=45))  # 31-60
        make_purchase(session, tenant_a, supplier, "100.00", day=TODAY - timedelta(days=10))  # 0-30
        session.commit()
        _pay(session, ctx, supplier, "150.00")

        r = payables_service.compute(session, tenant_a.shop.id, TODAY)

        assert r.aging == {"0-30": D("100.00"), "31-60": D("50.00"), "61-90": D("0.00"), "90+": D("0.00")}

    def test_the_report_states_that_no_payment_terms_are_assumed(self, session, tenant_a):
        r = payables_service.compute(session, tenant_a.shop.id, TODAY)
        assert "no payment terms" in r.methodology.lower()

    def test_future_purchases_are_not_counted_as_of_today(self, session, tenant_a):
        supplier = factories.make_supplier(session, tenant_a.shop)
        make_purchase(session, tenant_a, supplier, "100.00", day=TODAY + timedelta(days=3))
        session.commit()
        assert payables_service.compute(session, tenant_a.shop.id, TODAY).total_payable == D("0.00")

    def test_suppliers_are_kept_apart(self, session, tenant_a):
        a = factories.make_supplier(session, tenant_a.shop, "A")
        b = factories.make_supplier(session, tenant_a.shop, "B")
        make_purchase(session, tenant_a, a, "100.00", day=TODAY)
        make_purchase(session, tenant_a, b, "40.00", day=TODAY)
        session.commit()
        r = payables_service.compute(session, tenant_a.shop.id, TODAY, supplier_id=b.id)
        assert [(s.name, s.balance) for s in r.suppliers] == [("B", D("40.00"))]


class TestReceivables:
    def test_balances_come_from_khata_and_match_it_exactly(self, session, tenant_a):
        ctx = context_for(tenant_a)
        c1 = factories.make_customer(session, tenant_a.shop, "One")
        c2 = factories.make_customer(session, tenant_a.shop, "Two")
        session.commit()
        khata_service.create_opening_balance(session, ctx, c1.id, D("500.00"))
        khata_service.create_opening_balance(session, ctx, c2.id, D("200.00"))
        khata_service.record_payment(
            session, ctx, c2.id, D("50.00"), entry_date=TODAY, payment_method=PaymentMethod.CASH
        )
        session.commit()

        r = receivables_service.compute(session, tenant_a.shop.id, TODAY)

        assert r.total_receivables == D("650.00")
        for row in r.customers:
            assert row.balance == khata_service.get_customer_balance(
                session, tenant_a.shop.id, row.customer_id
            )

    def test_an_advance_is_reported_separately(self, session, tenant_a):
        ctx = context_for(tenant_a)
        c = factories.make_customer(session, tenant_a.shop)
        session.commit()
        khata_service.record_payment(
            session, ctx, c.id, D("75.00"), entry_date=TODAY, payment_method=PaymentMethod.CASH
        )
        session.commit()
        r = receivables_service.compute(session, tenant_a.shop.id, TODAY)
        assert r.total_receivables == D("0.00") and r.total_advances == D("75.00")

    def test_ageing_is_fifo_from_each_charges_own_date_and_labelled(self, session, tenant_a):
        ctx = context_for(tenant_a)
        c = factories.make_customer(session, tenant_a.shop)
        session.commit()
        khata_service.create_opening_balance(session, ctx, c.id, D("100.00"))  # dated today by default
        session.commit()
        events = [
            khata_service.AgingEvent(
                c.id,
                TODAY - timedelta(days=95),
                D("100.00"),
                khata_service.CustomerLedgerEntryType.CREDIT_SALE,
                1,
            ),
            khata_service.AgingEvent(
                c.id,
                TODAY - timedelta(days=20),
                D("100.00"),
                khata_service.CustomerLedgerEntryType.CREDIT_SALE,
                2,
            ),
            khata_service.AgingEvent(
                c.id,
                TODAY - timedelta(days=5),
                D("-120.00"),
                khata_service.CustomerLedgerEntryType.PAYMENT,
                3,
            ),
        ]

        aged = receivables_service.age_ledger(events, TODAY)[c.id]

        assert aged[0] == {"0-30": D("80.00"), "31-60": D("0.00"), "61-90": D("0.00"), "90+": D("0.00")}
        r = receivables_service.compute(session, tenant_a.shop.id, TODAY)
        assert "no due dates" in r.methodology.lower() and r.overdue == "Not Available"

    def test_period_flows_credit_sales_payments_and_return_credits(self, session, tenant_a):
        ctx = context_for(tenant_a)
        c = factories.make_customer(session, tenant_a.shop)
        session.commit()
        khata_service.create_opening_balance(session, ctx, c.id, D("100.00"))
        khata_service.record_payment(
            session, ctx, c.id, D("30.00"), entry_date=TODAY, payment_method=PaymentMethod.UPI
        )
        session.commit()
        r = receivables_service.compute(
            session, tenant_a.shop.id, TODAY, period_start=TODAY, period_end=TODAY
        )
        assert r.payments_received == D("30.00")

    def test_khata_is_not_duplicated_no_second_balance_table_exists(self, session, tenant_a):
        from sqlalchemy import inspect

        tables = set(inspect(session.get_bind()).get_table_names())
        assert not {t for t in tables if "receivable" in t or "payable" in t}
