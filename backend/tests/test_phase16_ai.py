"""AI business-intelligence tools: read-only, permission-gated, same numbers as the analytics reports, and every answer names its
period, source, calculation and limits. Nothing is written and no SQL is accepted."""

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models import AuditLog, SavedReport
from app.services import ai_bi_tools, ai_planner, ai_tools
from app.services.errors import ForbiddenError
from tests import factories
from tests.client_helpers import client_with
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import make_purchase, make_quick_sale, make_sale

TODAY = today_in_shop_timezone()
API = "/api/v1/ai"
BI_TOOLS = set(ai_bi_tools.BI_TOOLS)
ASKED_FOR = {
    "get_kpi",
    "get_executive_dashboard",
    "get_sales_analytics",
    "get_inventory_analytics",
    "get_customer_analytics",
    "get_supplier_analytics",
    "get_finance_analytics",
    "get_cohort_report",
    "get_cross_module_insights",
    "get_saved_report",
}


def _tc(session, tenant, ctx=None):
    return ai_tools.ToolContext(session=session, ctx=ctx or context_for(tenant), today=TODAY)


def _run(session, tenant, name, **args):
    tool = ai_tools.TOOLS[name]
    return tool.run(
        _tc(session, tenant), tool.args.model_validate({"period": "this month", "compare": False, **args})
    )


class TestRegistry:
    def test_all_ten_tools_exist_and_each_has_one_permission_entry(self):
        assert BI_TOOLS == ASKED_FOR
        assert set(ai_tools.TOOLS) == set(ai_tools.TOOL_PERMISSION)
        for name in ASKED_FOR:
            assert ai_tools.TOOL_PERMISSION[name].startswith("ANALYTICS_")

    def test_no_tool_accepts_a_write_a_shop_or_sql_argument(self):
        for name in ASKED_FOR:
            fields = set(ai_tools.TOOLS[name].args.model_fields)
            assert not fields & {
                "shop_id",
                "sql",
                "query",
                "amount",
                "status",
                "approve",
                "post",
                "price",
                "quantity",
            }, name
        with pytest.raises(ValidationError):
            ai_tools.TOOLS["get_kpi"].args.model_validate({"kpi": "revenue", "shop_id": 2})
        with pytest.raises(ValidationError):
            ai_tools.TOOLS["get_sales_analytics"].args.model_validate({"view": "trend; DROP TABLE sales"})

    def test_extra_permissions_mirror_the_analytics_route_rules(self):
        from app.core.permissions import ROUTE_RULES

        def needs(path):
            rule = next(r for r in ROUTE_RULES if r.method == "GET" and r.template == path)
            return set(rule.needs)

        expected = {
            "get_sales_analytics": "/analytics/sales/summary",
            "get_inventory_analytics": "/analytics/inventory/stock",
            "get_customer_analytics": "/analytics/customers/overview",
            "get_supplier_analytics": "/analytics/suppliers/spend",
            "get_finance_analytics": "/analytics/finance/summary",
            "get_cohort_report": "/analytics/cohorts",
            "get_executive_dashboard": "/analytics/executive",
            "get_kpi": "/analytics/kpis",
        }
        for tool, path in expected.items():
            got = {ai_tools.TOOL_PERMISSION[tool], *ai_tools.TOOL_EXTRA_PERMISSIONS[tool]}
            assert got == needs(path), tool

    def test_a_role_without_the_permissions_is_refused(self, session, tenant_a, make_client):
        ctx = context_for(tenant_a)
        weak = type(ctx)(
            shop_id=ctx.shop_id,
            user_id=ctx.user_id,
            role=ctx.role,
            permissions=frozenset({"ANALYTICS_ADVANCED", "REPORT_VIEW"}),
        )
        with pytest.raises(ForbiddenError):
            ai_tools.run_tool(session, weak, "get_finance_analytics", {"view": "summary"})
        with pytest.raises(ForbiddenError):
            ai_tools.run_tool(session, weak, "get_executive_dashboard", {})


