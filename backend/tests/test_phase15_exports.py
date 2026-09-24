"""Finance exports reuse the existing export system: they need the finance view permission AND FINANCE_EXPORT."""

from datetime import timedelta

import pytest

from app.core.context import RequestContext
from app.core.permissions import ROUTE_RULES
from app.models.enums import UserRole
from tests import factories
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import make_purchase, make_quick_sale

TODAY = today_in_shop_timezone()
EXPORTS = ["finance-ledger", "expenses", "payables", "receivables"]


@pytest.mark.parametrize("name", EXPORTS)
def test_each_export_needs_both_permissions(name):
    rule = next(r for r in ROUTE_RULES if r.template == f"/exports/{name}")
    assert "FINANCE_EXPORT" in rule.needs and len(rule.needs) == 2


@pytest.mark.parametrize("name", EXPORTS)
def test_a_role_without_export_permission_is_forbidden(session, tenant_a, make_client, name):
    from fastapi.testclient import TestClient

    from app.api.deps import get_request_context

    client: TestClient = make_client(tenant_a)
    ctx = RequestContext(
        shop_id=tenant_a.shop.id,
        user_id=tenant_a.user.id,
        role=UserRole.STAFF,
        permissions=frozenset({"FINANCE_VIEW", "FINANCE_EXPENSE_VIEW"}),
    )
    client.app.dependency_overrides[get_request_context] = lambda: ctx
    assert client.get(f"/api/v1/exports/{name}").status_code == 403


def test_the_ledger_export_lists_source_documents_as_csv(session, tenant_a, client_a):
    q = make_quick_sale(session, tenant_a, "123.45", day=TODAY)
    session.commit()
    r = client_a.get(
        "/api/v1/exports/finance-ledger", params={"date_from": (TODAY - timedelta(days=1)).isoformat()}
    )
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    assert q.quick_no in r.text and "123.45" in r.text and "QUICK_SALE" in r.text


def test_payables_and_expenses_exports(session, tenant_a, client_a):
    supplier = factories.make_supplier(session, tenant_a.shop, "Sharma")
    make_purchase(session, tenant_a, supplier, "400.00", day=TODAY)
    session.commit()
    assert "Sharma" in client_a.get("/api/v1/exports/payables").text
    assert client_a.get("/api/v1/exports/expenses").status_code == 200
    assert client_a.get("/api/v1/exports/receivables").status_code == 200


def test_exports_never_include_another_shops_rows(session, tenant_a, tenant_b, client_b):
    q = make_quick_sale(session, tenant_a, "77.77", day=TODAY)
    session.commit()
    assert q.quick_no not in client_b.get("/api/v1/exports/finance-ledger").text
