"""A real restore drill: back up a shop that has done business, change the shop afterwards, restore, and prove the restored system is whole
and keeps working: integrity checks pass, the data is exactly as of the backup, the migration state is current, and new business posts
correctly (stock, khata and books all still agree)."""

import gc
import sqlite3
from decimal import Decimal

from app.reporting import integrity
from app.services import restore_service
from tests.test_phase11_backup import env, make  # noqa: F401
from tests.test_purchases_api import stock
from tests.test_sales_api import CUSTOMERS, make_customer, owed, shelf, sold  # noqa: F401

SALES = "/api/v1/sales"


def test_restore_drill(env, client_a, shelf, session_factory, db_url, engine, session):  # noqa: F811
    rice = shelf["rice"]
    customer = make_customer(client_a, opening_balance="500")
    sold(
        client_a,
        [{"product_id": rice["id"], "quantity": "2"}],
        header={"customer_id": customer["id"]},
        amount_paid="0",
    )
    assert stock(client_a, rice) == 8 and owed(client_a, customer) == Decimal("600.00")
    _, key = make(session_factory)  # the backup

    # business carries on after the backup...
    sold(client_a, [{"product_id": rice["id"], "quantity": "5"}])
    client_a.post(f"{CUSTOMERS}/{customer['id']}/payments", json={"amount": "100", "payment_method": "CASH"})
    assert stock(client_a, rice) == 3 and owed(client_a, customer) == Decimal("500.00")

    # ...then disaster: restore the backup (application stopped: nothing holds the database)
    from tests.test_phase11_backup import TestRestore

    record = TestRestore.rec(None, session_factory, key)
    session.close()
    gc.collect()
    engine.dispose()
    result = restore_service.restore(
        record, confirmation=restore_service.confirmation_phrase(key), actor="drill@test", via_api=False
    )
    assert result.ok, result.message

    live = db_url.removeprefix("sqlite:///")
    con = sqlite3.connect(live)
    assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    from tests.test_migrations import HEAD

    assert con.execute("SELECT version_num FROM alembic_version").fetchone() == (
        HEAD,
    )  # the restored database is at the current schema
    con.close()

    # the data is exactly what it was at the backup, through the application
    assert stock(client_a, rice) == 8
    assert owed(client_a, customer) == Decimal("600.00")
    with session_factory() as s:
        findings = integrity.run(s)
    assert findings == [], findings  # the ledger checks (stock = ledger, khata = ledger, sale totals...) all hold on the restored data

    # and business keeps working on the restored system: stock, khata and the sale all agree
    sold(
        client_a,
        [{"product_id": rice["id"], "quantity": "1"}],
        header={"customer_id": customer["id"]},
        amount_paid="0",
    )
    assert stock(client_a, rice) == 7 and owed(client_a, customer) == Decimal("650.00")
