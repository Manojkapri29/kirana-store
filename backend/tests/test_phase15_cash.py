"""Cash management: expected closing cash from real cash movements, physical counts, explicit adjustments."""

from datetime import timedelta

import pytest

from app.models.enums import FinanceEventType, FinancePaymentMethod, FlowDirection, PaymentMethod, RefundMode
from app.services import (
    cash_service,
    expense_service,
    finance_ledger_service,
    finance_period_service,
    finance_settings_service,
    khata_service,
)
from app.services.errors import ConflictError, InvalidInputError
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import D, make_purchase, make_quick_sale, make_sale, make_sales_return

TODAY = today_in_shop_timezone()
YESTERDAY = TODAY - timedelta(days=1)


def _count(session, ctx, day, actual, reason=None):
    c = cash_service.record_count(
        session, ctx, count_date=day, actual_cash=D(actual), reason=reason, today=TODAY
    )
    session.commit()
    return c


def _expense(session, ctx, amount, method=FinancePaymentMethod.CASH, day=TODAY):
    cat = expense_service.create_category(session, ctx, name=f"C{amount}{method}{day}")
    e = expense_service.create_expense(
        session,
        ctx,
        {"expense_date": day, "category_id": cat.id, "amount": D(amount), "payment_method": method},
    )
    expense_service.submit_expense(session, ctx, e.id)
    expense_service.post_expense(session, ctx, e.id)
    session.commit()
    return e


