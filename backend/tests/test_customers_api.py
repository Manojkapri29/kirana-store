"""Customers and khata API: CRUD, search, balances, payments, advance, adjustments, reversal, isolation."""

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Customer, CustomerLedgerEntry
from app.models.enums import CustomerLedgerEntryType as E
from app.models.enums import KhataReferenceType as Ref
from app.models.enums import UserRole
from app.services import khata_service as khata
from tests.conftest import context_for
from tests.test_business_types import REQUESTED_TYPES

API = "/api/v1/customers"


def create(client, **fields):
    response = client.post(API, json={"name": "Ramesh Kumar", **fields})
    assert response.status_code == 201, response.text
    return response.json()


def customer(client, **fields) -> dict:
    return create(client, **fields)["customer"]


def errors(response) -> dict[str, str]:
    return {".".join(str(p) for p in i["loc"][1:]): i["msg"] for i in response.json()["detail"]}


def pay(client, customer_id, amount, **extra):
    return client.post(f"{API}/{customer_id}/payments", json={"amount": amount, **extra})


def balance(client, customer_id) -> Decimal:
    return Decimal(client.get(f"{API}/{customer_id}/balance").json()["balance"])


def credit(session_factory, tenant, customer_id, amount, ref=1):
    """The way Phase 7 will add credit: through the service (there is no endpoint for it)."""
    with session_factory() as s, s.begin():
        khata.record_credit_sale(
            s, context_for(tenant), customer_id, Decimal(amount), reference_type=Ref.SALE, reference_id=ref
        )


class TestCreate:
    def test_only_a_name_is_needed(self, client_a):
        data = create(client_a)

        c = data["customer"]
        assert c["name"] == "Ramesh Kumar" and c["is_active"] is True
        assert all(c[k] is None for k in ("phone", "email", "address", "notes"))
        assert (c["balance"], c["outstanding"], c["advance"]) == ("0.00", "0.00", "0.00")
        assert c["balance_status"] == "SETTLED" and c["entry_count"] == 0
        assert data["warnings"] == [] and data["opening_entry"] is None

    def test_details_are_stored_in_a_consistent_form(self, client_a):
        c = customer(
            client_a,
            name="  Ramesh   Kumar ",
            phone="+91 98765-43210",
            email="  RAMESH@Example.COM ",
            address="12 Main Bazaar",
            notes="Pays on the 1st",
        )
        assert (c["name"], c["phone"], c["email"]) == ("Ramesh Kumar", "+919876543210", "ramesh@example.com")

    def test_an_opening_balance_can_be_given_and_is_a_ledger_entry(self, client_a):
        data = create(client_a, opening_balance="1000", opening_balance_date="2026-01-15")

        assert (
            data["customer"]["balance"] == "1000.00" and data["customer"]["balance_status"] == "OUTSTANDING"
        )
        entry = data["opening_entry"]
        assert entry["entry_type"] == "OPENING_BALANCE" and entry["amount_delta"] == "1000.00"
        assert entry["entry_date"] == "2026-01-15" and entry["balance_after"] == "1000.00"

    def test_a_bad_opening_balance_creates_no_customer(self, client_a, fresh):
        response = client_a.post(API, json={"name": "New", "opening_balance": "0"})

        assert response.status_code == 422
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(Customer))) == 0

    def test_validation_reports_every_bad_field(self, client_a):
        response = client_a.post(API, json={"name": "  ", "phone": "12", "email": "nope"})
        assert response.status_code == 422 and set(errors(response)) == {"name", "phone", "email"}

    def test_name_is_required(self, client_a):
        assert client_a.post(API, json={"phone": "9876543210"}).status_code == 422

    def test_unknown_fields_are_refused(self, client_a):
        assert client_a.post(API, json={"name": "A", "balance": "5"}).status_code == 422
        assert client_a.post(API, json={"name": "A", "is_active": False}).status_code == 422

    def test_a_phone_number_cannot_be_used_twice_in_one_shop(self, client_a):
        customer(client_a, name="First", phone="98765 43210")

        response = client_a.post(API, json={"name": "Second", "phone": "+91 9876543210".replace("+91 ", "")})

        assert response.status_code == 409 and "already belongs to 'First'" in errors(response)["phone"]

    def test_the_same_phone_is_fine_in_another_shop(self, client_a, client_b):
        customer(client_a, phone="9876543210")
        assert customer(client_b, phone="9876543210")["phone"] == "9876543210"

    def test_customers_without_a_phone_never_clash(self, client_a):
        customer(client_a, name="One")
        customer(client_a, name="Two")

    def test_a_repeated_name_warns_but_saves(self, client_a):
        customer(client_a, name="Ramesh Kumar")
        data = create(client_a, name="ramesh kumar")
        assert data["warnings"] == ["Another customer is also named 'Ramesh Kumar'."]

    def test_creation_is_audited(self, client_a, fresh):
        c = customer(client_a)
        log = fresh(
            lambda s: s.execute(select(AuditLog).where(AuditLog.entity_type == "customer")).scalars().one()
        )
        assert (
            log.action == "create" and log.entity_id == c["id"] and log.after_json["name"] == "Ramesh Kumar"
        )


