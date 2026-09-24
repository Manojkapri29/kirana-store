"""The finance dashboard (backend-authoritative) and neutral financial alerts."""

from datetime import timedelta

from sqlalchemy import select

from app.models import NotificationEvent
from app.models.enums import FinancePaymentMethod, PaymentMethod
from app.services import (
    cash_service,
    expense_service,
    finance_alert_service,
    finance_dashboard_service,
    finance_period_service,
    finance_settings_service,
    khata_service,
    pnl_service,
)
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import D, make_purchase, make_quick_sale, make_sale

TODAY = today_in_shop_timezone()
FORBIDDEN_WORDS = ("fraud", "theft", "stole", "steal", "suspicious", "criminal", "embezzl", "cheat")


def _expense(session, ctx, amount, day, cat=None):
    cat = cat or expense_service.create_category(session, ctx, name=f"C-{amount}-{day}")
    e = expense_service.create_expense(
        session,
        ctx,
        {
            "expense_date": day,
            "category_id": cat.id,
            "amount": D(amount),
            "payment_method": FinancePaymentMethod.CASH,
        },
    )
    expense_service.submit_expense(session, ctx, e.id)
    expense_service.post_expense(session, ctx, e.id)
    session.commit()
    return e


def _dash(session, tenant, **kw):
    return finance_dashboard_service.build(
        session, tenant.shop.id, TODAY - timedelta(days=6), TODAY, today=TODAY, **kw
    )


class TestDashboard:
    def test_every_figure_agrees_with_the_report_that_owns_it(self, session, tenant_a):
        ctx = context_for(tenant_a)
        customer = factories.make_customer(session, tenant_a.shop)
        supplier = factories.make_supplier(session, tenant_a.shop)
        session.commit()
        make_sale(
            session, tenant_a, "1000.00", day=TODAY, cogs="600.00", paid="400.00", customer_id=customer.id
        )
        make_purchase(session, tenant_a, supplier, "300.00", day=TODAY)
        session.commit()
        khata_service.create_opening_balance(session, ctx, customer.id, D("600.00"))
        _expense(session, ctx, "100.00", TODAY)

        d = _dash(session, tenant_a)

        report = pnl_service.compute(session, tenant_a.shop.id, d.date_from, d.date_to)
        assert d.pnl == report
        assert (d.pnl.revenue, d.pnl.gross_profit, d.pnl.net_profit) == (
            D("1000.00"),
            D("400.00"),
            D("300.00"),
        )
        # Khata is the source of truth: only its 600 opening balance counts; the sale row alone changes nothing.
        assert d.customer_outstanding == D("600.00") == d.receivables_total
        assert d.supplier_outstanding == D("300.00") == d.payables_total
        assert d.expense_categories[0].amount == D("100.00")
        assert d.granularity == "day" and len(d.revenue_trend) == 7

    def test_comparison_is_the_previous_period_of_equal_length_and_null_against_zero(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "200.00", day=TODAY)
        make_quick_sale(session, tenant_a, "100.00", day=TODAY - timedelta(days=8))
        session.commit()
        d = _dash(session, tenant_a)
        assert (d.comparison_from, d.comparison_to) == (TODAY - timedelta(days=13), TODAY - timedelta(days=7))
        assert d.revenue_change.change_pct == D("100.0")  # 200 vs 100
        assert d.expenses_change.change_pct is None  # nothing before: not a percentage
        assert d.gross_profit_change.current is None  # quick sales: profit not available, never zero

    def test_profit_is_null_not_zero_when_cost_is_missing(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "200.00", day=TODAY)
        session.commit()
        d = _dash(session, tenant_a)
        assert d.pnl.gross_profit is None and d.pnl.net_profit is None
        assert any("Profit Not Available" in n for n in d.notes)

    def test_tax_is_not_shown_until_configured(self, session, tenant_a):
        d = _dash(session, tenant_a)
        assert d.tax_status == "NOT_CONFIGURED" and d.tax_collected is None

    def test_payment_method_mix_and_cash_flow(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "100.00", day=TODAY, method=PaymentMethod.UPI)
        make_quick_sale(session, tenant_a, "50.00", day=TODAY, method=PaymentMethod.CASH)
        session.commit()
        d = _dash(session, tenant_a)
        assert {m.payment_method: m.inflow for m in d.payment_method_mix} == {
            "UPI": D("100.00"),
            "CASH": D("50.00"),
        }
        assert d.cash_flow.net == D("150.00")

    def test_a_long_range_is_bucketed_by_month(self, session, tenant_a):
        d = finance_dashboard_service.build(
            session, tenant_a.shop.id, TODAY - timedelta(days=200), TODAY, today=TODAY, compare=False
        )
        assert d.granularity == "month" and d.revenue_change is None

    def test_api_returns_the_same_numbers_and_is_isolated(
        self, session, tenant_a, tenant_b, client_a, client_b
    ):
        make_quick_sale(session, tenant_a, "80.00", day=TODAY)
        session.commit()
        params = {"date_from": (TODAY - timedelta(days=6)).isoformat(), "date_to": TODAY.isoformat()}
        a = client_a.get("/api/v1/finance/dashboard", params=params).json()
        b = client_b.get("/api/v1/finance/dashboard", params=params).json()
        assert a["pnl"]["revenue"] == "80.00" and b["pnl"]["revenue"] == "0.00"
        assert a["pnl"]["gross_profit"] is None


