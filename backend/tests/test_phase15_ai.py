"""AI finance assistant: read-only tools that only echo what the finance services return, respect permissions,
explain period/source/limits, and can never post, reconcile, unlock, transfer or run SQL."""

import pytest
from pydantic import ValidationError

from app.models import AuditLog, Expense, FinanceEntry
from app.models.enums import PaymentMethod
from app.services import ai_action_service, ai_planner, ai_tools, pnl_service
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import D, make_quick_sale, make_sale

TODAY = today_in_shop_timezone()
API = "/api/v1/ai"
FINANCE_TOOLS = {
    "get_revenue_summary": "FINANCE_VIEW",
    "get_pnl_summary": "FINANCE_VIEW",
    "get_expense_summary": "FINANCE_EXPENSE_VIEW",
    "get_cash_flow_summary": "FINANCE_VIEW",
    "get_receivables_summary": "FINANCE_VIEW",
    "get_payables_summary": "FINANCE_VIEW",
    "get_financial_ledger": "FINANCE_VIEW",
    "get_tax_summary": "FINANCE_VIEW",
    "get_reconciliation_summary": "FINANCE_VIEW",
    "get_financial_dashboard": "FINANCE_VIEW",
}


def _tc(session, tenant):
    return ai_tools.ToolContext(session=session, ctx=context_for(tenant), today=TODAY)


def _run(session, tenant, name, **args):
    tool = ai_tools.TOOLS[name]
    return tool.run(_tc(session, tenant), tool.args.model_validate(args))


class TestToolRegistry:
    def test_every_finance_tool_is_registered_with_its_permission(self):
        for name, permission in FINANCE_TOOLS.items():
            assert name in ai_tools.TOOLS and ai_tools.TOOL_PERMISSION[name] == permission
        assert set(ai_tools.TOOLS) == set(ai_tools.TOOL_PERMISSION)

    def test_no_finance_tool_accepts_a_write_or_a_shop_or_sql_argument(self):
        for name in FINANCE_TOOLS:
            fields = set(ai_tools.TOOLS[name].args.model_fields)
            assert not fields & {"shop_id", "sql", "query", "amount", "status", "approve", "post"}
        with pytest.raises(ValidationError):
            ai_tools.TOOLS["get_pnl_summary"].args.model_validate({"shop_id": 2})

    def test_a_role_without_the_permission_cannot_run_a_tool(self, session, tenant_a, make_client):
        from app.models.enums import UserRole

        staff = make_client(tenant_a, role=UserRole.STAFF)
        # The legacy STAFF role holds the *_VIEW permissions but not, for example, FINANCE_EXPENSE_MANAGE.
        assert staff.post(f"{API}/ask", json={"question": "post an expense of 500"}).status_code in (200, 403)