class TestUpdateAndActivation:
    def test_only_the_fields_sent_change(self, client_a):
        c = customer(client_a, phone="9876543210", address="Old road")

        response = client_a.patch(f"{API}/{c['id']}", json={"address": "New road", "email": "a@b.com"})

        assert response.status_code == 200
        updated = response.json()["customer"]
        assert (updated["address"], updated["email"], updated["phone"]) == (
            "New road",
            "a@b.com",
            "9876543210",
        )

    def test_optional_fields_can_be_cleared(self, client_a):
        c = customer(client_a, email="a@b.com")
        cleared = client_a.patch(f"{API}/{c['id']}", json={"email": None, "notes": "  "}).json()["customer"]
        assert cleared["email"] is None and cleared["notes"] is None

    def test_the_name_cannot_be_blank(self, client_a):
        c = customer(client_a)
        assert client_a.patch(f"{API}/{c['id']}", json={"name": None}).status_code == 422
        assert client_a.patch(f"{API}/{c['id']}", json={"name": " "}).status_code == 422

    def test_changing_the_phone_to_a_used_one_is_refused(self, client_a):
        customer(client_a, name="A", phone="9000000001")
        b = customer(client_a, name="B", phone="9000000002")
        assert client_a.patch(f"{API}/{b['id']}", json={"phone": "9000000001"}).status_code == 409

    def test_saving_your_own_phone_again_is_fine(self, client_a):
        c = customer(client_a, phone="9000000001")
        assert (
            client_a.patch(f"{API}/{c['id']}", json={"phone": "90000 00001", "name": "Renamed"}).status_code
            == 200
        )

    def test_the_balance_cannot_be_edited(self, client_a):
        c = customer(client_a)
        assert client_a.patch(f"{API}/{c['id']}", json={"balance": "0"}).status_code == 422

    def test_an_edit_keeps_the_balance(self, client_a):
        c = customer(client_a, opening_balance="300")
        edited = client_a.patch(f"{API}/{c['id']}", json={"name": "Renamed"}).json()["customer"]
        assert edited["balance"] == "300.00"

    def test_deactivate_and_reactivate_keep_the_ledger(self, client_a):
        c = customer(client_a, opening_balance="250")

        off = client_a.post(f"{API}/{c['id']}/deactivate").json()
        on = client_a.post(f"{API}/{c['id']}/activate").json()

        assert off["is_active"] is False and off["balance"] == "250.00" and off["entry_count"] == 1
        assert on["is_active"] is True and on["balance"] == "250.00"

    def test_repeating_activation_is_harmless(self, client_a, fresh):
        c = customer(client_a)
        client_a.post(f"{API}/{c['id']}/deactivate")
        assert client_a.post(f"{API}/{c['id']}/deactivate").json()["is_active"] is False
        actions = fresh(
            lambda s: s.scalars(select(AuditLog.action).where(AuditLog.action == "deactivate")).all()
        )
        assert len(actions) == 1

    def test_an_inactive_customer_can_still_pay_but_gets_no_new_credit(
        self, client_a, tenant_a, session_factory
    ):
        c = customer(client_a, opening_balance="100")
        client_a.post(f"{API}/{c['id']}/deactivate")

        assert pay(client_a, c["id"], "100").status_code == 201
        with pytest.raises(Exception, match="inactive"):
            credit(session_factory, tenant_a, c["id"], "5")

    def test_there_is_no_delete(self, client_a):
        c = customer(client_a)
        assert client_a.delete(f"{API}/{c['id']}").status_code == 405


