"""Approvals, RBAC and tenant isolation across EVERY finance route: the permission each route declares is
really enforced, a request with no permission gets 403, and one shop can never reach another's finance data."""

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_request_context
from app.core.context import RequestContext
from app.core.permissions import ROUTE_RULES, SYSTEM_ROLES
from app.main import create_app
from app.models import AuditLog
from app.models.enums import FinancePaymentMethod, UserRole
from app.services import approval_service, expense_service, finance_settings_service
from tests import factories
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()
FINANCE_RULES = [r for r in ROUTE_RULES if r.template.startswith("/finance")]


@pytest.fixture(autouse=True)
def _bind(make_client):
    """`_client` needs the fixture that points the API at the test database."""
    _STATE["make_client"] = make_client


_STATE: dict = {}


def _client(tenant, user_id, permissions):
    client: TestClient = _STATE["make_client"](tenant)
    ctx = RequestContext(
        shop_id=tenant.shop.id, user_id=user_id, role=UserRole.STAFF, permissions=frozenset(permissions)
    )
    client.app.dependency_overrides[get_request_context] = lambda: ctx
    return client


def _path(template):
    return "/api/v1" + re.sub(r"\{[^}]+\}", "1", template)


class TestEveryFinanceRouteEnforcesItsPermission:
    @pytest.mark.parametrize("rule", FINANCE_RULES, ids=lambda r: f"{r.method} {r.template}")
    def test_no_permission_is_forbidden_and_the_exact_permission_gets_through(self, session, tenant_a, rule):
        nobody = _client(tenant_a, tenant_a.user.id, [])
        allowed = _client(tenant_a, tenant_a.user.id, list(rule.needs))
        body = {} if rule.method in ("POST", "PUT", "PATCH") else None
        denied = nobody.request(rule.method, _path(rule.template), json=body)
        assert denied.status_code == 403, denied.text
        ok = allowed.request(rule.method, _path(rule.template), json=body)
        assert ok.status_code != 403 and ok.status_code != 401, ok.text

    def test_there_is_a_rule_for_every_finance_route_and_none_is_a_delete(self):
        spec = create_app().openapi()["paths"]
        routes = {
            (method.upper(), path.replace("/api/v1", ""))
            for path, item in spec.items()
            if path.startswith("/api/v1/finance")
            for method in item
        }
        declared = {(r.method, r.template) for r in FINANCE_RULES}
        assert routes == declared
        assert not {m for m, _ in routes if m == "DELETE"}

    def test_the_default_roles_hold_only_what_their_job_needs(self):
        cashier = SYSTEM_ROLES["CASHIER"][2]
        assert {"FINANCE_CASH_VIEW", "FINANCE_CASH_MANAGE"} <= cashier
        assert (
            not {
                "FINANCE_VIEW",
                "FINANCE_EXPENSE_APPROVE",
                "FINANCE_PERIOD_UNLOCK",
                "FINANCE_ADJUSTMENT_MANAGE",
            }
            & cashier
        )
        accountant = SYSTEM_ROLES["ACCOUNTANT"][2]
        assert (
            "FINANCE_PERIOD_LOCK" in accountant
            and not {"FINANCE_PERIOD_UNLOCK", "FINANCE_EXPENSE_APPROVE", "FINANCE_ADJUSTMENT_MANAGE"}
            & accountant
        )
        sales = SYSTEM_ROLES["SALES_STAFF"][2]
        assert not {p for p in sales if p.startswith("FINANCE_")}
        inventory = SYSTEM_ROLES["INVENTORY_STAFF"][2]
        assert not {p for p in inventory if p.startswith("FINANCE_")}

    def test_unlocking_and_locking_are_separate_permissions(self):
        rules = {r.template: r.needs for r in FINANCE_RULES if r.method == "POST"}
        assert rules["/finance/periods/{period_id}/lock"] == ("FINANCE_PERIOD_LOCK",)
        assert rules["/finance/periods/{period_id}/unlock"] == ("FINANCE_PERIOD_UNLOCK",)
        assert rules["/finance/periods/{period_id}/reopen"] == ("FINANCE_PERIOD_UNLOCK",)


