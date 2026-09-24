"""Analytics RBAC: every analytics route needs an analytics permission AND the permission of the data it shows; system roles get
the documented defaults; a role without a permission is refused on every route of that family."""

import re

import pytest

from app.core.permissions import PERMISSIONS, ROUTE_RULES, SYSTEM_ROLES
from tests.client_helpers import client_with
from tests.factories import today_in_shop_timezone

TODAY = today_in_shop_timezone()
P = {"preset": "this_month", "compare": "none"}
ANALYTICS = [p for p in PERMISSIONS if p.startswith("ANALYTICS_")]
RULES = [
    r
    for r in ROUTE_RULES
    if r.template != "/analytics/overview"
    and r.template.startswith("/analytics")
    or r.template == "/scheduled-reports/advanced"
    or r.template == "/scheduled-reports/{report_id}/runs"
]


def _concrete(template: str) -> str:
    return re.sub(
        r"\{(\w+)\}",
        lambda m: "revenue" if m.group(1) == "level" else "1" if m.group(1) == "report_id" else "kpis",
        template,
    )


def test_the_six_analytics_permissions_exist():
    assert set(ANALYTICS) == {
        "ANALYTICS_VIEW",
        "ANALYTICS_ADVANCED",
        "ANALYTICS_EXECUTIVE",
        "ANALYTICS_CUSTOM_REPORT",
        "ANALYTICS_EXPORT",
        "ANALYTICS_SCHEDULE",
    }


def test_system_role_defaults():
    grants = {code: set(role[2]) for code, role in SYSTEM_ROLES.items()}
    everything = set(ANALYTICS)
    assert everything <= grants["OWNER"] and everything <= grants["MANAGER"]
    assert {"ANALYTICS_VIEW", "ANALYTICS_ADVANCED", "ANALYTICS_EXPORT"} <= grants["ACCOUNTANT"]
    assert not {"ANALYTICS_EXECUTIVE", "ANALYTICS_CUSTOM_REPORT", "ANALYTICS_SCHEDULE"} & grants["ACCOUNTANT"]
    for code, granted in grants.items():
        if code not in ("OWNER", "MANAGER", "ACCOUNTANT"):
            assert not set(ANALYTICS) & granted, code


@pytest.mark.parametrize("rule", RULES, ids=[f"{r.method} {r.template}" for r in RULES])
def test_every_analytics_route_needs_an_analytics_permission(rule):
    assert set(rule.needs) & set(ANALYTICS), rule.template


def test_data_routes_also_need_the_data_permission():
    by_prefix = {
        "/analytics/inventory/": "INVENTORY_VIEW",
        "/analytics/customers/": "CRM_ANALYTICS_VIEW",
        "/analytics/cohorts": "CRM_ANALYTICS_VIEW",
        "/analytics/suppliers/": "SUPPLIER_VIEW",
        "/analytics/finance/": "FINANCE_VIEW",
        "/analytics/sales/": "REPORT_VIEW",
        "/analytics/drill/inventory": "INVENTORY_VIEW",
        "/analytics/drill/finance": "FINANCE_VIEW",
        "/analytics/drill/expenses": "FINANCE_EXPENSE_VIEW",
        "/analytics/drill/suppliers": "SUPPLIER_VIEW",
        "/analytics/drill/customers": "CUSTOMER_VIEW",
    }
    for rule in RULES:
        for prefix, needed in by_prefix.items():
            if rule.template.startswith(prefix):
                assert needed in rule.needs, (rule.template, needed)


def test_finance_analytics_need_finance_permissions_and_customer_analytics_customer_data_permissions():
    finance = [r for r in RULES if r.template.startswith("/analytics/finance/")]
    assert finance and all("FINANCE_VIEW" in r.needs for r in finance)
    customers = [
        r
        for r in RULES
        if r.template.startswith(
            ("/analytics/customers/", "/analytics/cohorts", "/analytics/drill/customers")
        )
    ]
    assert customers and all("CRM_ANALYTICS_VIEW" in r.needs for r in customers)


def test_a_role_with_no_permissions_is_refused_on_every_analytics_get(session, tenant_a, make_client):
    nobody = client_with(make_client, tenant_a, [])
    for rule in RULES:
        if rule.method == "GET":
            assert nobody.get(f"/api/v1{_concrete(rule.template)}", params=P).status_code == 403, (
                rule.template
            )


def test_analytics_permission_alone_is_not_enough_for_data(session, tenant_a, make_client):
    only_analytics = client_with(make_client, tenant_a, ANALYTICS)
    for path in (
        "/analytics/inventory/stock",
        "/analytics/customers/overview",
        "/analytics/suppliers/spend",
        "/analytics/finance/summary",
        "/analytics/sales/summary",
    ):
        assert only_analytics.get(f"/api/v1{path}", params=P).status_code == 403, path
    # ...but the framework-level routes that need no data permission work
    assert only_analytics.get("/api/v1/analytics/kpis", params=P).status_code == 200
    assert only_analytics.get("/api/v1/analytics/executive", params=P).status_code == 200


def test_writes_need_the_custom_report_or_schedule_permission(session, tenant_a, make_client):
    viewer = client_with(
        make_client, tenant_a, ["ANALYTICS_VIEW", "ANALYTICS_ADVANCED", "ANALYTICS_EXPORT", "REPORT_VIEW"]
    )
    body = {"name": "x", "dataset": "sales", "definition": {}}
    assert viewer.post("/api/v1/analytics/reports", json=body).status_code == 403
    assert (
        viewer.post(
            "/api/v1/analytics/builder/preview", params=P, json={"dataset": "sales", "definition": {}}
        ).status_code
        == 403
    )
    assert (
        viewer.post(
            "/api/v1/scheduled-reports/advanced", json={"kind": "adv_kpis", "schedule": "DAILY"}
        ).status_code
        == 403
    )
    builder = client_with(make_client, tenant_a, ["ANALYTICS_CUSTOM_REPORT", "REPORT_VIEW"])
    assert builder.post("/api/v1/analytics/reports", json=body).status_code == 201


def test_there_is_no_http_delete_route_for_analytics():
    assert not [r for r in RULES if r.method == "DELETE"]