class TestListAndSearch:
    @pytest.fixture
    def people(self, client_a):
        asha = customer(
            client_a, name="Asha Devi", phone="9000000001", email="asha@shop.in", address="Gandhi Road"
        )
        bina = customer(client_a, name="Bina", phone="9000000002", opening_balance="800")
        chitra = customer(client_a, name="Chitra", phone="9000000003")
        pay(client_a, chitra["id"], "60")
        gone = customer(client_a, name="Dev")
        client_a.post(f"{API}/{gone['id']}/deactivate")
        return asha, bina, chitra, gone

    def names(self, client, **params):
        response = client.get(API, params=params)
        assert response.status_code == 200, response.text
        return [c["name"] for c in response.json()["items"]]

    def test_active_customers_by_name_with_balances(self, client_a, people):
        data = client_a.get(API).json()

        assert [c["name"] for c in data["items"]] == ["Asha Devi", "Bina", "Chitra"] and data["total"] == 3
        by_name = {c["name"]: c for c in data["items"]}
        assert by_name["Bina"]["balance"] == "800.00" and by_name["Bina"]["balance_status"] == "OUTSTANDING"
        assert by_name["Chitra"]["balance"] == "-60.00" and by_name["Chitra"]["advance"] == "60.00"
        assert (
            by_name["Chitra"]["balance_status"] == "ADVANCE"
            and by_name["Asha Devi"]["balance_status"] == "SETTLED"
        )

    def test_status_filter(self, client_a, people):
        assert self.names(client_a, status="inactive") == ["Dev"]
        assert self.names(client_a, status="all") == ["Asha Devi", "Bina", "Chitra", "Dev"]

    def test_search_by_name_phone_email_and_address(self, client_a, people):
        assert self.names(client_a, q="asha") == ["Asha Devi"]
        assert self.names(client_a, q="90000 00002") == ["Bina"]
        assert self.names(client_a, q="+919000000003".replace("+91", "")) == ["Chitra"]
        assert self.names(client_a, q="SHOP.IN") == ["Asha Devi"]
        assert self.names(client_a, q="gandhi") == ["Asha Devi"]
        assert self.names(client_a, q="nobody") == []

    def test_search_treats_wildcards_literally(self, client_a, people):
        assert self.names(client_a, q="%") == [] and self.names(client_a, q="_") == []

    def test_balance_filter_and_sort(self, client_a, people):
        assert self.names(client_a, balance="outstanding") == ["Bina"]
        assert self.names(client_a, balance="advance") == ["Chitra"]
        assert self.names(client_a, balance="settled") == ["Asha Devi"]
        assert self.names(client_a, sort="balance") == ["Bina", "Asha Devi", "Chitra"]
        assert client_a.get(API, params={"balance": "bogus"}).status_code == 422

    def test_filters_combine_and_total_reflects_them(self, client_a, people):
        data = client_a.get(API, params={"q": "a", "balance": "outstanding"}).json()
        assert data["total"] == 1 and data["items"][0]["name"] == "Bina"

    def test_pagination(self, client_a, people):
        page = client_a.get(API, params={"limit": 2, "offset": 2}).json()
        assert [c["name"] for c in page["items"]] == ["Chitra"] and page["total"] == 3
        assert client_a.get(API, params={"limit": 0}).status_code == 422
        assert client_a.get(API, params={"limit": 201}).status_code == 422

    def test_detail(self, client_a, people):
        _, bina, _, _ = people
        detail = client_a.get(f"{API}/{bina['id']}").json()
        assert detail["name"] == "Bina" and detail["balance"] == "800.00" and detail["entry_count"] == 1
        assert client_a.get(f"{API}/99999").status_code == 404


class TestOpeningBalanceEndpoint:
    def test_it_is_added_once(self, client_a):
        c = customer(client_a)

        first = client_a.post(
            f"{API}/{c['id']}/opening-balance", json={"amount": "1000", "note": "Old notebook"}
        )
        again = client_a.post(f"{API}/{c['id']}/opening-balance", json={"amount": "1000"})

        assert first.status_code == 201 and first.json()["balance"]["balance"] == "1000.00"
        assert first.json()["entry"]["note"] == "Old notebook"
        assert again.status_code == 409 and balance(client_a, c["id"]) == Decimal("1000.00")

    def test_a_customer_created_with_one_cannot_get_a_second(self, client_a):
        c = customer(client_a, opening_balance="10")
        assert client_a.post(f"{API}/{c['id']}/opening-balance", json={"amount": "10"}).status_code == 409

    @pytest.mark.parametrize("amount", ["0", "-5", "abc", "1.005", ""])
    def test_bad_amounts_are_refused(self, client_a, amount):
        c = customer(client_a)
        assert client_a.post(f"{API}/{c['id']}/opening-balance", json={"amount": amount}).status_code == 422

    def test_a_number_instead_of_text_is_refused(self, client_a):
        c = customer(client_a)
        assert client_a.post(f"{API}/{c['id']}/opening-balance", json={"amount": 10.5}).status_code == 422