class TestApprovalsAreDecidedByTheRightPeople:
    def _pending_expense_request(self, session, tenant):
        ctx = context_for(tenant)
        finance_settings_service.update_settings(session, ctx, {"expense_approval_threshold": 100})
        cat = expense_service.create_category(session, ctx, name="Rent")
        e = expense_service.create_expense(
            session,
            ctx,
            {
                "expense_date": TODAY,
                "category_id": cat.id,
                "amount": __import__("decimal").Decimal("500.00"),
                "payment_method": FinancePaymentMethod.CASH,
            },
        )
        expense_service.submit_expense(session, ctx, e.id)
        approver = factories.make_user(session, tenant.shop, email="fin-approver@test.local")
        session.commit()
        request = approval_service.list_pending(session, tenant.shop.id)[0]
        return e, request, approver

    def test_a_stock_count_approver_cannot_approve_an_expense(self, session, tenant_a):
        e, request, approver = self._pending_expense_request(session, tenant_a)
        stock_only = _client(tenant_a, approver.id, ["STOCK_COUNT_APPROVE"])
        r = stock_only.post(f"/api/v1/approvals/{request.id}/decide", json={"approve": True})
        assert r.status_code == 403
        assert stock_only.get(f"/api/v1/approvals/{request.id}").status_code == 403
        assert stock_only.get("/api/v1/approvals").json()["total"] == 0  # not even listed

    def test_the_finance_approver_can_decide_through_the_shared_queue_and_it_is_idempotent(
        self, session, tenant_a, fresh
    ):
        e, request, approver = self._pending_expense_request(session, tenant_a)
        finance = _client(tenant_a, approver.id, ["FINANCE_EXPENSE_APPROVE"])
        assert finance.get("/api/v1/approvals").json()["total"] == 1
        ok = finance.post(f"/api/v1/approvals/{request.id}/decide", json={"approve": True})
        assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
        again = finance.post(f"/api/v1/approvals/{request.id}/decide", json={"approve": True})
        assert again.status_code == 409  # never decided twice
        assert (
            fresh(lambda s: expense_service.get_expense(s, tenant_a.shop.id, e.id).status.value) == "APPROVED"
        )

    def test_the_submitter_cannot_approve_even_holding_the_permission(self, session, tenant_a):
        e, request, approver = self._pending_expense_request(session, tenant_a)
        owner_like = _client(tenant_a, tenant_a.user.id, ["FINANCE_EXPENSE_APPROVE"])
        r = owner_like.post(f"/api/v1/finance/expenses/{e.id}/approve")
        assert r.status_code == 403

    def test_approval_decisions_are_audited_with_who_and_when(self, session, tenant_a, fresh):
        e, request, approver = self._pending_expense_request(session, tenant_a)
        _client(tenant_a, approver.id, ["FINANCE_EXPENSE_APPROVE"]).post(
            f"/api/v1/finance/expenses/{e.id}/approve"
        )
        who = fresh(
            lambda s: [
                (r.user_id, r.created_at)
                for r in s.scalars(select(AuditLog).where(AuditLog.action == "approval_decided"))
            ]
        )
        assert who and who[0][0] == approver.id and who[0][1] is not None

    def test_a_period_reopen_approval_needs_the_unlock_permission(self, session, tenant_a):
        from app.services import finance_period_service

        ctx = context_for(tenant_a)
        finance_settings_service.update_settings(session, ctx, {"period_reopen_requires_approval": True})
        p = finance_period_service.create_period(
            session, ctx, period_start=TODAY - timedelta(days=9), period_end=TODAY - timedelta(days=5)
        )
        finance_period_service.close(session, ctx, p.id)
        finance_period_service.reopen(session, ctx, p.id, "Fix")
        approver = factories.make_user(session, tenant_a.shop, email="unlocker@test.local")
        session.commit()
        request = approval_service.list_pending(session, tenant_a.shop.id)[0]
        assert (
            _client(tenant_a, approver.id, ["STOCK_COUNT_APPROVE", "FINANCE_EXPENSE_APPROVE"])
            .post(f"/api/v1/approvals/{request.id}/decide", json={"approve": True})
            .status_code
            == 403
        )
        assert (
            _client(tenant_a, approver.id, ["FINANCE_PERIOD_UNLOCK"])
            .post(f"/api/v1/approvals/{request.id}/decide", json={"approve": True})
            .status_code
            == 200
        )

    def test_existing_stock_count_and_campaign_approvals_still_use_their_own_permissions(
        self, session, tenant_a
    ):
        ctx = context_for(tenant_a)
        req = approval_service.create(
            session, ctx, kind="CAMPAIGN_LARGE_AUDIENCE", entity_type="campaign", entity_id=1, reason="x"
        )
        other = factories.make_user(session, tenant_a.shop, email="camp@test.local")
        session.commit()
        assert (
            _client(tenant_a, other.id, ["STOCK_COUNT_APPROVE"])
            .post(f"/api/v1/approvals/{req.id}/decide", json={"approve": True})
            .status_code
            == 403
        )