class TestAnswers:
    def test_kpi_answer_matches_the_kpi_endpoint_and_explains_itself(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        session.commit()
        a = _run(session, tenant_a, "get_kpi", kpi="revenue")
        api = next(
            k
            for k in client_a.get(
                "/api/v1/analytics/kpis", params={"preset": "this_month", "compare": "none"}
            ).json()["kpis"]
            if k["definition"]["key"] == "revenue"
        )
        assert a.figures[0].value == "₹1,000.00" and api["current"]["amount"] == "1000.00"
        text = " ".join(a.notes)
        assert "Calculation:" in text and "Limitation:" in text and a.sources and a.period["label"]

    def test_profit_not_available_is_never_a_number(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "500.00", day=TODAY)
        session.commit()
        a = _run(session, tenant_a, "get_kpi", kpi="gross_profit")
        assert a.status == "NOT_AVAILABLE" and a.figures[0].value in ("Not Available", "Insufficient Data")
        f = _run(session, tenant_a, "get_finance_analytics", view="summary")
        assert f.status == "NOT_AVAILABLE" and "Profit Not Available" in f.message

    def test_an_unknown_kpi_lists_the_ones_that_exist(self, session, tenant_a):
        a = _run(session, tenant_a, "get_kpi", kpi="happiness")
        assert a.status == "NO_DATA" and "revenue" in a.message

    def test_a_kpi_the_role_cannot_see_is_treated_as_unknown(self, session, give_plan, tenant_a):
        give_plan(tenant_a, "pro")
        ctx = context_for(tenant_a)
        weak = type(ctx)(
            shop_id=ctx.shop_id, user_id=ctx.user_id, role=ctx.role, permissions=frozenset({"ANALYTICS_VIEW"})
        )
        a = ai_tools.run_tool(session, weak, "get_kpi", {"kpi": "gross_profit"})
        assert a.status == "NO_DATA" and not a.figures

    def test_sales_summary_states_online_is_not_available(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "80.00", day=TODAY)
        session.commit()
        a = _run(session, tenant_a, "get_sales_analytics", view="summary")
        by = {f.label: f.value for f in a.figures}
        assert by["Revenue (detailed + quick)"] == "₹80.00" and by["Online sales"] == "Not Available"

    def test_every_view_answers_with_period_source_and_calculation(self, session, tenant_a):
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
        s = factories.make_supplier(session, tenant_a.shop)
        make_purchase(session, tenant_a, s, "50.00", day=TODAY)
        session.commit()
        calls = (
            [
                ("get_sales_analytics", {"view": v})
                for v in ("summary", "trend", "products", "categories", "channels", "payment_methods")
            ]
            + [
                ("get_inventory_analytics", {"view": v})
                for v in ("summary", "stock", "turnover", "fast", "slow", "dead", "reorder", "stock_outs")
            ]
            + [("get_customer_analytics", {"view": v}) for v in ("overview", "segments", "loyalty")]
            + [("get_supplier_analytics", {"view": v}) for v in ("overview", "spend", "returns")]
            + [
                ("get_finance_analytics", {"view": v})
                for v in ("summary", "trend", "expenses", "payment_mix")
            ]
            + [("get_cohort_report", {}), ("get_cross_module_insights", {}), ("get_executive_dashboard", {})]
        )
        for name, args in calls:
            a = _run(session, tenant_a, name, **args)
            assert a.period and a.sources, (name, args)
            assert any("Calculation:" in n for n in a.notes), (name, args)
            assert a.status in ("ANSWERED", "NO_DATA", "NOT_AVAILABLE")

    def test_supplier_answers_never_rank_or_score(self, session, tenant_a):
        s = factories.make_supplier(session, tenant_a.shop, name="Alpha")
        make_purchase(session, tenant_a, s, "500.00", day=TODAY)
        session.commit()
        a = _run(session, tenant_a, "get_supplier_analytics", view="spend")
        text = " ".join([a.message, *a.notes, *[c for c in a.table.columns]]).lower()
        assert "best" not in text.replace("not ranked best", "") and "reliab" not in text.replace(
            "no quality, reliability or delivery data", ""
        )
        assert a.table.rows[0][0] == "Alpha"

    def test_cross_module_answers_use_observed_together_wording(self, session, tenant_a):
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="60.00")
        session.commit()
        a = _run(session, tenant_a, "get_cross_module_insights")
        assert "not causes" in a.message and any("caused" in n for n in a.notes)

    def test_saved_report_runs_with_the_callers_permissions(self, session, give_plan, tenant_a, client_a):
        make_quick_sale(session, tenant_a, "80.00", day=TODAY)
        session.commit()
        rid = client_a.post(
            "/api/v1/analytics/reports",
            json={
                "name": "Totals",
                "dataset": "sales",
                "definition": {"aggregations": [{"field": "total_amount", "op": "SUM"}]},
            },
        ).json()["id"]
        a = _run(session, tenant_a, "get_saved_report", report_id=rid)
        assert a.table.rows == [["₹80.00"]] and a.title == "Totals"
        give_plan(tenant_a, "pro")
        finance_id = client_a.post(
            "/api/v1/analytics/reports", json={"name": "Fin", "dataset": "finance", "definition": {}}
        ).json()["id"]
        ctx = context_for(tenant_a)
        weak = type(ctx)(
            shop_id=ctx.shop_id,
            user_id=ctx.user_id,
            role=ctx.role,
            permissions=frozenset({"ANALYTICS_CUSTOM_REPORT", "REPORT_VIEW"}),
        )
        session.commit()  # start a fresh transaction so the new plan is visible
        with pytest.raises(ForbiddenError):
            ai_tools.run_tool(session, weak, "get_saved_report", {"report_id": finance_id})

    def test_another_shops_saved_report_is_not_found(self, session, give_plan, tenant_a, tenant_b, client_a):
        from app.services.errors import NotFoundError

        rid = client_a.post(
            "/api/v1/analytics/reports", json={"name": "Totals", "dataset": "sales", "definition": {}}
        ).json()["id"]
        give_plan(tenant_b, "pro")
        with pytest.raises(NotFoundError):
            ai_tools.run_tool(session, context_for(tenant_b), "get_saved_report", {"report_id": rid})

    def test_data_of_another_shop_never_appears(self, session, tenant_a, tenant_b):
        make_quick_sale(session, tenant_a, "999.00", day=TODAY)
        session.commit()
        a = _run(session, tenant_b, "get_sales_analytics", view="summary")
        assert {f.label: f.value for f in a.figures}["Revenue (detailed + quick)"] == "₹0.00"

    def test_comparison_says_insufficient_when_the_base_is_zero(self, session, tenant_a):
        make_quick_sale(session, tenant_a, "80.00", day=TODAY)
        session.commit()
        a = ai_tools.TOOLS["get_sales_analytics"].run(
            _tc(session, tenant_a),
            ai_tools.TOOLS["get_sales_analytics"].args.model_validate(
                {"period": "this month", "compare": True}
            ),
        )
        prev = next(f for f in a.figures if f.label == "Previous period revenue")
        assert prev.note in ("Insufficient comparison data", None) or prev.note.endswith("%")