class TestPaymentsEndpoint:
    def test_partial_full_and_over_payment(self, client_a):
        c = customer(client_a, opening_balance="5000")

        partial = pay(
            client_a, c["id"], "2000", payment_method="UPI", payment_reference="UPI-9", note="Counter"
        )
        assert partial.status_code == 201
        body = partial.json()
        assert body["balance"]["balance"] == "3000.00" and body["balance"]["status"] == "OUTSTANDING"
        assert body["entry"]["amount_delta"] == "-2000.00" and body["entry"]["entry_type"] == "PAYMENT"
        assert body["entry"]["payment_method"] == "UPI" and body["entry"]["payment_reference"] == "UPI-9"

        full = pay(client_a, c["id"], "3000").json()
        assert full["balance"]["balance"] == "0.00" and full["balance"]["status"] == "SETTLED"

        over = pay(client_a, c["id"], "1500").json()
        assert over["balance"]["balance"] == "-1500.00" and over["balance"]["status"] == "ADVANCE"
        assert over["balance"]["advance"] == "1500.00" and over["balance"]["outstanding"] == "0.00"

    def test_the_extra_is_not_discarded(self, client_a):
        c = customer(client_a, opening_balance="1000")
        pay(client_a, c["id"], "1500")
        assert balance(client_a, c["id"]) == Decimal("-500.00")
        assert client_a.get(f"{API}/{c['id']}").json()["advance"] == "500.00"

    @pytest.mark.parametrize("amount", ["0", "-1", "x", "1.234", ""])
    def test_bad_amounts_are_refused_and_nothing_is_recorded(self, client_a, amount):
        c = customer(client_a)
        assert pay(client_a, c["id"], amount).status_code == 422
        assert client_a.get(f"{API}/{c['id']}/ledger").json()["total"] == 0

    def test_a_future_date_is_refused(self, client_a):
        c = customer(client_a)
        response = pay(client_a, c["id"], "10", entry_date="2999-01-01")
        assert response.status_code == 422 and "entry_date" in errors(response)

    def test_an_unknown_method_is_refused(self, client_a):
        c = customer(client_a)
        assert pay(client_a, c["id"], "10", payment_method="BITCOIN").status_code == 422

    def test_a_payment_can_be_backdated(self, client_a):
        c = customer(client_a)
        assert (
            pay(client_a, c["id"], "10", entry_date="2026-02-01").json()["entry"]["entry_date"]
            == "2026-02-01"
        )


class TestAdjustmentsEndpoint:
    def test_increase_and_decrease(self, client_a):
        c = customer(client_a, opening_balance="100")

        up = client_a.post(
            f"{API}/{c['id']}/adjustments",
            json={"amount": "25", "direction": "INCREASE", "reason": "Missed charge"},
        )
        down = client_a.post(
            f"{API}/{c['id']}/adjustments",
            json={"amount": "50", "direction": "DECREASE", "reason": "Discount"},
        )

        assert up.status_code == 201 and up.json()["entry"]["amount_delta"] == "25.00"
        assert down.json()["entry"]["amount_delta"] == "-50.00"
        assert balance(client_a, c["id"]) == Decimal("75.00")

    def test_a_reason_is_required(self, client_a):
        c = customer(client_a)
        base = {"amount": "5", "direction": "INCREASE"}
        for reason in ({}, {"reason": ""}, {"reason": "   "}):
            response = client_a.post(f"{API}/{c['id']}/adjustments", json={**base, **reason})
            assert response.status_code == 422
        assert balance(client_a, c["id"]) == Decimal("0.00")

    def test_the_direction_is_required_and_the_amount_is_positive(self, client_a):
        c = customer(client_a)
        url = f"{API}/{c['id']}/adjustments"
        assert client_a.post(url, json={"amount": "5", "reason": "x"}).status_code == 422
        assert (
            client_a.post(url, json={"amount": "-5", "direction": "INCREASE", "reason": "x"}).status_code
            == 422
        )
        assert (
            client_a.post(url, json={"amount": "0", "direction": "INCREASE", "reason": "x"}).status_code
            == 422
        )