class TestTenantIsolationAcrossFinance:
    def _seed(self, session, tenant):
        from decimal import Decimal

        from app.models.enums import FinanceEventType, FlowDirection
        from app.services import finance_ledger_service, finance_period_service

        ctx = context_for(tenant)
        cat = expense_service.create_category(session, ctx, name="Rent")
        e = expense_service.create_expense(
            session,
            ctx,
            {
                "expense_date": TODAY,
                "category_id": cat.id,
                "amount": Decimal("10.00"),
                "payment_method": FinancePaymentMethod.CASH,
            },
        )
        entry, _ = finance_ledger_service.record_entry(
            session,
            ctx,
            event_type=FinanceEventType.OTHER_INCOME,
            direction=FlowDirection.IN,
            amount=Decimal("5.00"),
            payment_method=FinancePaymentMethod.CASH,
            entry_date=TODAY,
        )
        period = finance_period_service.create_period(
            session, ctx, period_start=TODAY - timedelta(days=90), period_end=TODAY - timedelta(days=80)
        )
        session.commit()
        return {"expense": e.id, "category": cat.id, "entry": entry.id, "period": period.id}

    def test_another_shops_records_are_not_found_for_every_by_id_route(
        self, session, tenant_a, tenant_b, client_b
    ):
        ids = self._seed(session, tenant_a)
        calls = [
            ("GET", f"/expenses/{ids['expense']}", None),
            ("PATCH", f"/expenses/{ids['expense']}", {"payee": "x"}),
            ("POST", f"/expenses/{ids['expense']}/submit", None),
            ("POST", f"/expenses/{ids['expense']}/approve", None),
            ("POST", f"/expenses/{ids['expense']}/reject", {"reason": "x"}),
            ("POST", f"/expenses/{ids['expense']}/post", None),
            ("POST", f"/expenses/{ids['expense']}/void", {"reason": "x"}),
            ("PATCH", f"/expense-categories/{ids['category']}", {"name": "Y"}),
            ("POST", f"/entries/{ids['entry']}/reverse", {"reason": "x"}),
            ("POST", f"/periods/{ids['period']}/lock", None),
            ("POST", f"/periods/{ids['period']}/close", None),
            ("POST", f"/periods/{ids['period']}/unlock", None),
            ("POST", f"/periods/{ids['period']}/reopen", {"reason": "x"}),
        ]
        for method, path, body in calls:
            r = client_b.request(method, "/api/v1/finance" + path, json=body)
            assert r.status_code == 404, (method, path, r.status_code, r.text)

    def test_lists_and_reports_of_one_shop_never_include_the_others(
        self, session, tenant_a, tenant_b, client_b
    ):
        self._seed(session, tenant_a)
        assert client_b.get("/api/v1/finance/expenses").json()["total"] == 0
        assert client_b.get("/api/v1/finance/expense-categories").json() == []
        assert client_b.get("/api/v1/finance/ledger").json()["total"] == 0
        assert client_b.get("/api/v1/finance/periods").json() == []

    def test_settings_are_per_shop(self, session, tenant_a, tenant_b, client_a, client_b):
        assert (
            client_a.put(
                "/api/v1/finance/settings", json={"expense_approval_threshold": "999.00"}
            ).status_code
            == 200
        )
        assert client_b.get("/api/v1/finance/settings").json()["expense_approval_threshold"] is None

    def test_a_category_of_another_shop_cannot_be_used_on_an_expense(
        self, session, tenant_a, tenant_b, client_b
    ):
        ids = self._seed(session, tenant_a)
        r = client_b.post(
            "/api/v1/finance/expenses",
            json={
                "expense_date": TODAY.isoformat(),
                "category_id": ids["category"],
                "amount": "5.00",
                "payment_method": "CASH",
            },
        )
        assert r.status_code == 404


class TestAuditTrailAndNoSecrets:
    def test_financial_actions_are_audited(self, session, tenant_a, client_a):
        cat = client_a.post("/api/v1/finance/expense-categories", json={"name": "Rent"}).json()
        e = client_a.post(
            "/api/v1/finance/expenses",
            json={
                "expense_date": TODAY.isoformat(),
                "category_id": cat["id"],
                "amount": "10.00",
                "payment_method": "CASH",
            },
        ).json()
        client_a.post(f"/api/v1/finance/expenses/{e['id']}/submit")
        client_a.post(f"/api/v1/finance/expenses/{e['id']}/post")
        client_a.post(f"/api/v1/finance/expenses/{e['id']}/void", json={"reason": "Oops"})
        client_a.post(
            "/api/v1/finance/cash/counts",
            json={"count_date": (TODAY - timedelta(days=1)).isoformat(), "actual_cash": "5.00"},
        )
        client_a.post(
            "/api/v1/finance/adjustments",
            json={
                "direction": "IN",
                "amount": "1.00",
                "payment_method": "CASH",
                "entry_date": TODAY.isoformat(),
                "note": "x",
            },
        )
        p = client_a.post(
            "/api/v1/finance/periods",
            json={
                "period_start": (TODAY - timedelta(days=60)).isoformat(),
                "period_end": (TODAY - timedelta(days=50)).isoformat(),
            },
        ).json()
        client_a.post(f"/api/v1/finance/periods/{p['id']}/lock")
        client_a.post(f"/api/v1/finance/periods/{p['id']}/unlock")
        actions = set(session.scalars(select(AuditLog.action)))
        assert {
            "expense_category_created",
            "expense_created",
            "expense_submitted",
            "expense_posted",
            "expense_voided",
            "cash_counted",
            "finance_entry_recorded",
            "finance_entry_reversed",
            "period_created",
            "period_locked",
            "period_unlocked",
        } <= actions

    def test_audit_rows_carry_no_credentials_or_tokens(self, session, tenant_a, client_a):
        client_a.put(
            "/api/v1/finance/tax/settings",
            json={"tax_type": "GST", "registration_number": "27ABCDE1234F1Z5", "prices_include_tax": True},
        )
        text = " ".join(str(r.after_json) + str(r.before_json) for r in session.scalars(select(AuditLog)))
        assert not re.search(r"password|secret|token|api[_-]?key", text, re.I)