class TestOpeningCash:
    def test_with_no_earlier_count_opening_and_expected_are_not_available_never_zero(self, session, tenant_a):
        make_sale(session, tenant_a, "100.00", day=TODAY)
        session.commit()

        s = cash_service.daily_summary(session, tenant_a.shop.id, TODAY)

        assert s.opening_cash is None and s.expected_closing is None
        assert s.lines["cash_sales"] == D("100.00")  # the movements are still shown

    def test_opening_is_the_last_count_rolled_forward_by_cash_movements(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _count(session, ctx, YESTERDAY - timedelta(days=1), "1000.00")
        make_sale(session, tenant_a, "300.00", day=YESTERDAY)
        session.commit()

        s = cash_service.daily_summary(session, tenant_a.shop.id, TODAY)

        assert s.opening_cash == D("1300.00")


class TestExpectedClosing:
    def test_the_full_formula(self, session, tenant_a, tenant_b):
        ctx = context_for(tenant_a)
        supplier = factories.make_supplier(session, tenant_a.shop)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        _count(session, ctx, YESTERDAY, "1000.00")
        sale = make_sale(session, tenant_a, "500.00", day=TODAY)  # +500 cash
        make_quick_sale(session, tenant_a, "200.00", day=TODAY)  # +200 cash
        make_quick_sale(session, tenant_a, "90.00", day=TODAY, method=PaymentMethod.UPI)  # UPI: not cash
        make_sale(
            session, tenant_a, "400.00", day=TODAY, paid="100.00", customer_id=customer.id
        )  # +100 cash, 300 credit
        make_sales_return(session, tenant_a, sale, "50.00", day=TODAY, mode=RefundMode.CASH)  # -50
        make_purchase(
            session, tenant_a, supplier, "600.00", day=TODAY, paid="150.00", method=PaymentMethod.CASH
        )  # -150
        session.commit()
        khata_service.create_opening_balance(session, ctx, customer.id, D("300.00"))
        khata_service.record_payment(
            session, ctx, customer.id, D("80.00"), entry_date=TODAY, payment_method=PaymentMethod.CASH
        )  # +80
        _expense(session, ctx, "70.00")  # -70
        finance_ledger_service.record_entry(
            session,
            ctx,
            event_type=FinanceEventType.SUPPLIER_PAYMENT,
            direction=FlowDirection.OUT,
            amount=D("60.00"),
            payment_method=FinancePaymentMethod.CASH,
            entry_date=TODAY,
            supplier_id=supplier.id,
        )  # -60
        finance_ledger_service.record_entry(
            session,
            ctx,
            event_type=FinanceEventType.OTHER_INCOME,
            direction=FlowDirection.IN,
            amount=D("25.00"),
            payment_method=FinancePaymentMethod.CASH,
            entry_date=TODAY,
        )  # +25
        finance_ledger_service.record_adjustment(
            session,
            ctx,
            direction=FlowDirection.OUT,
            amount=D("10.00"),
            payment_method=FinancePaymentMethod.CASH,
            entry_date=TODAY,
            note="Coins found short",
        )  # -10
        session.commit()

        s = cash_service.daily_summary(session, tenant_a.shop.id, TODAY)

        assert s.opening_cash == D("1000.00")
        assert s.lines["cash_sales"] == D("800.00")  # 500 + 200 + 100
        assert s.lines["customer_payments"] == D("80.00")
        assert s.lines["refunds"] == D("-50.00")
        assert s.lines["cash_purchases"] == D("-150.00")
        assert s.lines["expenses"] == D("-70.00")
        assert s.lines["supplier_payments"] == D("-60.00")
        assert s.lines["other_cash_income"] == D("25.00")
        assert s.lines["adjustments"] == D("-10.00")
        assert s.expected_closing == D("1565.00")  # 1000 + 800 + 80 + 25 - 50 - 150 - 70 - 60 - 10

    def test_a_credit_sale_puts_nothing_in_the_drawer(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        _count(session, ctx, YESTERDAY, "100.00")
        make_sale(session, tenant_a, "900.00", day=TODAY, paid="0.00", customer_id=customer.id)
        session.commit()

        assert cash_service.daily_summary(session, tenant_a.shop.id, TODAY).expected_closing == D("100.00")

    def test_a_voided_expense_returns_the_cash(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _count(session, ctx, YESTERDAY, "500.00")
        e = _expense(session, ctx, "120.00")
        expense_service.void_expense(session, ctx, e.id, "Mistake", today=TODAY)
        session.commit()
        assert cash_service.daily_summary(session, tenant_a.shop.id, TODAY).expected_closing == D("500.00")

    def test_money_received_with_no_method_is_not_assumed_to_be_cash(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        session.commit()
        _count(session, ctx, YESTERDAY, "0.00")
        khata_service.create_opening_balance(session, ctx, customer.id, D("100.00"))
        khata_service.record_payment(session, ctx, customer.id, D("40.00"), entry_date=TODAY)  # no method
        session.commit()

        s = cash_service.daily_summary(session, tenant_a.shop.id, TODAY)

        assert s.expected_closing == D("0.00") and s.unclassified_receipts == D("40.00")
        assert any("no payment method" in n for n in s.notes)


class TestCounts:
    def test_a_count_stores_expected_actual_and_difference(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _count(session, ctx, YESTERDAY, "1000.00")
        make_sale(session, tenant_a, "200.00", day=TODAY)
        session.commit()

        c = _count(session, ctx, TODAY, "1150.00", reason="Gave change from own pocket")

        assert (c.expected_cash, c.actual_cash, c.difference) == (D("1200.00"), D("1150.00"), D("-50.00"))
        assert c.counted_by == ctx.user_id and c.counted_at is not None

    def test_a_difference_needs_a_reason(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _count(session, ctx, YESTERDAY, "1000.00")
        with pytest.raises(InvalidInputError):
            cash_service.record_count(
                session, ctx, count_date=TODAY, actual_cash=D("900.00"), reason=None, today=TODAY
            )

    def test_the_first_count_needs_no_reason_and_has_no_expected(self, session, tenant_a):
        c = _count(session, context_for(tenant_a), TODAY, "750.00")
        assert c.expected_cash is None and c.difference is None

    def test_a_count_never_rewrites_the_expected_figure_of_an_earlier_one(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _count(session, ctx, YESTERDAY, "1000.00")
        first = _count(session, ctx, TODAY, "1000.00")
        make_sale(session, tenant_a, "300.00", day=TODAY)  # arrives after the count
        session.commit()
        assert first.expected_cash == D("1000.00")

    def test_counts_are_insert_only(self, session, tenant_a):
        c = _count(session, context_for(tenant_a), TODAY, "10.00")
        c.actual_cash = D("99.00")
        with pytest.raises(Exception, match="insert-only"):
            session.flush()
        session.rollback()

    def test_a_future_or_negative_count_is_refused(self, session, tenant_a):
        ctx = context_for(tenant_a)
        with pytest.raises(InvalidInputError):
            cash_service.record_count(
                session, ctx, count_date=TODAY + timedelta(days=1), actual_cash=D(1), reason="x", today=TODAY
            )
        with pytest.raises(InvalidInputError):
            cash_service.record_count(
                session, ctx, count_date=TODAY, actual_cash=D("-1"), reason="x", today=TODAY
            )
        with pytest.raises(InvalidInputError):
            cash_service.record_count(
                session, ctx, count_date=TODAY, actual_cash=10.5, reason="x", today=TODAY
            )  # float


class TestCashAdjustments:
    def test_an_adjustment_is_explicit_and_audited_never_a_silent_edit(self, session, tenant_a):
        from sqlalchemy import select

        from app.models import AuditLog

        ctx = context_for(tenant_a)
        _count(session, ctx, YESTERDAY, "100.00")
        finance_ledger_service.record_adjustment(
            session,
            ctx,
            direction=FlowDirection.IN,
            amount=D("5.00"),
            payment_method=FinancePaymentMethod.CASH,
            entry_date=TODAY,
            note="Found in drawer",
        )
        session.commit()

        assert cash_service.daily_summary(session, tenant_a.shop.id, TODAY).expected_closing == D("105.00")
        assert "finance_entry_recorded" in set(session.scalars(select(AuditLog.action)))

    def test_an_adjustment_without_a_reason_is_refused(self, session, tenant_a):
        with pytest.raises(InvalidInputError):
            finance_ledger_service.record_adjustment(
                session,
                context_for(tenant_a),
                direction=FlowDirection.IN,
                amount=D("5.00"),
                payment_method=FinancePaymentMethod.CASH,
                entry_date=TODAY,
                note="  ",
            )

    def test_a_large_cash_adjustment_needs_a_second_persons_approval_then_is_single_use(
        self, session, tenant_a
    ):
        from app.core.context import RequestContext
        from app.services import approval_service

        ctx = context_for(tenant_a)
        finance_settings_service.update_settings(session, ctx, {"cash_adjustment_threshold": D("50.00")})
        kwargs = dict(
            direction=FlowDirection.OUT,
            amount=D("80.00"),
            payment_method=FinancePaymentMethod.CASH,
            entry_date=TODAY,
            note="Bank deposit shortfall",
        )
        pending = finance_ledger_service.record_adjustment(session, ctx, **kwargs)
        session.commit()
        assert pending.entry is None and pending.approval_request_id is not None
        with pytest.raises(ConflictError):  # not approved yet
            finance_ledger_service.record_adjustment(
                session, ctx, approval_request_id=pending.approval_request_id, **kwargs
            )

        other = factories.make_user(session, tenant_a.shop, email="approver@test.local")
        session.commit()
        approval_service.decide(
            session,
            RequestContext(shop_id=tenant_a.shop.id, user_id=other.id, role=ctx.role),
            pending.approval_request_id,
            approve=True,
        )
        session.commit()
        done = finance_ledger_service.record_adjustment(
            session, ctx, approval_request_id=pending.approval_request_id, **kwargs
        )
        session.commit()
        assert done.entry is not None
        with pytest.raises(ConflictError):  # single use
            finance_ledger_service.record_adjustment(
                session, ctx, approval_request_id=pending.approval_request_id, **kwargs
            )
        session.rollback()
        # A different amount cannot ride on the same approval.
        with pytest.raises(InvalidInputError):
            finance_ledger_service.record_adjustment(
                session,
                ctx,
                approval_request_id=pending.approval_request_id,
                **{**kwargs, "amount": D("90.00")},
            )


class TestApi:
    def test_summary_count_and_listing(self, session, tenant_a, client_a):
        assert (
            client_a.post(
                "/api/v1/finance/cash/counts",
                json={"count_date": YESTERDAY.isoformat(), "actual_cash": "500.00"},
            ).status_code
            == 201
        )
        s = client_a.get("/api/v1/finance/cash/summary", params={"day": TODAY.isoformat()}).json()
        assert s["opening_cash"] == "500.00" and s["expected_closing"] == "500.00"
        assert len(client_a.get("/api/v1/finance/cash/counts").json()) == 1

    def test_a_count_in_a_closed_period_is_refused(self, session, tenant_a, client_a):
        ctx = context_for(tenant_a)
        p = finance_period_service.create_period(
            session, ctx, period_start=YESTERDAY - timedelta(days=5), period_end=YESTERDAY
        )
        finance_period_service.close(session, ctx, p.id)
        session.commit()
        r = client_a.post(
            "/api/v1/finance/cash/counts", json={"count_date": YESTERDAY.isoformat(), "actual_cash": "1.00"}
        )
        assert r.status_code == 409