class TestReversalEndpoint:
    def entry_id(self, response) -> int:
        return response.json()["entry"]["id"]

    def test_the_original_stays_and_the_reversal_offsets_it(self, client_a):
        c = customer(client_a, opening_balance="500")
        payment = pay(client_a, c["id"], "200")

        reversed_ = client_a.post(
            f"{API}/{c['id']}/ledger/{self.entry_id(payment)}/reverse", json={"reason": "Cheque bounced"}
        )

        assert reversed_.status_code == 201
        assert reversed_.json()["entry"]["amount_delta"] == "200.00"
        assert reversed_.json()["entry"]["reverses_entry_id"] == self.entry_id(payment)
        assert balance(client_a, c["id"]) == Decimal("500.00")
        ledger = client_a.get(f"{API}/{c['id']}/ledger").json()["items"]
        original = next(e for e in ledger if e["id"] == self.entry_id(payment))
        assert original["amount_delta"] == "-200.00" and original["reversed_by_entry_id"] == self.entry_id(
            reversed_
        )
        assert len(ledger) == 3  # opening, payment, reversal: nothing deleted

    def test_it_cannot_be_reversed_twice(self, client_a):
        c = customer(client_a)
        entry = self.entry_id(pay(client_a, c["id"], "50"))
        url = f"{API}/{c['id']}/ledger/{entry}/reverse"
        assert client_a.post(url, json={"reason": "x"}).status_code == 201
        assert client_a.post(url, json={"reason": "again"}).status_code == 409
        assert balance(client_a, c["id"]) == Decimal("0.00")

    def test_a_reversal_cannot_be_reversed(self, client_a):
        c = customer(client_a)
        entry = self.entry_id(pay(client_a, c["id"], "50"))
        reversal = self.entry_id(
            client_a.post(f"{API}/{c['id']}/ledger/{entry}/reverse", json={"reason": "x"})
        )
        assert (
            client_a.post(f"{API}/{c['id']}/ledger/{reversal}/reverse", json={"reason": "y"}).status_code
            == 409
        )

    def test_a_reason_is_required(self, client_a):
        c = customer(client_a)
        entry = self.entry_id(pay(client_a, c["id"], "50"))
        url = f"{API}/{c['id']}/ledger/{entry}/reverse"
        assert client_a.post(url, json={}).status_code == 422
        assert client_a.post(url, json={"reason": "  "}).status_code == 422

    def test_an_opening_balance_can_be_reversed_and_redone(self, client_a):
        c = customer(client_a, opening_balance="999")
        opening = client_a.get(f"{API}/{c['id']}/ledger").json()["items"][0]["id"]
        assert (
            client_a.post(f"{API}/{c['id']}/ledger/{opening}/reverse", json={"reason": "Wrong"}).status_code
            == 201
        )
        assert client_a.post(f"{API}/{c['id']}/opening-balance", json={"amount": "99"}).status_code == 201
        assert balance(client_a, c["id"]) == Decimal("99.00")

    def test_entries_from_a_sale_cannot_be_reversed_by_hand(self, client_a, tenant_a, session_factory):
        c = customer(client_a)
        credit(session_factory, tenant_a, c["id"], "300")
        entry = client_a.get(f"{API}/{c['id']}/ledger").json()["items"][0]

        response = client_a.post(f"{API}/{c['id']}/ledger/{entry['id']}/reverse", json={"reason": "x"})

        assert response.status_code == 409 and entry["entry_type"] == "CREDIT_SALE"
        assert balance(client_a, c["id"]) == Decimal("300.00")

    def test_an_entry_of_another_customer_is_not_found(self, client_a):
        one, two = customer(client_a, name="One"), customer(client_a, name="Two")
        entry = self.entry_id(pay(client_a, one["id"], "5"))
        assert (
            client_a.post(f"{API}/{two['id']}/ledger/{entry}/reverse", json={"reason": "x"}).status_code
            == 404
        )


