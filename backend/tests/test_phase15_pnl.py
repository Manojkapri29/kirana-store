"""Profit and loss: the formula, and above all the honesty rules - a missing cost is never zero, Quick Sales
never get a product-level profit, draft expenses never count."""

from datetime import timedelta

from app.models.enums import FinancePaymentMethod, RefundMode
from app.services import expense_service, pnl_service
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import D, make_quick_sale, make_sale, make_sales_return

TODAY = today_in_shop_timezone()


def _pnl(session, tenant, start=None, end=None):
    return pnl_service.compute(session, tenant.shop.id, start or TODAY, end or TODAY)


def _post_expense(session, ctx, amount, day=TODAY, status="POST"):
    cat = expense_service.create_category(session, ctx, name=f"cat-{amount}-{day}-{status}")
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
    if status in ("SUBMIT", "POST"):
        expense_service.submit_expense(session, ctx, e.id)
    if status == "POST":
        expense_service.post_expense(session, ctx, e.id)
    session.commit()
    return e


class TestTheFormula:
    def test_revenue_cogs_gross_net_and_margin_when_every_cost_is_known(self, session, tenant_a):
        ctx = context_for(tenant_a)
        sale = make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        make_sale(session, tenant_a, "500.00", day=TODAY, cogs="300.00")
        make_sales_return(session, tenant_a, sale, "100.00", day=TODAY, mode=RefundMode.CASH, cogs="60.00")
        session.commit()
        _post_expense(session, ctx, "150.00")

        p = _pnl(session, tenant_a)

        assert p.status == "ACTUAL"
        assert p.revenue == D("1400.00")  # 1000 + 500 - 100
        assert p.gross_profit == D("560.00")  # (1000-600) + (500-300) - (100-60)
        assert p.cogs == D("840.00")
        assert p.operating_expenses == D("150.00")
        assert p.net_profit == D("410.00")
        assert p.gross_margin_pct == D("40.00")  # 560 / 1400

    def test_no_sales_at_all_is_an_actual_zero_not_a_gap(self, session, tenant_a):
        p = _pnl(session, tenant_a)
        assert p.status == "ACTUAL" and (p.revenue, p.gross_profit, p.net_profit) == (D("0.00"),) * 3
        assert p.gross_margin_pct is None  # a margin of nothing is not a number


class TestMissingCostIsNeverZero:
    def test_an_unknown_line_cost_makes_profit_not_available_not_full_margin(self, session, tenant_a):
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs=None)  # cost unknown
        session.commit()
        p = _pnl(session, tenant_a)
        assert p.status == "NOT_AVAILABLE" and p.status_note == "Insufficient Cost Data"
        assert p.revenue == D("1000.00")  # revenue is still fact
        assert (
            p.cogs is None and p.gross_profit is None and p.net_profit is None and p.gross_margin_pct is None
        )
        assert "Profit Not Available" in p.notes

    def test_one_uncosted_sale_among_costed_ones_blocks_the_total_but_costed_part_is_reported_labelled(
        self, session, tenant_a
    ):
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="700.00")
        make_sale(session, tenant_a, "200.00", day=TODAY, cogs=None)
        session.commit()
        p = _pnl(session, tenant_a)
        assert p.gross_profit is None and p.detailed_sales_without_cost == 1
        assert p.costed_sales.status == "PARTIAL" and p.costed_sales.gross_profit == D("300.00")
        assert p.costed_sales.coverage_pct == D("83.33")  # 1000 of 1200

    def test_a_return_of_an_uncosted_item_blocks_profit(self, session, tenant_a):
        sale = make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
        make_sales_return(session, tenant_a, sale, "20.00", day=TODAY, cogs=None)
        session.commit()
        p = _pnl(session, tenant_a)
        assert p.gross_profit is None and p.returns_without_cost == 1


class TestQuickSales:
    def test_quick_sales_add_revenue_but_never_a_product_level_profit(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "300.00", day=TODAY)
        session.commit()
        p = _pnl(session, tenant_a)
        assert p.revenue == D("300.00") and p.quick_sales == D("300.00")
        assert p.status == "NOT_AVAILABLE" and p.gross_profit is None and p.quick_sales_have_no_cost is True
        assert p.costed_sales.status == "NOT_AVAILABLE"  # there is nothing costed to report

    def test_quick_sales_never_create_inventory_or_cost_rows(self, session, tenant_a):
        from sqlalchemy import func, select

        from app.models import InventoryTransaction, SaleItem

        make_quick_sale(session, tenant_a, "300.00", day=TODAY)
        session.commit()
        assert session.scalar(select(func.count()).select_from(InventoryTransaction)) == 0
        assert session.scalar(select(func.count()).select_from(SaleItem)) == 0


