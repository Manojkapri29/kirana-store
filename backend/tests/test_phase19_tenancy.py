"""Multi-tenant audit, generic: every read the application makes on behalf of a shop names that shop in its SQL.

For every GET route (with sample ids) called as shop B while shop A also has data, each SELECT that touches a table owned by a shop must
mention `shop_id`. This is a structural check on the queries themselves, so a new route that forgets the shop filter fails here even if no
test thought to ask another shop for its data. Reference tables (units, business types, plans) are shared on purpose and exempt.
"""

import re
from datetime import timedelta

import pytest
from sqlalchemy import event

from app.core.permissions import ROUTE_RULES
from app.models import Base as ModelBase
from tests import factories
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import make_purchase, make_quick_sale, make_sale

TODAY = today_in_shop_timezone()
SHOP_TABLES = {t.name for t in ModelBase.metadata.tables.values() if "shop_id" in t.c}
# Tables whose reads legitimately do not filter by shop: accounts and sessions are looked up by their own secrets in the auth layer.
EXEMPT = {"accounts", "auth_sessions", "invitations"}
TABLE_REF = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_]+)", re.I)


def _seed(session, tenant, marker):
    c = factories.make_customer(session, tenant.shop, name=f"Cust {marker}", phone=f"9{marker:09d}")
    s = factories.make_supplier(session, tenant.shop, name=f"Supp {marker}")
    session.commit()
    make_sale(session, tenant, "100.00", day=TODAY, cogs="60.00", customer_id=c.id)
    make_quick_sale(session, tenant, "50.00", day=TODAY - timedelta(days=1))
    make_purchase(session, tenant, s, "70.00", day=TODAY)
    session.commit()


def test_every_shop_read_names_the_shop(session, tenant_a, tenant_b, client_b, engine):
    _seed(session, tenant_a, 1)
    _seed(session, tenant_b, 2)
    statements: list[tuple[str, str]] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append((current[0], statement))

    current = [""]
    paths = sorted({r.template for r in ROUTE_RULES if r.method == "GET"})
    assert len(paths) > 150
    for template in paths:
        url = "/api/v1" + re.sub(r"\{[^}]+\}", "1", template)
        current[0] = url
        client_b.get(url, params={"preset": "this_month", "date_from": TODAY.isoformat(), "date_to": TODAY.isoformat(), "kind": "fast", "level": "months", "months": 3})
    assert len(statements) > 400, len(statements)  # the guard really watched the application work
    violations = []
    checked = 0
    for url, sql in statements:
        if not sql.lstrip().upper().startswith("SELECT"):
            continue
        touched = {m.lower() for m in TABLE_REF.findall(sql)} & (SHOP_TABLES - EXEMPT)
        checked += bool(touched)
        if touched and "shop_id" not in sql:
            violations.append((url, sorted(touched), " ".join(sql.split())[:160]))
    assert checked > 300, checked
    assert not violations, "\n".join(map(str, violations[:15]))


def test_a_sweep_of_id_lookups_never_returns_the_other_shops_rows(session, tenant_a, tenant_b, client_a, client_b):
    """Ids from shop A asked for as shop B: 404 (or empty), never the record."""
    _seed(session, tenant_a, 1)
    ids = {}
    for name, path in (("products", "/products"), ("customers", "/customers"), ("suppliers", "/suppliers"), ("sales", "/sales"), ("quick-sales", "/quick-sales"), ("purchases", "/purchases")):
        items = client_a.get(f"/api/v1{path}", params={"limit": 5}).json().get("items", [])
        if items:
            ids[name] = items[0]["id"]
    assert {"customers", "suppliers", "sales", "quick-sales", "purchases"} <= set(ids)
    for name, rid in ids.items():
        assert client_b.get(f"/api/v1/{name}/{rid}").status_code == 404, name
    assert client_b.get(f"/api/v1/customers/{ids['customers']}/ledger").status_code in (404, 200)
    body = client_b.get(f"/api/v1/customers/{ids['customers']}/ledger")
    assert body.status_code == 404 or body.json().get("items", []) == []
    with pytest.raises(AssertionError):
        assert client_b.get(f"/api/v1/sales/{ids['sales']}").status_code == 200


def test_the_guard_itself_catches_a_missing_filter():
    sql = "SELECT sales.id FROM sales WHERE sales.id = ?"
    assert {m.lower() for m in TABLE_REF.findall(sql)} & SHOP_TABLES and "shop_id" not in sql