class TestLedgerEndpoint:
    def test_history_with_running_balance_and_the_document_reference(
        self, client_a, tenant_a, session_factory
    ):
        c = customer(client_a, opening_balance="1000", opening_balance_date="2026-01-01")
        credit(session_factory, tenant_a, c["id"], "500", ref=77)
        pay(client_a, c["id"], "300")

        data = client_a.get(f"{API}/{c['id']}/ledger").json()

        assert data["total"] == 3
        assert [e["entry_type"] for e in data["items"]] == ["PAYMENT", "CREDIT_SALE", "OPENING_BALANCE"]
        assert [e["balance_after"] for e in data["items"]] == ["1200.00", "1500.00", "1000.00"]
        sale = data["items"][1]
        assert (sale["reference_type"], sale["reference_id"]) == ("SALE", 77)
        assert data["items"][0]["created_by_name"] == "Test Owner"
        assert data["items"][0]["balance_after"] == client_a.get(f"{API}/{c['id']}/balance").json()["balance"]

    def test_filters_and_pagination(self, client_a):
        c = customer(client_a, opening_balance="100", opening_balance_date="2026-01-01")
        pay(client_a, c["id"], "10", entry_date="2026-02-01")
        pay(client_a, c["id"], "10", entry_date="2026-03-01")

        assert client_a.get(f"{API}/{c['id']}/ledger", params={"entry_type": "PAYMENT"}).json()["total"] == 2
        assert (
            client_a.get(f"{API}/{c['id']}/ledger", params={"date_from": "2026-02-15"}).json()["total"] == 1
        )
        assert client_a.get(f"{API}/{c['id']}/ledger", params={"date_to": "2026-02-01"}).json()["total"] == 2
        page = client_a.get(f"{API}/{c['id']}/ledger", params={"limit": 1, "offset": 1}).json()
        assert len(page["items"]) == 1 and page["total"] == 3
        assert client_a.get(f"{API}/{c['id']}/ledger", params={"entry_type": "BOGUS"}).status_code == 422

    def test_ledger_rows_cannot_be_changed_or_deleted_through_the_api(self, client_a):
        c = customer(client_a, opening_balance="10")
        entry = client_a.get(f"{API}/{c['id']}/ledger").json()["items"][0]["id"]
        url = f"{API}/{c['id']}/ledger/{entry}"

        not_available = (404, 405)  # there is no such route, or no such method on it
        assert client_a.put(url, json={}).status_code in not_available
        assert client_a.patch(url, json={}).status_code in not_available
        assert client_a.delete(url).status_code in not_available
        assert client_a.post(f"{API}/{c['id']}/ledger", json={"amount": "5"}).status_code in not_available

    def test_there_is_no_endpoint_to_add_credit_or_return_credit(self, client_a):
        c = customer(client_a)
        for suffix in ("credit-sales", "credit-sale", "return-credit", "sales"):
            assert client_a.post(f"{API}/{c['id']}/{suffix}", json={"amount": "5"}).status_code == 404

    def test_the_database_refuses_edits_and_deletes_of_ledger_rows(self, client_a, session):
        from sqlalchemy import text

        c = customer(client_a, opening_balance="10")
        for statement in ("UPDATE customer_ledger SET amount_delta = 1", "DELETE FROM customer_ledger"):
            with pytest.raises(Exception, match="insert-only"):
                session.execute(text(statement))
            session.rollback()
        assert balance(client_a, c["id"]) == Decimal("10.00")