class TestExpensesInProfit:
    def test_only_posted_expenses_reduce_profit(self, session, tenant_a):
        ctx = context_for(tenant_a)
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="400.00")
        session.commit()
        _post_expense(session, ctx, "100.00", status="DRAFT")
        _post_expense(session, ctx, "200.00", status="SUBMIT")
        assert _pnl(session, tenant_a).operating_expenses == D("0.00")
        _post_expense(session, ctx, "50.00", status="POST")
        p = _pnl(session, tenant_a)
        assert p.operating_expenses == D("50.00") and p.net_profit == D("550.00")

    def test_a_voided_expense_no_longer_reduces_profit(self, session, tenant_a):
        ctx = context_for(tenant_a)
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="400.00")
        session.commit()
        e = _post_expense(session, ctx, "80.00")
        assert _pnl(session, tenant_a).net_profit == D("520.00")
        expense_service.void_expense(session, ctx, e.id, "Mistake", today=TODAY)
        session.commit()
        assert _pnl(session, tenant_a).net_profit == D("600.00")

    def test_expenses_alone_show_a_loss_only_when_gross_profit_is_known(self, session, tenant_a):
        ctx = context_for(tenant_a)
        _post_expense(session, ctx, "90.00")
        p = _pnl(session, tenant_a)
        assert p.status == "ACTUAL" and p.net_profit == D("-90.00")


class TestPeriodsAndDeterminism:
    def test_only_documents_dated_in_the_period_count(self, session, tenant_a):
        make_sale(session, tenant_a, "100.00", day=TODAY - timedelta(days=10), cogs="50.00")
        make_sale(session, tenant_a, "200.00", day=TODAY, cogs="100.00")
        session.commit()
        assert _pnl(session, tenant_a).revenue == D("200.00")
        assert _pnl(session, tenant_a, TODAY - timedelta(days=10), TODAY).revenue == D("300.00")

    def test_the_same_data_always_gives_the_same_answer(self, session, tenant_a):
        make_sale(session, tenant_a, "333.33", day=TODAY, cogs="111.11")
        make_sale(session, tenant_a, "10.01", day=TODAY, cogs="3.33")
        session.commit()
        assert _pnl(session, tenant_a) == _pnl(session, tenant_a)

    def test_money_is_exact_decimal_never_float(self, session, tenant_a):
        make_sale(session, tenant_a, "0.10", day=TODAY, cogs="0.03")
        make_sale(session, tenant_a, "0.20", day=TODAY, cogs="0.06")
        session.commit()
        p = _pnl(session, tenant_a)
        assert p.revenue == D("0.30") and p.gross_profit == D("0.21")
        assert all(isinstance(v, D) for v in (p.revenue, p.cogs, p.gross_profit, p.net_profit))

    def test_trend_buckets_and_null_profit_for_unknown_cost(self, session, tenant_a):
        make_sale(session, tenant_a, "100.00", day=TODAY - timedelta(days=1), cogs="40.00")
        make_sale(session, tenant_a, "50.00", day=TODAY, cogs=None)
        session.commit()
        pts = pnl_service.trend(session, tenant_a.shop.id, TODAY - timedelta(days=1), TODAY, "day")
        assert [(p.revenue, p.gross_profit) for p in pts] == [(D("100.00"), D("60.00")), (D("50.00"), None)]

    def test_another_shops_sales_never_leak_in(self, session, tenant_a, tenant_b):
        make_sale(session, tenant_b, "999.00", day=TODAY, cogs="1.00")
        session.commit()
        assert _pnl(session, tenant_a).revenue == D("0.00")


class TestKhataAndInventoryStayTheSourceOfTruth:
    def test_building_the_report_changes_no_balance_and_no_stock(self, session, tenant_a):
        from sqlalchemy import func, select

        from app.models import CustomerLedgerEntry, InventoryTransaction

        customer = factories.make_customer(session, tenant_a.shop)
        make_sale(session, tenant_a, "100.00", day=TODAY, paid="0.00", customer_id=customer.id, cogs="50.00")
        session.commit()
        before = (
            session.scalar(select(func.count()).select_from(CustomerLedgerEntry)),
            session.scalar(select(func.count()).select_from(InventoryTransaction)),
        )
        _pnl(session, tenant_a)
        assert before == (
            session.scalar(select(func.count()).select_from(CustomerLedgerEntry)),
            session.scalar(select(func.count()).select_from(InventoryTransaction)),
        )
