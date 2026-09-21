"""Quick/Daily Sales: money-only entries. Lifecycle, khata, no stock or cost, isolation, exports, migration."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, CustomerLedgerEntry, DocumentSequence, InventoryTransaction, QuickSale, Sale
from app.models.enums import UserRole
from app.services import khata_service
from tests.factories import today_in_shop_timezone
from tests.test_business_types import REQUESTED_TYPES
from tests.test_exports import INJECTIONS, read_csv, read_xlsx
from tests.test_purchases_api import make_product, make_supplier, stock
from tests.test_sales_api import CUSTOMERS, make_customer, owed, stock_up

API = "/api/v1/quick-sales"
EXPORTS = "/api/v1/exports"


def draft(client, gross="500", **fields):
    response = client.post(API, json={"gross_amount": gross, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def post(client, entry, **payment):
    return client.post(f"{API}/{entry['id']}/post", json=payment or {"payment_method": "CASH"})


def sold(client, gross="500", *, header=None, **payment):
    payment.setdefault("payment_method", "CASH")
    response = post(client, draft(client, gross, **(header or {})), **payment)
    assert response.status_code == 200, response.text
    return response.json()


def errors(response) -> dict[str, str]:
    return {".".join(str(p) for p in i["loc"][1:]): i["msg"] for i in response.json()["detail"]}


class TestDraft:
    def test_a_draft_is_an_entry_that_changes_nothing(self, client_a, fresh):
        entry = draft(client_a, "1200", note="Morning counter")

        assert entry["status"] == "DRAFT" and entry["quick_no"] is None
        assert (entry["gross_amount"], entry["discount"], entry["total_amount"]) == (
            "1200.00",
            "0.00",
            "1200.00",
        )
        assert entry["payment_type"] is None and entry["amount_paid"] is None
        assert (
            entry["sale_date"] == today_in_shop_timezone().isoformat()
            and entry["created_by_name"] == "Test Owner"
        )
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(CustomerLedgerEntry))) == 0
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(DocumentSequence))) == 0

    def test_a_transaction_level_discount_is_recorded_as_such(self, client_a):
        entry = draft(client_a, "1000", discount="100")
        assert (entry["gross_amount"], entry["discount"], entry["total_amount"]) == (
            "1000.00",
            "100.00",
            "900.00",
        )

    def test_a_draft_can_be_edited(self, client_a):
        c = make_customer(client_a)
        entry = draft(client_a, "100")

        edited = client_a.patch(
            f"{API}/{entry['id']}",
            json={"gross_amount": "300", "discount": "50", "customer_id": c["id"], "note": " Sweets "},
        )

        assert edited.status_code == 200
        data = edited.json()
        assert (data["gross_amount"], data["total_amount"], data["customer_name"], data["note"]) == (
            "300.00",
            "250.00",
            "Ramesh",
            "Sweets",
        )
        cleared = client_a.patch(f"{API}/{entry['id']}", json={"customer_id": None, "discount": None}).json()
        assert cleared["customer_id"] is None and cleared["total_amount"] == "300.00"

    def test_editing_only_the_amount_keeps_the_discount(self, client_a):
        entry = draft(client_a, "500", discount="20")
        assert (
            client_a.patch(f"{API}/{entry['id']}", json={"gross_amount": "800"}).json()["total_amount"]
            == "780.00"
        )

    def test_a_discount_must_be_less_than_the_amount(self, client_a):
        response = client_a.post(API, json={"gross_amount": "100", "discount": "100"})
        assert response.status_code == 422 and "discount" in errors(response)
        entry = draft(client_a, "100")
        assert client_a.patch(f"{API}/{entry['id']}", json={"discount": "150"}).status_code == 422

    @pytest.mark.parametrize("amount", ["0", "-5", "abc", "1.005", ""])
    def test_bad_amounts_are_refused(self, client_a, amount):
        assert client_a.post(API, json={"gross_amount": amount}).status_code == 422

    def test_the_amount_is_required_and_must_be_text(self, client_a):
        assert client_a.post(API, json={}).status_code == 422
        assert client_a.post(API, json={"gross_amount": 500.5}).status_code == 422

    def test_a_future_date_is_refused_and_a_past_one_accepted(self, client_a):
        tomorrow = (today_in_shop_timezone() + timedelta(days=1)).isoformat()
        assert client_a.post(API, json={"gross_amount": "5", "sale_date": tomorrow}).status_code == 422
        assert draft(client_a, "5", sale_date="2026-01-15")["sale_date"] == "2026-01-15"

    def test_there_is_no_way_to_add_products_or_a_product_discount(self, client_a):
        for extra in (
            {"items": []},
            {"product_id": 1},
            {"quantity": "1"},
            {"promotion_id": 1},
            {"coupon_code": "X"},
            {"total_amount": "1"},
            {"status": "POSTED"},
        ):
            assert client_a.post(API, json={"gross_amount": "5", **extra}).status_code == 422, extra

    def test_the_customer_must_belong_to_the_shop(self, client_a, client_b):
        theirs = make_customer(client_b, "Theirs")
        response = client_a.post(API, json={"gross_amount": "5", "customer_id": theirs["id"]})
        assert response.status_code == 422 and "customer_id" in errors(response)

    def test_there_is_no_delete(self, client_a):
        assert client_a.delete(f"{API}/{draft(client_a)['id']}").status_code == 405


class TestPosting:
    def test_posting_numbers_the_entry_and_settles_the_payment(self, client_a):
        entry = sold(client_a, "750", payment_method="UPI", payment_reference=" UPI-9 ")

        year = today_in_shop_timezone()
        start = year.year if year.month >= 4 else year.year - 1
        assert entry["status"] == "POSTED" and entry["quick_no"] == f"QS/{start}-{(start + 1) % 100:02d}/0001"
        assert (
            entry["payment_type"],
            entry["amount_paid"],
            entry["payment_method"],
            entry["payment_reference"],
        ) == ("PAID", "750.00", "UPI", "UPI-9")
        assert (
            entry["posted_at"]
            and entry["posted_by_name"] == "Test Owner"
            and entry["credit_amount"] == "0.00"
        )

    def test_a_method_is_required_when_money_is_received(self, client_a):
        entry = draft(client_a)
        response = client_a.post(f"{API}/{entry['id']}/post")
        assert response.status_code == 422 and "payment_method" in errors(response)
        assert client_a.get(f"{API}/{entry['id']}").json()["status"] == "DRAFT"

    def test_numbers_run_in_sequence_and_only_posting_uses_one(self, client_a):
        draft(client_a)
        numbers = [sold(client_a)["quick_no"] for _ in range(3)]
        assert [n.rsplit("/", 1)[1] for n in numbers] == ["0001", "0002", "0003"]

    def test_quick_sale_and_detailed_sale_numbers_are_separate(self, client_a):
        assert sold(client_a)["quick_no"].startswith("QS/")

    def test_an_entry_cannot_be_posted_twice(self, client_a):
        entry = draft(client_a)
        assert post(client_a, entry).status_code == 200
        again = post(client_a, entry)
        assert again.status_code == 409 and "already been posted" in again.json()["detail"][0]["msg"]

    def test_a_posted_entry_cannot_be_edited(self, client_a):
        entry = sold(client_a, "100")
        response = client_a.patch(f"{API}/{entry['id']}", json={"gross_amount": "999"})
        assert (
            response.status_code == 409
            and client_a.get(f"{API}/{entry['id']}").json()["gross_amount"] == "100.00"
        )

    def test_posting_is_audited(self, client_a, fresh):
        entry = sold(client_a, "100")
        log = fresh(
            lambda s: s.scalars(
                select(AuditLog).where(AuditLog.entity_type == "quick_sale", AuditLog.action == "post")
            ).one()
        )
        assert (
            log.entity_id == entry["id"]
            and log.before_json["status"] == "DRAFT"
            and log.after_json["status"] == "POSTED"
        )

    def test_posting_needs_a_positive_total_after_the_discount(self, client_a):
        assert sold(client_a, "100", header={"discount": "99.99"})["total_amount"] == "0.01"


class TestCreditAndKhata:
    def test_a_part_payment_is_a_credit_sale_on_the_khata(self, client_a):
        c = make_customer(client_a)
        entry = sold(client_a, "1000", header={"customer_id": c["id"]}, amount_paid="700")

        assert (entry["payment_type"], entry["amount_paid"], entry["credit_amount"]) == (
            "CREDIT",
            "700.00",
            "300.00",
        )
        assert owed(client_a, c) == Decimal("300.00")
        ledger = client_a.get(f"{CUSTOMERS}/{c['id']}/ledger").json()["items"][0]
        assert (ledger["entry_type"], ledger["amount_delta"]) == ("CREDIT_SALE", "300.00")
        assert (ledger["reference_type"], ledger["reference_id"], ledger["reference_no"]) == (
            "QUICK_SALE",
            entry["id"],
            entry["quick_no"],
        )

    def test_the_credit_is_on_the_amount_after_the_discount(self, client_a):
        c = make_customer(client_a)
        entry = sold(client_a, "1000", header={"customer_id": c["id"], "discount": "100"}, amount_paid="0")
        assert entry["total_amount"] == "900.00" and owed(client_a, c) == Decimal("900.00")

    def test_a_fully_paid_entry_touches_no_khata(self, client_a, fresh):
        c = make_customer(client_a)
        sold(client_a, "500", header={"customer_id": c["id"]})
        assert owed(client_a, c) == 0
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(CustomerLedgerEntry))) == 0

    def test_credit_needs_a_customer(self, client_a):
        entry = draft(client_a, "500")
        response = post(client_a, entry, amount_paid="100", payment_method="CASH")
        assert response.status_code == 422 and "customer_id" in errors(response)

    def test_paying_more_than_the_total_is_refused_not_lost(self, client_a):
        c = make_customer(client_a)
        entry = draft(client_a, "500", customer_id=c["id"])
        response = post(client_a, entry, amount_paid="600", payment_method="CASH")
        assert response.status_code == 422 and "advance" in errors(response)["amount_paid"]

    def test_an_inactive_customer_cannot_be_given_credit_and_nothing_is_written(self, client_a, fresh):
        c = make_customer(client_a)
        entry = draft(client_a, "500", customer_id=c["id"])
        client_a.post(f"{CUSTOMERS}/{c['id']}/deactivate")

        response = post(client_a, entry, amount_paid="0")

        assert (
            response.status_code == 409 and client_a.get(f"{API}/{entry['id']}").json()["status"] == "DRAFT"
        )
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(DocumentSequence))) == 0

    def test_a_failure_charging_the_khata_rolls_the_posting_back(self, client_a, fresh, monkeypatch):
        c = make_customer(client_a)
        entry = draft(client_a, "500", customer_id=c["id"])
        monkeypatch.setattr(
            khata_service, "record_credit_sale", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError):
            post(client_a, entry, amount_paid="0")

        assert client_a.get(f"{API}/{entry['id']}").json()["status"] == "DRAFT"
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(DocumentSequence))) == 0
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(CustomerLedgerEntry))) == 0

    def test_a_khata_entry_from_a_quick_sale_cannot_be_reversed_by_hand(self, client_a):
        c = make_customer(client_a)
        sold(client_a, "500", header={"customer_id": c["id"]}, amount_paid="0")
        entry_id = client_a.get(f"{CUSTOMERS}/{c['id']}/ledger").json()["items"][0]["id"]
        response = client_a.post(f"{CUSTOMERS}/{c['id']}/ledger/{entry_id}/reverse", json={"reason": "x"})
        assert response.status_code == 409 and owed(client_a, c) == Decimal("500.00")


class TestVoid:
    def test_voiding_a_credit_entry_reverses_the_khata_and_keeps_the_record(self, client_a):
        c = make_customer(client_a)
        entry = sold(client_a, "400", header={"customer_id": c["id"]}, amount_paid="100")
        assert owed(client_a, c) == Decimal("300.00")

        response = client_a.post(f"{API}/{entry['id']}/void", json={"reason": "Wrong customer"})

        assert response.status_code == 200
        data = response.json()
        assert (
            data["status"] == "VOID"
            and data["void_reason"] == "Wrong customer"
            and data["quick_no"] == entry["quick_no"]
        )
        assert owed(client_a, c) == 0
        kinds = [e["entry_type"] for e in client_a.get(f"{CUSTOMERS}/{c['id']}/ledger").json()["items"]]
        assert kinds == ["REVERSAL", "CREDIT_SALE"]  # both stay in the history

    def test_voiding_a_fully_paid_entry_touches_no_khata(self, client_a, fresh):
        entry = sold(client_a, "100")
        assert client_a.post(f"{API}/{entry['id']}/void", json={"reason": "x"}).status_code == 200
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(CustomerLedgerEntry))) == 0

    def test_a_reason_is_required(self, client_a):
        entry = sold(client_a)
        url = f"{API}/{entry['id']}/void"
        assert client_a.post(url, json={}).status_code == 422
        assert client_a.post(url, json={"reason": "  "}).status_code == 422
        assert client_a.get(f"{API}/{entry['id']}").json()["status"] == "POSTED"

    def test_a_void_entry_cannot_be_voided_or_posted_again(self, client_a):
        entry = sold(client_a)
        client_a.post(f"{API}/{entry['id']}/void", json={"reason": "x"})
        assert client_a.post(f"{API}/{entry['id']}/void", json={"reason": "y"}).status_code == 409
        assert post(client_a, entry).status_code == 409

    def test_voiding_a_draft_discards_it_and_uses_no_number(self, client_a):
        entry = draft(client_a)
        response = client_a.post(f"{API}/{entry['id']}/void", json={"reason": "Not needed"})
        assert response.status_code == 200 and response.json()["quick_no"] is None
        assert sold(client_a)["quick_no"].endswith("/0001")

    def test_voiding_is_audited(self, client_a, fresh):
        entry = sold(client_a)
        client_a.post(f"{API}/{entry['id']}/void", json={"reason": "Wrong"})
        log = fresh(
            lambda s: s.scalars(
                select(AuditLog).where(AuditLog.action == "void", AuditLog.entity_type == "quick_sale")
            ).one()
        )
        assert log.after_json["void_reason"] == "Wrong"


class TestMoneyOnly:
    """A quick sale never touches stock and never has a product-level cost or profit."""

    def test_it_has_no_inventory_effect(self, client_a, tenant_a, units, fresh):
        supplier = make_supplier(client_a)
        rice = make_product(client_a, tenant_a, units, "RICE", selling_price="50")
        stock_up(client_a, supplier, rice, 10, 20)
        before = fresh(lambda s: s.scalar(select(func.count()).select_from(InventoryTransaction)))

        entry = sold(client_a, "5000")
        client_a.post(f"{API}/{entry['id']}/void", json={"reason": "x"})

        assert stock(client_a, rice) == 10
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(InventoryTransaction))) == before

    def test_profit_is_always_not_available(self, client_a):
        entry = sold(client_a, "5000")
        assert entry["gross_profit"] is None and entry["profit_label"] == "Not Available"
        assert draft(client_a)["profit_label"] == "Not Available"
        assert client_a.get(f"{API}/{entry['id']}").json()["gross_profit"] is None

    def test_the_table_has_no_product_quantity_or_cost_columns(self):
        names = set(QuickSale.__table__.columns.keys())
        forbidden = {"product_id", "quantity", "unit_cost", "cogs_amount", "line_total", "items", "sale_id"}
        assert names.isdisjoint(forbidden)

    def test_the_service_does_not_use_inventory_at_all(self):
        import ast
        from pathlib import Path

        tree = ast.parse(
            (Path(__file__).resolve().parent.parent / "app/services/quick_sale_service.py").read_text()
        )
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported |= {node.module or ""} | {a.name for a in node.names}
            elif isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
        assert not {n for n in imported if "inventory" in n or "costing" in n or "InventoryTransaction" in n}

    def test_a_quick_sale_is_not_a_detailed_sale(self, client_a, fresh):
        sold(client_a, "100")
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(Sale))) == 0
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(QuickSale))) == 1
        assert client_a.get("/api/v1/sales").json()["total"] == 0


class TestListAndSearch:
    @pytest.fixture
    def three(self, client_a):
        asha = make_customer(client_a, "Asha Devi", phone="9000000001")
        a = sold(client_a, "100", header={"sale_date": "2026-01-10"})
        b = sold(
            client_a,
            "200",
            header={"customer_id": asha["id"], "sale_date": "2026-02-10", "note": "festival"},
            amount_paid="0",
        )
        c = draft(client_a, "300", sale_date="2026-03-10")
        return asha, a, b, c

    def ids(self, client, **params):
        response = client.get(API, params=params)
        assert response.status_code == 200, response.text
        return [e["id"] for e in response.json()["items"]]

    def test_newest_first_with_amounts(self, client_a, three):
        _, a, b, c = three
        data = client_a.get(API).json()
        assert [e["id"] for e in data["items"]] == [c["id"], b["id"], a["id"]] and data["total"] == 3
        row = data["items"][1]
        assert (row["total_amount"], row["payment_type"], row["customer_name"]) == (
            "200.00",
            "CREDIT",
            "Asha Devi",
        )

    def test_search_and_filters(self, client_a, three):
        asha, a, b, c = three
        assert self.ids(client_a, q=a["quick_no"]) == [a["id"]]
        assert self.ids(client_a, q="asha") == [b["id"]] and self.ids(client_a, q="90000 00001") == [b["id"]]
        assert self.ids(client_a, q="festival") == [b["id"]] and self.ids(client_a, q="%") == []
        assert self.ids(client_a, status="DRAFT") == [c["id"]] and self.ids(
            client_a, payment_type="CREDIT"
        ) == [b["id"]]
        assert self.ids(client_a, customer_id=asha["id"]) == [b["id"]]
        assert self.ids(client_a, date_from="2026-02-01") == [c["id"], b["id"]]
        assert self.ids(client_a, date_to="2026-01-31") == [a["id"]]
        assert client_a.get(API, params={"status": "BOGUS"}).status_code == 422
        assert (
            client_a.get(API, params={"date_from": "2026-05-01", "date_to": "2026-01-01"}).status_code == 422
        )

    def test_pagination(self, client_a, three):
        page = client_a.get(API, params={"limit": 2, "offset": 1}).json()
        assert len(page["items"]) == 2 and page["total"] == 3
        assert client_a.get(API, params={"limit": 201}).status_code == 422


class TestTenantIsolation:
    def test_another_shops_entry_is_not_found_by_any_endpoint(self, client_a, client_b):
        entry = sold(client_a)
        url = f"{API}/{entry['id']}"
        responses = [
            client_b.get(url),
            client_b.patch(url, json={"note": "x"}),
            client_b.post(f"{url}/post"),
            client_b.post(f"{url}/void", json={"reason": "x"}),
        ]
        assert [r.status_code for r in responses] == [404] * 4
        assert client_a.get(url).json()["status"] == "POSTED"

    def test_lists_never_show_another_shops_entries_and_numbering_is_independent(self, client_a, client_b):
        mine, theirs = sold(client_a), sold(client_b)
        assert mine["quick_no"] == theirs["quick_no"]  # each shop's own 0001
        assert client_b.get(API).json()["total"] == 1

    def test_a_quick_sale_cannot_use_another_shops_customer_even_in_the_database(
        self, session, tenant_a, tenant_b
    ):
        from app.models.enums import SaleStatus
        from tests import factories
        from tests.conftest import assert_rejected

        foreign = factories.make_customer(session, tenant_b.shop)
        session.commit()
        entry = QuickSale(
            shop_id=tenant_a.shop.id, status=SaleStatus.DRAFT, sale_date=date.today(), gross_amount=Decimal("5"),
            total_amount=Decimal("5"), customer_id=foreign.id, created_by=tenant_a.user.id,
        )  # fmt: skip
        assert_rejected(session, entry, match="FOREIGN KEY")


class TestRacesAndPlans:
    def race(self, clients, request):
        barrier = threading.Barrier(len(clients))

        def run(client):
            barrier.wait()
            return request(client).status_code

        with ThreadPoolExecutor(len(clients)) as pool:
            return sorted(pool.map(run, clients))

    @pytest.mark.parametrize("attempt", range(3))
    def test_two_simultaneous_posts_of_one_entry_post_it_once(
        self, client_a, make_client, tenant_a, fresh, attempt
    ):
        c = make_customer(client_a, f"Racer {attempt}")
        entry = draft(client_a, "500", customer_id=c["id"])
        clients = [make_client(tenant_a) for _ in range(3)]

        codes = self.race(clients, lambda cl: post(cl, entry, amount_paid="0"))

        assert codes == [200, 409, 409] and owed(client_a, c) == Decimal("500.00")

    def test_quick_sales_count_against_the_monthly_invoice_allowance(
        self, client_a, tenant_a, session_factory
    ):
        from app.services import entitlement_service

        with session_factory() as s, s.begin():
            entitlement_service.set_plan_entries(s, "free", limits={"max_monthly_invoices": 1})
        sold(client_a, "100")
        entry = draft(client_a, "100")
        refused = post(client_a, entry)
        assert refused.status_code == 403 and refused.json()["detail"][0]["feature"] == "max_monthly_invoices"
        assert client_a.get(f"{API}/{entry['id']}").json()["status"] == "DRAFT"


class TestEveryBusinessTypeAndRole:
    @pytest.mark.parametrize("business_type", REQUESTED_TYPES)
    def test_quick_sales_work_for_every_business_type(self, make_client, tenant_of, business_type):
        client = make_client(tenant_of(business_type))
        buyer = make_customer(client, "Buyer")
        entry = sold(client, "1000", header={"customer_id": buyer["id"]}, amount_paid="400")
        assert entry["status"] == "POSTED" and owed(client, buyer) == Decimal("600.00")

    def test_a_staff_user_can_record_a_quick_sale(self, make_client, tenant_a):
        assert sold(make_client(tenant_a, role=UserRole.STAFF))["status"] == "POSTED"


class TestExports:
    @pytest.fixture
    def data(self, client_a):
        asha = make_customer(client_a, "Asha Devi")
        a = sold(
            client_a,
            "1000",
            header={"sale_date": "2026-01-10", "discount": "100", "note": "counter"},
            payment_method="UPI",
            payment_reference="UPI-1",
        )
        b = sold(
            client_a, "400", header={"customer_id": asha["id"], "sale_date": "2026-02-10"}, amount_paid="100"
        )
        c = draft(client_a, "50", sale_date="2026-03-10")
        return a, b, c

    def rows(self, response):
        table = read_csv(response.content)
        return [dict(zip(table[0], row, strict=True)) for row in table[1:]]

    def test_csv_rows_and_the_profit_column(self, client_a, data):
        a, b, c = data
        response = client_a.get(f"{EXPORTS}/quick-sales")

        assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"].startswith('attachment; filename="quick_sales_')
        assert response.headers["cache-control"] == "no-store" and response.content.startswith("﻿".encode())
        by_no = {r["Quick Sale No"]: r for r in self.rows(response)}
        first, second = by_no[a["quick_no"]], by_no[b["quick_no"]]
        assert (
            first["Amount"],
            first["Discount"],
            first["Total"],
            first["Payment Method"],
            first["Payment Reference"],
        ) == ("1000.00", "100.00", "900.00", "UPI", "UPI-1")
        assert (second["Payment"], second["On Khata (Credit)"], second["Customer"]) == (
            "Credit",
            "300.00",
            "Asha Devi",
        )
        assert {r["Profit"] for r in by_no.values()} == {"Not Available"}
        assert by_no[""]["Status"] == "Draft"

    def test_xlsx_has_real_dates_and_numbers(self, client_a, data):
        sheet = read_xlsx(
            client_a.get(f"{EXPORTS}/quick-sales", params={"format": "xlsx", "status": "POSTED"}).content
        )
        header = [c.value for c in sheet[1]]
        newest = dict(zip(header, sheet[2], strict=True))
        assert (
            newest["Date"].value.date() == date(2026, 2, 10) and newest["Date"].number_format == "dd/mm/yyyy"
        )
        assert float(newest["Total"].value) == 400.0 and newest["Total"].number_format == "#,##0.00"

    def test_filters(self, client_a, data):
        assert len(self.rows(client_a.get(f"{EXPORTS}/quick-sales", params={"status": "DRAFT"}))) == 1
        assert len(self.rows(client_a.get(f"{EXPORTS}/quick-sales", params={"payment_type": "CREDIT"}))) == 1
        assert len(self.rows(client_a.get(f"{EXPORTS}/quick-sales", params={"date_from": "2026-02-01"}))) == 2

    @pytest.mark.parametrize("fmt", ["csv", "xlsx"])
    @pytest.mark.parametrize("payload", INJECTIONS[:5])
    def test_formula_like_text_is_neutralised(self, client_a, payload, fmt):
        buyer = make_customer(client_a, payload)
        sold(
            client_a,
            "100",
            header={"customer_id": buyer["id"], "note": payload},
            payment_method="UPI",
            payment_reference=payload[:100],
        )
        content = client_a.get(f"{EXPORTS}/quick-sales", params={"format": fmt}).content
        if fmt == "csv":
            cells = [c for row in read_csv(content) for c in row]
        else:
            cells = [
                str(c.value) for row in read_xlsx(content).iter_rows() for c in row if c.value is not None
            ]
        assert [c for c in cells if c.startswith(("=", "+", "-", "@", "\t", "\r"))] == []
        assert "'" + payload in "\n".join(cells)

    def test_scope_and_owner_only(self, client_a, client_b, make_client, tenant_a, data):
        sold(client_b, "777")
        text = client_a.get(
            f"{EXPORTS}/quick-sales", params={"status": ["POSTED", "DRAFT", "VOID"]}
        ).content.decode("utf-8-sig")
        assert "777" not in text
        assert len(self.rows(client_b.get(f"{EXPORTS}/quick-sales"))) == 1
        assert make_client(tenant_a, role=UserRole.STAFF).get(f"{EXPORTS}/quick-sales").status_code == 403


class TestMigration0008:
    NOW = "'2026-09-01 10:00:00.000000'"

    def test_existing_quick_sales_are_numbered_and_keep_their_data(self, tmp_path):
        from alembic import command
        from sqlalchemy import text

        from app.db.engine import create_db_engine
        from tests.conftest import alembic_config, sqlite_url

        url = sqlite_url(tmp_path / "m.db")
        command.upgrade(alembic_config(url), "0007")
        engine = create_db_engine(url)
        with engine.begin() as c:
            c.execute(
                text(
                    f"INSERT INTO shops (id, name, business_type, phone, address, mrp_validation_mode, created_at, updated_at) VALUES (7, 'S', 'BAKERY', '9', 'x', 'WARN', {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    f"INSERT INTO users (id, shop_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES (3, 7, 'o@x.l', '!', 'O', 'OWNER', 1, {self.NOW}, {self.NOW})"
                )
            )
            c.execute(
                text(
                    f"INSERT INTO quick_sales (id, shop_id, sale_date, total_amount, payment_type, amount_paid, payment_method, created_by, status, created_at, updated_at) VALUES (5, 7, '2026-08-30', 250000, 'PAID', 250000, 'CASH', 3, 'POSTED', '2026-08-30 09:00:00.000000', {self.NOW})"
                )
            )
        engine.dispose()

        command.upgrade(alembic_config(url), "head")

        engine = create_db_engine(url)
        with engine.connect() as c:
            row = c.execute(
                text(
                    "SELECT status, quick_no, gross_amount, discount, total_amount, posted_by FROM quick_sales WHERE id = 5"
                )
            ).one()
            assert tuple(row) == ("POSTED", "QS/LEGACY/5", 250000, 0, 250000, 3)
            assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            columns = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(quick_sales)")}
            assert not columns & {"product_id", "quantity", "unit_cost", "cogs_amount"}
        engine.dispose()
        command.check(alembic_config(url))
        command.downgrade(alembic_config(url), "0007")
        command.upgrade(alembic_config(url), "head")