class TestTenantIsolation:
    def test_another_shops_customer_is_not_found_everywhere(self, client_a, client_b):
        c = customer(client_a, opening_balance="100")
        entry = client_a.get(f"{API}/{c['id']}/ledger").json()["items"][0]["id"]
        base = f"{API}/{c['id']}"

        responses = [
            client_b.get(base),
            client_b.patch(base, json={"name": "Hacked"}),
            client_b.post(f"{base}/activate"),
            client_b.post(f"{base}/deactivate"),
            client_b.get(f"{base}/balance"),
            client_b.get(f"{base}/ledger"),
            client_b.post(f"{base}/opening-balance", json={"amount": "1"}),
            client_b.post(f"{base}/payments", json={"amount": "1"}),
            client_b.post(
                f"{base}/adjustments", json={"amount": "1", "direction": "INCREASE", "reason": "x"}
            ),
            client_b.post(f"{base}/ledger/{entry}/reverse", json={"reason": "x"}),
        ]

        assert [r.status_code for r in responses] == [404] * len(responses)
        assert (
            client_a.get(base).json()["balance"] == "100.00"
            and client_a.get(base).json()["name"] == "Ramesh Kumar"
        )

    def test_lists_and_searches_never_include_another_shops_customers(self, client_a, client_b):
        customer(client_a, name="Mine", phone="9000000001", opening_balance="5")
        customer(client_b, name="Theirs", phone="9000000009", opening_balance="777")

        assert [c["name"] for c in client_a.get(API).json()["items"]] == ["Mine"]
        assert client_a.get(API, params={"q": "Theirs"}).json()["total"] == 0
        assert client_a.get(API, params={"q": "9000000009"}).json()["total"] == 0
        assert client_a.get(API, params={"balance": "outstanding", "status": "all"}).json()["total"] == 1

    def test_payments_only_touch_the_callers_shop(self, client_a, client_b, fresh, tenant_a, tenant_b):
        mine = customer(client_a)
        pay(client_a, mine["id"], "10")

        rows = fresh(lambda s: s.execute(select(CustomerLedgerEntry.shop_id)).scalars().all())

        assert rows == [tenant_a.shop.id] and tenant_a.shop.id != tenant_b.shop.id

    def test_a_ledger_row_cannot_point_at_another_shops_customer_even_in_the_database(
        self, session, tenant_a, tenant_b
    ):
        from tests import factories
        from tests.conftest import assert_rejected
        from tests.test_ledgers import entry

        foreign = factories.make_customer(session, tenant_b.shop)
        session.commit()
        assert_rejected(session, entry(tenant_a, foreign, E.PAYMENT, "-5"), match="FOREIGN KEY")


class TestEveryBusinessType:
    @pytest.mark.parametrize("business_type", REQUESTED_TYPES)
    def test_khata_works_the_same_for_every_business_type(self, make_client, tenant_of, business_type):
        client = make_client(tenant_of(business_type))

        c = customer(client, name="Buyer", opening_balance="1000")
        pay(client, c["id"], "1500")

        assert client.get(f"{API}/{c['id']}").json()["balance_status"] == "ADVANCE"
        assert balance(client, c["id"]) == Decimal("-500.00")


class TestStaffCanUseKhata:
    def test_a_staff_user_can_take_a_payment(self, make_client, tenant_a, client_a):
        c = customer(client_a, opening_balance="10")
        staff = make_client(tenant_a, role=UserRole.STAFF)
        assert pay(staff, c["id"], "10").status_code == 201


class TestRaces:
    def race(self, clients, request):
        barrier = threading.Barrier(len(clients))

        def run(client):
            barrier.wait()
            return request(client).status_code

        with ThreadPoolExecutor(len(clients)) as pool:
            return sorted(pool.map(run, clients))

    @pytest.mark.parametrize("attempt", range(3))
    def test_two_simultaneous_opening_balances_make_exactly_one(
        self, client_a, make_client, tenant_a, attempt
    ):
        c = customer(client_a, name=f"Race {attempt}")
        clients = [make_client(tenant_a) for _ in range(3)]

        codes = self.race(
            clients, lambda cl: cl.post(f"{API}/{c['id']}/opening-balance", json={"amount": "100"})
        )

        assert codes == [201, 409, 409] and balance(client_a, c["id"]) == Decimal("100.00")

    @pytest.mark.parametrize("attempt", range(3))
    def test_two_simultaneous_reversals_undo_the_entry_once(self, client_a, make_client, tenant_a, attempt):
        c = customer(client_a, name=f"Rev {attempt}", opening_balance="500")
        entry = pay(client_a, c["id"], "200").json()["entry"]["id"]
        clients = [make_client(tenant_a) for _ in range(3)]

        codes = self.race(
            clients, lambda cl: cl.post(f"{API}/{c['id']}/ledger/{entry}/reverse", json={"reason": "x"})
        )

        assert codes == [201, 409, 409] and balance(client_a, c["id"]) == Decimal("500.00")

    def test_simultaneous_payments_all_count(self, client_a, make_client, tenant_a):
        c = customer(client_a, opening_balance="1000")
        clients = [make_client(tenant_a) for _ in range(5)]

        codes = self.race(clients, lambda cl: pay(cl, c["id"], "100"))

        assert codes == [201] * 5 and balance(client_a, c["id"]) == Decimal("500.00")