class TestAlerts:
    def _codes(self, session, tenant):
        return {a.code: a for a in finance_alert_service.compute(session, tenant.shop.id, TODAY)}

    def test_a_quiet_shop_has_no_alerts(self, session, tenant_a):
        assert finance_alert_service.compute(session, tenant_a.shop.id, TODAY) == []

    def test_an_expense_variance_uses_the_shops_own_threshold(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _expense(session, ctx, "100.00", TODAY - timedelta(days=40))
        _expense(session, ctx, "160.00", TODAY - timedelta(days=2))
        assert "EXPENSE_VARIANCE" in self._codes(session, tenant_a)  # +60% vs default 50%
        finance_settings_service.update_settings(session, ctx, {"expense_spike_pct": 80})
        session.commit()
        assert "EXPENSE_VARIANCE" not in self._codes(session, tenant_a)

    def test_no_baseline_means_no_variance_alert(self, session, tenant_a):
        _expense(session, context_for(tenant_a), "999.00", TODAY)
        assert "EXPENSE_VARIANCE" not in self._codes(session, tenant_a)

    def test_aged_customer_balances_use_the_shops_days_setting(self, session, tenant_a):
        ctx = context_for(tenant_a)
        c = factories.make_customer(session, tenant_a.shop)
        session.commit()
        khata_service.create_opening_balance(
            session, ctx, c.id, D("500.00"), entry_date=TODAY - timedelta(days=45)
        )
        session.commit()
        alert = self._codes(session, tenant_a)["AGED_CUSTOMER_BALANCE"]
        assert alert.figures == {"customers": "1", "amount": "500.00", "days": "30"}
        assert "no due dates" in alert.message
        finance_settings_service.update_settings(session, ctx, {"overdue_after_days": 60})
        session.commit()
        assert "AGED_CUSTOMER_BALANCE" not in self._codes(session, tenant_a)

    def test_cash_count_variance(self, session, tenant_a):
        ctx = context_for(tenant_a)
        cash_service.record_count(
            session,
            ctx,
            count_date=TODAY - timedelta(days=2),
            actual_cash=D("1000.00"),
            reason=None,
            today=TODAY,
        )
        cash_service.record_count(
            session, ctx, count_date=TODAY, actual_cash=D("900.00"), reason="Short", today=TODAY
        )
        session.commit()
        a = self._codes(session, tenant_a)["CASH_COUNT_VARIANCE"]
        assert "Review recommended" in a.message and a.figures["difference"] == "-100.00"

    def test_missing_cost_and_falling_margin(self, session, tenant_a):
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs=None)
        make_sale(
            session, tenant_a, "900.00", day=TODAY - timedelta(days=45), cogs="450.00"
        )  # 50% margin before
        make_sale(session, tenant_a, "900.00", day=TODAY - timedelta(days=3), cogs="810.00")  # 10% margin now
        session.commit()
        codes = self._codes(session, tenant_a)
        assert "MISSING_COST" in codes and codes["MISSING_COST"].title == "Missing cost data"

    def test_falling_margin_when_both_windows_are_costed(self, session, tenant_a):
        make_sale(session, tenant_a, "900.00", day=TODAY - timedelta(days=45), cogs="450.00")
        make_sale(session, tenant_a, "900.00", day=TODAY - timedelta(days=3), cogs="810.00")
        session.commit()
        assert self._codes(session, tenant_a)["FALLING_MARGIN"].figures == {
            "current_pct": "10.00",
            "previous_pct": "50.00",
        }

    def test_unreconciled_payments(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "100.00", day=TODAY, method=PaymentMethod.UPI)
        session.commit()
        assert self._codes(session, tenant_a)["UNRECONCILED_PAYMENTS"].figures["count"] == "1"

    def test_payables_increasing(self, session, tenant_a):
        supplier = factories.make_supplier(session, tenant_a.shop)
        make_purchase(session, tenant_a, supplier, "100.00", day=TODAY - timedelta(days=60))
        make_purchase(session, tenant_a, supplier, "200.00", day=TODAY - timedelta(days=3))
        session.commit()
        assert "PAYABLE_INCREASING" in self._codes(session, tenant_a)

    def test_a_refused_period_change_raises_an_alert(self, session, tenant_a, client_a):
        ctx = context_for(tenant_a)
        p = finance_period_service.create_period(
            session, ctx, period_start=TODAY - timedelta(days=9), period_end=TODAY - timedelta(days=5)
        )
        finance_period_service.close(session, ctx, p.id)
        session.commit()
        client_a.post(
            "/api/v1/finance/entries",
            json={
                "event_type": "OTHER_INCOME",
                "amount": "5.00",
                "payment_method": "CASH",
                "entry_date": (TODAY - timedelta(days=7)).isoformat(),
            },
        )
        assert self._codes(session, tenant_a)["PERIOD_CHANGE_REFUSED"].figures["attempts"] == "1"

    def test_wording_is_neutral_never_accusatory(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _expense(session, ctx, "100.00", TODAY - timedelta(days=40))
        _expense(session, ctx, "900.00", TODAY - timedelta(days=2))
        cash_service.record_count(
            session,
            ctx,
            count_date=TODAY - timedelta(days=2),
            actual_cash=D("1000.00"),
            reason=None,
            today=TODAY,
        )
        cash_service.record_count(
            session, ctx, count_date=TODAY, actual_cash=D("500.00"), reason="Short", today=TODAY
        )
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs=None)
        session.commit()
        alerts = finance_alert_service.compute(session, tenant_a.shop.id, TODAY)
        assert alerts
        for a in alerts:
            text = f"{a.title} {a.message}".lower()
            assert not any(w in text for w in FORBIDDEN_WORDS)
            assert a.severity in ("INFO", "REVIEW")

    def test_alerts_reach_the_notification_centre_once_a_day(self, session, tenant_a):
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs=None)
        session.commit()
        assert finance_alert_service.notify(session, tenant_a.shop.id, TODAY) == 1
        finance_alert_service.notify(session, tenant_a.shop.id, TODAY)
        session.commit()
        events = session.scalars(
            select(NotificationEvent).where(NotificationEvent.shop_id == tenant_a.shop.id)
        ).all()
        assert len([e for e in events if e.title == "Missing cost data"]) == 1

    def test_api_alerts_are_shop_scoped(self, session, tenant_a, tenant_b, client_a, client_b):
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs=None)
        session.commit()
        assert [a["code"] for a in client_a.get("/api/v1/finance/alerts").json()] == ["MISSING_COST"]
        assert client_b.get("/api/v1/finance/alerts").json() == []