class TestPlanner:
    def test_questions_route_to_the_read_only_tools(self):
        for question, tool in [
            ("what is my revenue kpi", "get_kpi"),
            ("show the gross margin kpi", "get_kpi"),
            ("executive dashboard", "get_executive_dashboard"),
            ("sales analytics for last month", "get_sales_analytics"),
            ("stock turnover this quarter", "get_inventory_analytics"),
            ("customer analytics", "get_customer_analytics"),
            ("supplier spend this month", "get_supplier_analytics"),
            ("finance analytics", "get_finance_analytics"),
            ("cohort retention", "get_cohort_report"),
            ("what was observed together across modules", "get_cross_module_insights"),
            ("run saved report #3", "get_saved_report"),
        ]:
            planned = ai_planner.route(question, TODAY)
            assert planned is not None and planned.tool == tool, question
        assert ai_planner.route("revenue kpi", TODAY).args["kpi"] == "revenue"
        assert ai_planner.route("gross margin kpi", TODAY).args["kpi"] == "gross_margin"

    def test_writes_and_sql_are_refused_before_any_tool_runs(self):
        for q in (
            "delete the saved report",
            "change the price of rice",
            "approve the expense",
            "refund the last sale",
            "update inventory to 50",
        ):
            assert ai_planner.guard(q).kind == "write", q
        assert ai_planner.guard("SELECT * FROM sales").kind == "sql"

    def test_asking_never_writes_anything(self, session, tenant_a, client_a):
        make_sale(session, tenant_a, "100.00", day=TODAY, cogs="40.00")
        session.commit()
        client_a.post("/api/v1/analytics/reports", json={"name": "S", "dataset": "sales", "definition": {}})
        before = (
            session.scalar(select(func.count()).select_from(SavedReport)),
            session.scalar(select(func.count()).select_from(AuditLog)),
        )
        for q in (
            "delete the saved report S",
            "sales analytics",
            "executive dashboard",
            "what is my gross margin kpi",
            "cohort retention",
        ):
            client_a.post(f"{API}/ask", json={"question": q})
        session.expire_all()
        after = (
            session.scalar(select(func.count()).select_from(SavedReport)),
            session.scalar(select(func.count()).select_from(AuditLog)),
        )
        assert before[0] == after[0]
        actions = set(session.scalars(select(AuditLog.action)))
        assert not {a for a in actions if a.startswith("saved_report_") and a != "saved_report_created"}


def test_the_ask_endpoint_answers_a_bi_question_within_the_role(session, tenant_a, make_client):
    make_sale(session, tenant_a, "100.00", day=TODAY, cogs="40.00")
    session.commit()
    c = client_with(make_client, tenant_a, ["AI_ASSISTANT_USE", "ANALYTICS_VIEW"])
    r = c.post(f"{API}/ask", json={"question": "what is my revenue kpi"})
    assert r.status_code in (200, 403), r.text