class TestNoInventedNumbers:
    def test_profit_not_available_when_cost_is_missing(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "500.00", day=TODAY)
        session.commit()
        a = _run(session, tenant_a, "get_pnl_summary", date_from=TODAY.isoformat(), date_to=TODAY.isoformat())
        assert a.status == "NOT_AVAILABLE" and "Insufficient Cost Data" in a.message
        by_label = {f.label: f.value for f in a.figures}
        assert by_label["Net profit"] == "Not Available" and by_label["Gross profit"] == "Not Available"
        assert by_label["Revenue"] == "₹500.00"

    def test_the_tool_reports_exactly_the_service_figures(self, session, tenant_a):
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        session.commit()
        p = pnl_service.compute(session, tenant_a.shop.id, TODAY, TODAY)
        a = _run(session, tenant_a, "get_pnl_summary", date_from=TODAY.isoformat(), date_to=TODAY.isoformat())
        by_label = {f.label: f.value for f in a.figures}
        assert (
            p.gross_profit == D("400.00") and by_label["Gross profit"] == "₹400.00" and a.status == "ANSWERED"
        )
        assert by_label["Gross margin"] == "40.00%"

    def test_answers_explain_period_source_calculation_and_limits(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "100.00", day=TODAY)
        session.commit()
        a = _run(
            session, tenant_a, "get_revenue_summary", date_from=TODAY.isoformat(), date_to=TODAY.isoformat()
        )
        assert a.period["from"] == TODAY.isoformat() and a.sources
        text = " ".join(a.notes)
        assert "Calculation" in text and "Limitation" in text and "Nothing here changes any record" in text

    def test_missing_data_says_so_instead_of_zero(self, session, tenant_a):
        assert _run(session, tenant_a, "get_receivables_summary").status == "NO_DATA"
        assert _run(session, tenant_a, "get_payables_summary").status == "NO_DATA"
        assert _run(session, tenant_a, "get_expense_summary").status == "NO_DATA"
        assert _run(session, tenant_a, "get_tax_summary").status == "NOT_CONFIGURED"

    def test_reconciliation_states_bank_integration_is_not_configured(self, session, tenant_a):
        a = _run(session, tenant_a, "get_reconciliation_summary")
        assert any("Bank Integration Not Configured" in n for n in a.notes)

    def test_the_ledger_tool_lists_source_documents(self, session, tenant_a):
        sale = make_quick_sale(session, tenant_a, "100.00", day=TODAY, method=PaymentMethod.UPI)
        session.commit()
        a = _run(
            session, tenant_a, "get_financial_ledger", date_from=TODAY.isoformat(), date_to=TODAY.isoformat()
        )
        assert sale.quick_no in a.table.rows[0]
        assert (
            _run(
                session,
                tenant_a,
                "get_financial_ledger",
                event_type="EXPENSE",
                date_from=TODAY.isoformat(),
                date_to=TODAY.isoformat(),
            ).status
            == "NO_DATA"
        )

    def test_a_tool_never_sees_another_shops_money(self, session, tenant_a, tenant_b):
        make_sale(session, tenant_b, "999.00", day=TODAY, cogs="1.00")
        session.commit()
        a = _run(
            session, tenant_a, "get_revenue_summary", date_from=TODAY.isoformat(), date_to=TODAY.isoformat()
        )
        assert {f.label: f.value for f in a.figures}["Net revenue"] == "₹0.00"

    def test_khata_is_the_source_of_receivables(self, session, tenant_a):
        from app.services import khata_service

        c = factories.make_customer(session, tenant_a.shop)
        session.commit()
        khata_service.create_opening_balance(session, context_for(tenant_a), c.id, D("250.00"))
        session.commit()
        a = _run(session, tenant_a, "get_receivables_summary")
        assert {f.label: f.value for f in a.figures}["Total receivables"] == "₹250.00"
        assert any("Khata" in s for s in a.sources)


class TestTheAssistantNeverActs:
    @pytest.mark.parametrize(
        "question",
        [
            "post an expense of 500 for rent",
            "reconcile all my payments",
            "unlock the financial period",
            "reopen the closed period",
            "transfer 1000 to the supplier",
            "refund the last customer",
            "approve the pending expense",
            "record a supplier payment of 200",
            "change the tax rate to 5",
            "adjust the cash balance",
            "close the books for last month",
        ],
    )
    def test_financial_write_requests_are_refused_by_the_backend(self, question):
        refusal = ai_planner.guard(question)
        assert refusal is not None and refusal.kind == "write"

    def test_sql_is_refused(self):
        assert ai_planner.guard("SELECT * FROM finance_entries").kind == "sql"

    def test_reading_questions_route_to_the_read_only_tools(self):
        for question, tool in [
            ("what is my net profit this month", "get_pnl_summary"),
            ("show my expenses", "get_expense_summary"),
            ("cash flow last month", "get_cash_flow_summary"),
            ("who owes me money receivables", "get_receivables_summary"),
            ("how much do we owe suppliers payable", "get_payables_summary"),
            ("tax summary", "get_tax_summary"),
            ("reconciliation status", "get_reconciliation_summary"),
            ("finance dashboard", "get_financial_dashboard"),
        ]:
            planned = ai_planner.route(question, TODAY)
            assert planned is not None and planned.tool == tool, question

    def test_there_is_no_finance_action_kind_so_nothing_can_be_confirmed_into_a_posting(self):
        kinds = {k.value for k in ai_action_service.AiActionKind}
        assert not {k for k in kinds if "EXPENSE" in k or "FINANCE" in k or "PERIOD" in k or "RECON" in k}

    def test_asking_never_writes_anything(self, session, tenant_a, client_a):
        from sqlalchemy import func, select

        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="40.00")
        session.commit()
        before = tuple(session.scalar(select(func.count()).select_from(m)) for m in (Expense, FinanceEntry))
        for q in ("post an expense of 500", "what is my net profit", "show the financial ledger"):
            client_a.post(f"{API}/ask", json={"question": q})
        session.expire_all()
        assert before == tuple(
            session.scalar(select(func.count()).select_from(m)) for m in (Expense, FinanceEntry)
        )
        assert "finance_entry_recorded" not in set(session.scalars(select(AuditLog.action)))
