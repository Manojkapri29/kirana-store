"""Online payments and webhooks: lifecycle, no trust in the client, verified and idempotent webhooks, no duplicate financial records."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.integrations import payments as payment_adapters
from app.models import CustomerLedgerEntry, OnlinePayment, OnlinePaymentEvent, WebhookEvent
from app.services import khata_service
from tests import factories
from tests.client_helpers import client_with
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import make_quick_sale, make_sale
from tests.integration_helpers import FakeGatewayPayments, signed

API = "/api/v1"
TODAY = today_in_shop_timezone()
SECRET = "whsec-test-secret-value"


@pytest.fixture(autouse=True)
def _fake_gateway(monkeypatch):
    """The fake provider exists only inside these tests: the application never registers it."""
    FakeGatewayPayments.reset()
    monkeypatch.setitem(payment_adapters.PROVIDERS, "fake_gateway", FakeGatewayPayments)
    from app.services import integration_service

    monkeypatch.setitem(
        integration_service.CATALOG[integration_service.IT.PAYMENT],
        "fake_gateway",
        integration_service.ProviderSpec("Fake (tests only)", webhook=True),
    )
    yield
    FakeGatewayPayments.reset()


def _setup(client, provider, monkeypatch, secret=SECRET):
    monkeypatch.setenv("KIRANA_INTEGRATION_WEBHOOK_SECRET", secret)
    body = {"provider": provider, "config": {}, "is_enabled": True}
    if provider != "manual":
        body["webhook_credential_ref"] = "KIRANA_INTEGRATION_WEBHOOK_SECRET"
    r = client.put(f"{API}/integrations/PAYMENT", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _pay(client, key="pay-key-000001", status=201, **over):
    body = {"method": "UPI", "purpose": "KHATA_PAYMENT", "amount": "250.00", **over}
    r = client.post(f"{API}/payments", json=body, headers={"Idempotency-Key": key})
    assert r.status_code == status, r.text
    return r.json()


def _hook(client, view, event, secret=SECRET, timestamp=None):
    body, headers = signed(secret, event, timestamp=timestamp)
    return client.post(view["webhook_path"], content=body, headers=headers)


def _event(txn, status, event_id, amount="250.00", **more):
    return {
        "event_id": event_id,
        "type": "payment.status",
        "txn_id": txn,
        "status": status,
        "amount": amount,
        **more,
    }


def _balance(session, shop_id, customer_id):
    session.commit()  # end the read snapshot this session may be holding, so committed changes are visible
    return khata_service.get_customer_balance(session, shop_id, customer_id)


def _customer_owing(session, tenant, amount="1000.00"):
    """A customer who owes `amount` on their khata (an opening balance: the ledger's own way to start a balance)."""
    c = factories.make_customer(session, tenant.shop, name="Asha")
    session.commit()
    khata_service.create_opening_balance(session, context_for(tenant), c.id, Decimal(amount))
    session.commit()
    return c


class TestNotConfigured:
    def test_no_provider_means_provider_not_configured_never_a_fake_payment(self, client_a, session):
        r = client_a.post(
            f"{API}/payments",
            json={"method": "UPI", "purpose": "KHATA_PAYMENT", "amount": "10.00", "customer_id": 1},
            headers={"Idempotency-Key": "pay-key-000001"},
        )
        assert r.status_code == 409 and "Provider Not Configured" in r.text
        assert session.scalar(select(func.count()).select_from(OnlinePayment)) == 0

    def test_a_disabled_provider_is_not_used(self, client_a, monkeypatch):
        _setup(client_a, "manual", monkeypatch)
        client_a.post(f"{API}/integrations/PAYMENT/disable")
        assert (
            client_a.post(
                f"{API}/payments",
                json={"method": "COD", "purpose": "KHATA_PAYMENT", "amount": "10.00", "customer_id": 1},
                headers={"Idempotency-Key": "pay-key-000001"},
            ).status_code
            == 409
        )


class TestRequestRules:
    def test_an_idempotency_key_is_required_and_validated(self, client_a, monkeypatch):
        _setup(client_a, "manual", monkeypatch)
        body = {"method": "COD", "purpose": "KHATA_PAYMENT", "amount": "10.00", "customer_id": 1}
        assert client_a.post(f"{API}/payments", json=body).status_code == 422
        assert (
            client_a.post(f"{API}/payments", json=body, headers={"Idempotency-Key": "short"}).status_code
            == 422
        )

    @pytest.mark.parametrize("amount", ["0", "-5", "10.005", "abc", "1e3"])
    def test_amounts_must_be_positive_with_two_places(self, client_a, monkeypatch, amount):
        _setup(client_a, "manual", monkeypatch)
        r = client_a.post(
            f"{API}/payments",
            json={"method": "COD", "purpose": "KHATA_PAYMENT", "amount": amount, "customer_id": 1},
            headers={"Idempotency-Key": "pay-key-000001"},
        )
        assert r.status_code == 422

    def test_floats_are_refused(self, session, tenant_a, monkeypatch):
        from app.services import online_payment_service as ops
        from app.services.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            ops._amount(10.5)

    def test_purpose_rules(self, client_a, session, tenant_a, tenant_b, monkeypatch):
        _setup(client_a, "manual", monkeypatch)
        c = _customer_owing(session, tenant_a)
        assert _pay(client_a, "k-000000001", status=422, purpose="KHATA_PAYMENT", customer_id=None)
        assert _pay(client_a, "k-000000002", status=422, purpose="SALE_PAYMENT")
        assert _pay(client_a, "k-000000003", status=422, purpose="SALE_PAYMENT", sale_id=1, quick_sale_id=1)
        foreign = factories.make_customer(session, tenant_b.shop, name="Other")
        session.commit()
        assert _pay(client_a, "k-000000004", status=404, customer_id=foreign.id)
        assert c.id

    def test_a_sale_payment_needs_a_posted_sale_of_this_shop_and_not_more_than_its_total(
        self, client_a, session, tenant_a, tenant_b, monkeypatch
    ):
        _setup(client_a, "manual", monkeypatch)
        sale = make_sale(session, tenant_a, "300.00", day=TODAY, cogs="100.00")
        other = make_sale(session, tenant_b, "300.00", day=TODAY, cogs="100.00")
        session.commit()
        assert _pay(client_a, "k-000000010", status=404, purpose="SALE_PAYMENT", sale_id=other.id)
        assert _pay(
            client_a, "k-000000011", status=422, purpose="SALE_PAYMENT", sale_id=sale.id, amount="300.01"
        )
        ok = _pay(
            client_a, "k-000000012", purpose="SALE_PAYMENT", sale_id=sale.id, amount="300.00", method="COD"
        )
        assert ok["sale_id"] == sale.id and ok["status"] == "PENDING"


class TestManualProvider:
    def test_lifecycle_confirm_then_refund_and_the_khata_is_touched_once(
        self, client_a, session, tenant_a, monkeypatch
    ):
        _setup(client_a, "manual", monkeypatch)
        customer = _customer_owing(session, tenant_a, "1000.00")
        p = _pay(client_a, customer_id=customer.id)
        assert (
            p["status"] == "PENDING"
            and p["provider"] == "manual"
            and p["provider_txn_id"].startswith("manual-")
        )
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal(
            "1000.00"
        )  # nothing recorded until captured
        done = client_a.post(f"{API}/payments/{p['id']}/confirm", json={"note": "cash at the door"})
        assert (
            done.status_code == 200 and done.json()["status"] == "CAPTURED" and done.json()["khata_entry_id"]
        )
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal("750.00")
        again = client_a.post(f"{API}/payments/{p['id']}/confirm", json={})
        assert again.status_code == 409  # already captured: nothing repeated
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal("750.00")
        events = client_a.get(f"{API}/payments/{p['id']}/events").json()
        assert [e["to_status"] for e in events] == ["CREATED", "PENDING", "CAPTURED"] and events[-1][
            "source"
        ] == "MANUAL"

    def test_the_khata_entry_is_a_normal_payment_the_ledger_and_finance_already_understand(
        self, client_a, session, tenant_a, monkeypatch
    ):
        _setup(client_a, "manual", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, customer_id=customer.id, method="COD")
        client_a.post(f"{API}/payments/{p['id']}/confirm", json={})
        session.expire_all()
        entries = list(
            session.scalars(select(CustomerLedgerEntry).where(CustomerLedgerEntry.customer_id == customer.id))
        )
        payment_entry = next(e for e in entries if e.amount_delta < 0)
        assert (
            payment_entry.amount_delta == Decimal("-250.00")
            and payment_entry.payment_reference == f"online-payment:{p['id']}"
        )
        assert payment_entry.payment_method.value == "CASH"  # cash on delivery is cash

    def test_a_sale_payment_posts_nothing(self, client_a, session, tenant_a, monkeypatch):
        _setup(client_a, "manual", monkeypatch)
        sale = make_sale(session, tenant_a, "300.00", day=TODAY, cogs="100.00")
        session.commit()
        before = session.scalar(select(func.count()).select_from(CustomerLedgerEntry))
        p = _pay(client_a, purpose="SALE_PAYMENT", sale_id=sale.id, amount="300.00", method="UPI")
        client_a.post(f"{API}/payments/{p['id']}/confirm", json={})
        session.expire_all()
        assert client_a.get(f"{API}/payments/{p['id']}").json()["status"] == "CAPTURED"
        assert session.scalar(select(func.count()).select_from(CustomerLedgerEntry)) == before

    def test_same_key_same_request_returns_the_same_payment_once(
        self, client_a, session, tenant_a, monkeypatch
    ):
        _setup(client_a, "manual", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        first = _pay(client_a, "same-key-0001", customer_id=customer.id)
        second = _pay(client_a, "same-key-0001", customer_id=customer.id)
        assert first["id"] == second["id"]
        assert session.scalar(select(func.count()).select_from(OnlinePayment)) == 1

    def test_same_key_different_request_is_refused(self, client_a, session, tenant_a, monkeypatch):
        _setup(client_a, "manual", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        _pay(client_a, "same-key-0002", customer_id=customer.id)
        r = client_a.post(
            f"{API}/payments",
            json={
                "method": "UPI",
                "purpose": "KHATA_PAYMENT",
                "amount": "999.00",
                "customer_id": customer.id,
            },
            headers={"Idempotency-Key": "same-key-0002"},
        )
        assert r.status_code == 409 and "different payment" in r.text

    def test_cancel_only_before_capture(self, client_a, session, tenant_a, monkeypatch):
        _setup(client_a, "manual", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, "cancel-key-01", customer_id=customer.id)
        assert client_a.post(f"{API}/payments/{p['id']}/cancel").json()["status"] == "CANCELLED"
        assert client_a.post(f"{API}/payments/{p['id']}/confirm", json={}).status_code == 409
        p2 = _pay(client_a, "cancel-key-02", customer_id=customer.id)
        client_a.post(f"{API}/payments/{p2['id']}/confirm", json={})
        assert client_a.post(f"{API}/payments/{p2['id']}/cancel").status_code == 409

    def test_manual_covers_only_cash_on_delivery_and_upi(self, client_a, session, tenant_a, monkeypatch):
        _setup(client_a, "manual", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        assert (
            client_a.post(
                f"{API}/payments",
                json={
                    "method": "CARD",
                    "purpose": "KHATA_PAYMENT",
                    "amount": "10.00",
                    "customer_id": customer.id,
                },
                headers={"Idempotency-Key": "pay-key-000009"},
            ).status_code
            == 422
        )

    def test_there_is_no_way_for_the_browser_to_set_a_status(self, client_a, monkeypatch, session, tenant_a):
        _setup(client_a, "manual", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, "no-set-key-01", customer_id=customer.id)
        assert client_a.patch(f"{API}/payments/{p['id']}", json={"status": "CAPTURED"}).status_code in (
            404,
            405,
        )
        assert client_a.put(f"{API}/payments/{p['id']}", json={"status": "CAPTURED"}).status_code in (
            404,
            405,
        )
        assert (
            client_a.post(f"{API}/payments/{p['id']}/verify").status_code == 409
        )  # manual cannot be checked: no fabricated answer

    def test_refunds_are_bounded_idempotent_and_never_touch_the_khata(
        self, client_a, session, tenant_a, monkeypatch
    ):
        _setup(client_a, "manual", monkeypatch)
        sale = make_sale(session, tenant_a, "300.00", day=TODAY, cogs="100.00")
        session.commit()
        p = _pay(
            client_a, "ref-pay-key-1", purpose="SALE_PAYMENT", sale_id=sale.id, amount="300.00", method="UPI"
        )
        assert (
            client_a.post(
                f"{API}/payments/{p['id']}/refund",
                json={"amount": "10.00"},
                headers={"Idempotency-Key": "refund-key-01"},
            ).status_code
            == 409
        )  # not captured yet
        client_a.post(f"{API}/payments/{p['id']}/confirm", json={})
        r1 = client_a.post(
            f"{API}/payments/{p['id']}/refund",
            json={"amount": "100.00"},
            headers={"Idempotency-Key": "refund-key-01"},
        )
        assert r1.json()["status"] == "PARTIALLY_REFUNDED" and r1.json()["refunded_amount"] == "100.00"
        again = client_a.post(
            f"{API}/payments/{p['id']}/refund",
            json={"amount": "100.00"},
            headers={"Idempotency-Key": "refund-key-01"},
        )
        assert again.json()["refunded_amount"] == "100.00"  # the same request is not refunded twice
        assert (
            client_a.post(
                f"{API}/payments/{p['id']}/refund",
                json={"amount": "250.00"},
                headers={"Idempotency-Key": "refund-key-02"},
            ).status_code
            == 422
        )
        done = client_a.post(
            f"{API}/payments/{p['id']}/refund",
            json={"amount": "200.00"},
            headers={"Idempotency-Key": "refund-key-03"},
        )
        assert done.json()["status"] == "REFUNDED" and done.json()["refunded_amount"] == "300.00"
        assert (
            client_a.post(
                f"{API}/payments/{p['id']}/refund",
                json={"amount": "1.00"},
                headers={"Idempotency-Key": "refund-key-04"},
            ).status_code
            == 409
        )
        assert (
            client_a.post(f"{API}/payments/{p['id']}/refund", json={"amount": "1.00"}).status_code == 422
        )  # a key is required

    def test_a_khata_payment_cannot_be_refunded_until_its_ledger_entry_is_reversed(
        self, client_a, session, tenant_a, monkeypatch
    ):
        _setup(client_a, "manual", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, "khata-ref-key1", customer_id=customer.id)
        done = client_a.post(f"{API}/payments/{p['id']}/confirm", json={}).json()
        r = client_a.post(
            f"{API}/payments/{p['id']}/refund",
            json={"amount": "50.00"},
            headers={"Idempotency-Key": "refund-key-11"},
        )
        assert r.status_code == 409 and "khata" in r.text.lower()
        assert done["khata_entry_id"]


class TestExternalProvider:
    def test_creation_calls_the_provider_once_and_records_its_transaction(
        self, client_a, monkeypatch, session, tenant_a
    ):
        _setup(client_a, "fake_gateway", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, "ext-key-00001", customer_id=customer.id)
        _pay(client_a, "ext-key-00001", customer_id=customer.id)
        assert p["provider"] == "fake_gateway" and p["provider_txn_id"] == "fg_1" and p["status"] == "PENDING"
        assert FakeGatewayPayments.create_calls == 1

    def test_an_external_payment_cannot_be_confirmed_or_cancelled_by_a_person(
        self, client_a, monkeypatch, session, tenant_a
    ):
        _setup(client_a, "fake_gateway", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, "ext-key-00002", customer_id=customer.id)
        assert client_a.post(f"{API}/payments/{p['id']}/confirm", json={}).status_code == 409
        assert client_a.post(f"{API}/payments/{p['id']}/cancel").status_code == 409

    def test_a_definite_provider_failure_marks_the_payment_failed_and_returns_it(
        self, client_a, monkeypatch, session, tenant_a
    ):
        from app.integrations.base import ProviderError

        _setup(client_a, "fake_gateway", monkeypatch)
        FakeGatewayPayments.create_error = ProviderError("invalid_credentials", retryable=False)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, "ext-key-00003", customer_id=customer.id)
        assert p["status"] == "FAILED" and p["failure_code"] == "invalid_credentials"
        again = _pay(client_a, "ext-key-00003", customer_id=customer.id)
        assert (
            again["id"] == p["id"] and FakeGatewayPayments.create_calls == 1
        )  # a failed create is not silently retried
        d = client_a.get(f"{API}/integrations/dashboard").json()
        assert (
            next(i for i in d["integrations"] if i["integration_type"] == "PAYMENT")["last_error_code"]
            == "invalid_credentials"
        )

    def test_an_unknown_outcome_is_flagged_for_review_not_repeated(
        self, client_a, monkeypatch, session, tenant_a
    ):
        from app.integrations.base import ProviderError

        _setup(client_a, "fake_gateway", monkeypatch)
        FakeGatewayPayments.create_error = ProviderError("timeout_unknown_outcome", retryable=False)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, "ext-key-00004", customer_id=customer.id)
        assert (
            p["status"] == "CREATED" and p["needs_review"] is True and FakeGatewayPayments.create_calls == 1
        )
        assert (
            _pay(client_a, "ext-key-00004", customer_id=customer.id)["id"] == p["id"]
            and FakeGatewayPayments.create_calls == 1
        )

    def test_verify_asks_the_provider_and_retries_a_transient_failure(
        self, client_a, monkeypatch, session, tenant_a
    ):
        from app.integrations.base import ProviderError, ProviderPayment

        _setup(client_a, "fake_gateway", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, "ext-key-00005", customer_id=customer.id)
        FakeGatewayPayments.world["fg_1"] = ProviderPayment("fg_1", "CAPTURED", Decimal("250.00"))
        FakeGatewayPayments.status_errors = [ProviderError("connection_failed", retryable=True)]
        monkeypatch.setattr("time.sleep", lambda s: None)
        done = client_a.post(f"{API}/payments/{p['id']}/verify")
        assert (
            done.status_code == 200
            and done.json()["status"] == "CAPTURED"
            and FakeGatewayPayments.status_calls == 2
        )
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal("750.00")
        client_a.post(f"{API}/payments/{p['id']}/verify")  # checking again changes nothing
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal("750.00")

    def test_verify_with_a_mismatched_amount_is_review_not_money(
        self, client_a, monkeypatch, session, tenant_a
    ):
        from app.integrations.base import ProviderPayment

        _setup(client_a, "fake_gateway", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, "ext-key-00006", customer_id=customer.id)
        FakeGatewayPayments.world["fg_1"] = ProviderPayment("fg_1", "CAPTURED", Decimal("249.00"))
        out = client_a.post(f"{API}/payments/{p['id']}/verify").json()
        assert (
            out["status"] == "PENDING"
            and out["needs_review"] is True
            and "does not match" in out["review_reason"]
        )
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal("1000.00")

    def test_an_external_refund_calls_the_provider_once_per_key(
        self, client_a, monkeypatch, session, tenant_a
    ):
        from app.integrations.base import ProviderPayment

        _setup(client_a, "fake_gateway", monkeypatch)
        sale = make_sale(session, tenant_a, "300.00", day=TODAY, cogs="100.00")
        session.commit()
        p = _pay(
            client_a, "ext-key-00007", purpose="SALE_PAYMENT", sale_id=sale.id, amount="300.00", method="CARD"
        )
        FakeGatewayPayments.world["fg_1"] = ProviderPayment("fg_1", "CAPTURED", Decimal("300.00"))
        client_a.post(f"{API}/payments/{p['id']}/verify")
        for _ in range(3):
            r = client_a.post(
                f"{API}/payments/{p['id']}/refund",
                json={"amount": "100.00"},
                headers={"Idempotency-Key": "refund-key-ext1"},
            )
            assert r.status_code == 200
        assert FakeGatewayPayments.refund_calls == 1 and r.json()["refunded_amount"] == "100.00"


class TestWebhooks:
    def _external(self, client, session, tenant, monkeypatch, key="hook-key-0001"):
        view = _setup(client, "generic_webhook", monkeypatch)
        customer = _customer_owing(session, tenant)
        p = client.post(
            f"{API}/payments",
            json={
                "method": "ONLINE_PAYMENT",
                "purpose": "KHATA_PAYMENT",
                "amount": "250.00",
                "customer_id": customer.id,
                "provider_txn_id": "TXN-1",
            },
            headers={"Idempotency-Key": key},
        ).json()
        return view, customer, p

    def test_a_signed_capture_records_the_payment_once(self, client_a, session, tenant_a, monkeypatch):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        assert p["status"] == "PENDING" and p["provider_txn_id"] == "TXN-1"
        r = _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-1"))
        assert r.status_code == 200 and r.json()["status"] == "processed"
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal("750.00")
        assert client_a.get(f"{API}/payments/{p['id']}").json()["status"] == "CAPTURED"

    def test_a_duplicate_delivery_changes_nothing(self, client_a, session, tenant_a, monkeypatch):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        for _ in range(4):
            assert _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-dup")).status_code == 200
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal("750.00")
        session.expire_all()
        assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 1
        assert session.scalar(select(WebhookEvent.duplicates)) == 3
        entries = session.scalar(
            select(func.count()).select_from(CustomerLedgerEntry).where(CustomerLedgerEntry.amount_delta < 0)
        )
        assert entries == 1

    def test_a_second_event_id_for_the_same_state_is_ignored_not_re_applied(
        self, client_a, session, tenant_a, monkeypatch
    ):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-a"))
        r = _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-b"))
        assert r.json()["status"] == "ignored" and _balance(
            session, tenant_a.shop.id, customer.id
        ) == Decimal("750.00")

    def test_a_forged_signature_is_refused_and_leaves_no_trace_in_the_events_table(
        self, client_a, session, tenant_a, monkeypatch
    ):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        r = _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-forged"), secret="wrong-secret-value-1")
        assert r.status_code == 401 and r.json() == {"status": "refused"}
        session.expire_all()
        assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 0
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal("1000.00")

    def test_a_tampered_body_fails_the_signature(self, client_a, session, tenant_a, monkeypatch):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        body, headers = signed(SECRET, _event("TXN-1", "CAPTURED", "evt-t", amount="250.00"))
        r = client_a.post(view["webhook_path"], content=body.replace(b"250.00", b"9.00"), headers=headers)
        assert r.status_code == 401

    def test_stale_and_future_timestamps_are_refused(self, client_a, session, tenant_a, monkeypatch):
        import time

        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        assert (
            _hook(
                client_a, view, _event("TXN-1", "CAPTURED", "evt-old"), timestamp=int(time.time()) - 3600
            ).status_code
            == 401
        )
        assert (
            _hook(
                client_a, view, _event("TXN-1", "CAPTURED", "evt-fut"), timestamp=int(time.time()) + 3600
            ).status_code
            == 401
        )

    def test_a_missing_header_or_unknown_key_reveals_nothing(self, client_a, session, tenant_a, monkeypatch):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        assert client_a.post(view["webhook_path"], content=b"{}").status_code == 401
        unknown = client_a.post("/api/v1/webhooks/" + "x" * 32, content=b"{}")
        assert unknown.status_code == 404 and unknown.json() == {"status": "not_found"}
        assert client_a.post("/api/v1/webhooks/short", content=b"{}").status_code == 404

    def test_oversized_bodies_are_refused(self, client_a, session, tenant_a, monkeypatch):
        view, *_ = self._external(client_a, session, tenant_a, monkeypatch)
        assert client_a.post(view["webhook_path"], content=b"x" * (70 * 1024)).status_code == 413

    def test_a_reused_event_id_with_a_different_body_is_refused(
        self, client_a, session, tenant_a, monkeypatch
    ):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        _hook(client_a, view, _event("TXN-1", "PENDING", "evt-same"))
        assert _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-same")).status_code == 401
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal("1000.00")

    def test_an_amount_that_does_not_match_is_not_applied(self, client_a, session, tenant_a, monkeypatch):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        r = _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-amt", amount="200.00"))
        assert r.status_code == 200 and r.json()["status"] == "failed"
        shown = client_a.get(f"{API}/payments/{p['id']}").json()
        assert (
            shown["status"] == "PENDING"
            and shown["needs_review"]
            and _balance(session, tenant_a.shop.id, customer.id) == Decimal("1000.00")
        )
        assert client_a.get(f"{API}/payments", params={"needs_review": "true"}).json()["total"] == 1

    def test_out_of_order_and_contradicting_events(self, client_a, session, tenant_a, monkeypatch):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-1"))
        late = _hook(client_a, view, _event("TXN-1", "PENDING", "evt-2"))  # older news arriving late
        assert (
            late.json()["status"] == "ignored"
            and client_a.get(f"{API}/payments/{p['id']}").json()["status"] == "CAPTURED"
        )
        # a payment recorded as failed that the provider later says was captured is a contradiction: reviewed, never applied
        p2 = client_a.post(
            f"{API}/payments",
            json={
                "method": "ONLINE_PAYMENT",
                "purpose": "KHATA_PAYMENT",
                "amount": "50.00",
                "customer_id": customer.id,
                "provider_txn_id": "TXN-2",
            },
            headers={"Idempotency-Key": "hook-key-0002"},
        ).json()
        _hook(client_a, view, _event("TXN-2", "FAILED", "evt-3", amount="50.00", failure_code="declined"))
        bad = _hook(client_a, view, _event("TXN-2", "CAPTURED", "evt-4", amount="50.00"))
        assert bad.json()["status"] == "failed"
        shown = client_a.get(f"{API}/payments/{p2['id']}").json()
        assert shown["status"] == "FAILED" and shown["needs_review"] and shown["failure_code"] == "declined"
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal("750.00")

    def test_refund_events_track_the_cumulative_amount_and_flag_a_live_khata_entry(
        self, client_a, session, tenant_a, monkeypatch
    ):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-1"))
        _hook(client_a, view, _event("TXN-1", "PARTIALLY_REFUNDED", "evt-2", refunded="100.00"))
        shown = client_a.get(f"{API}/payments/{p['id']}").json()
        assert (
            shown["status"] == "PARTIALLY_REFUNDED"
            and shown["refunded_amount"] == "100.00"
            and shown["needs_review"]
        )
        assert "reverse that entry" in shown["review_reason"]
        assert (
            _hook(client_a, view, _event("TXN-1", "PARTIALLY_REFUNDED", "evt-3", refunded="100.00")).json()[
                "status"
            ]
            == "ignored"
        )
        _hook(client_a, view, _event("TXN-1", "REFUNDED", "evt-4", refunded="250.00"))
        assert client_a.get(f"{API}/payments/{p['id']}").json()["status"] == "REFUNDED"
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal(
            "750.00"
        )  # the khata is never changed by a refund event

    def test_events_for_unknown_payments_or_types_are_ignored_safely(
        self, client_a, session, tenant_a, monkeypatch
    ):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        assert _hook(client_a, view, _event("NOPE", "CAPTURED", "evt-x")).json()["status"] == "ignored"
        assert _hook(client_a, view, {"event_id": "evt-y", "type": "ping"}).json()["status"] == "ignored"
        assert (
            _hook(
                client_a,
                view,
                {"event_id": "evt-z", "type": "payment.status", "txn_id": "TXN-1", "status": "TELEPORTED"},
            ).status_code
            == 400
        )
        assert (
            _hook(
                client_a,
                view,
                {
                    "event_id": "evt-f",
                    "type": "payment.status",
                    "txn_id": "TXN-1",
                    "status": "CAPTURED",
                    "amount": 250.0,
                },
            ).status_code
            == 400
        )  # floats refused

    def test_a_webhook_cannot_reach_another_shops_payment(
        self, client_a, client_b, session, tenant_a, tenant_b, monkeypatch
    ):
        view_a, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        view_b = _setup(client_b, "generic_webhook", monkeypatch)
        assert view_a["webhook_path"] != view_b["webhook_path"]
        r = _hook(
            client_b, view_b, _event("TXN-1", "CAPTURED", "evt-cross")
        )  # shop B signs an event naming shop A's transaction id
        assert r.json()["status"] == "ignored"
        assert client_a.get(f"{API}/payments/{p['id']}").json()["status"] == "PENDING" and _balance(
            session, tenant_a.shop.id, customer.id
        ) == Decimal("1000.00")

    def test_rotating_the_webhook_key_disables_the_old_address(
        self, client_a, session, tenant_a, monkeypatch
    ):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        new = client_a.post(f"{API}/integrations/PAYMENT/rotate-webhook-key").json()
        assert new["webhook_path"] != view["webhook_path"]
        assert _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-old-key")).status_code == 404
        assert _hook(client_a, new, _event("TXN-1", "CAPTURED", "evt-new-key")).status_code == 200

    def test_a_missing_secret_means_the_endpoint_is_closed(self, client_a, session, tenant_a, monkeypatch):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        monkeypatch.delenv("KIRANA_INTEGRATION_WEBHOOK_SECRET")
        assert _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-nosecret")).status_code == 404

    def test_a_disabled_integration_ignores_webhooks(self, client_a, session, tenant_a, monkeypatch):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        client_a.post(f"{API}/integrations/PAYMENT/disable")
        assert _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-dis")).status_code == 404

    def test_a_failed_application_is_recorded_and_a_redelivery_retries_it(
        self, client_a, session, tenant_a, monkeypatch
    ):
        from app.services import online_payment_service as ops

        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        real = ops.apply_status
        monkeypatch.setattr(ops, "apply_status", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        r = _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-retry"))
        assert r.status_code == 200 and r.json()["status"] == "failed"
        monkeypatch.setattr(ops, "apply_status", real)
        assert _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-retry")).json()["status"] == "processed"
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal("750.00")

    def test_the_management_list_shows_event_status_and_no_payload(
        self, client_a, session, tenant_a, monkeypatch
    ):
        view, customer, p = self._external(client_a, session, tenant_a, monkeypatch)
        _hook(client_a, view, _event("TXN-1", "CAPTURED", "evt-list"))
        items = client_a.get(f"{API}/integrations/webhook-events").json()["items"]
        assert items[0]["status"] == "PROCESSED" and set(items[0]) == {
            "id",
            "provider",
            "event_id",
            "event_type",
            "status",
            "error_code",
            "duplicates",
            "received_at",
            "processed_at",
        }

    def test_an_unconfigured_secret_reference_is_not_usable_for_the_webhook_secret(
        self, client_a, monkeypatch
    ):
        r = client_a.put(
            f"{API}/integrations/PAYMENT",
            json={"provider": "generic_webhook", "config": {}, "webhook_credential_ref": "PATH"},
        )
        assert r.status_code == 422

    def test_the_webhook_is_rate_limited(self, client_a, monkeypatch):
        from app.core import ratelimit
        from app.core.config import get_settings

        monkeypatch.setenv("KIRANA_RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("KIRANA_RATE_LIMIT_WEBHOOK", "3")
        get_settings.cache_clear()
        ratelimit.limiter.reset()
        codes = [client_a.post("/api/v1/webhooks/" + "y" * 32, content=b"{}").status_code for _ in range(6)]
        assert codes[:3] == [404, 404, 404] and 429 in codes[3:]


class TestPermissionsAndIsolation:
    def test_payment_routes_need_their_permissions(self, session, tenant_a, make_client, monkeypatch):
        owner = make_client(tenant_a)
        _setup(owner, "manual", monkeypatch)
        viewer = client_with(make_client, tenant_a, ["INTEGRATION_VIEW"])
        assert viewer.get(f"{API}/payments").status_code == 200
        assert (
            viewer.post(
                f"{API}/payments",
                json={"method": "COD", "purpose": "KHATA_PAYMENT", "amount": "1.00", "customer_id": 1},
                headers={"Idempotency-Key": "pay-key-000001"},
            ).status_code
            == 403
        )
        for action in ("verify", "cancel"):
            assert viewer.post(f"{API}/payments/1/{action}").status_code == 403
        assert viewer.post(f"{API}/payments/1/confirm", json={}).status_code == 403
        assert (
            viewer.post(
                f"{API}/payments/1/refund",
                json={"amount": "1.00"},
                headers={"Idempotency-Key": "refund-key-01"},
            ).status_code
            == 403
        )
        assert client_with(make_client, tenant_a, []).get(f"{API}/payments").status_code == 403

    def test_payments_are_shop_scoped(self, client_a, client_b, session, tenant_a, monkeypatch):
        _setup(client_a, "manual", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, "iso-key-00001", customer_id=customer.id)
        assert client_b.get(f"{API}/payments/{p['id']}").status_code == 404
        assert client_b.get(f"{API}/payments").json()["total"] == 0
        for action in ("verify", "cancel"):
            assert client_b.post(f"{API}/payments/{p['id']}/{action}").status_code == 404
        assert client_b.get(f"{API}/payments/{p['id']}/events").status_code == 404

    def test_events_are_insert_only_history(self, client_a, session, tenant_a, monkeypatch):
        _setup(client_a, "manual", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, "hist-key-0001", customer_id=customer.id)
        client_a.post(f"{API}/payments/{p['id']}/confirm", json={})
        session.expire_all()
        assert session.scalar(select(func.count()).select_from(OnlinePaymentEvent)) == 3


def test_quick_sale_payments_link_to_the_quick_sale(client_a, session, tenant_a, monkeypatch):
    _setup(client_a, "manual", monkeypatch)
    quick = make_quick_sale(session, tenant_a, "80.00", day=TODAY - timedelta(days=0))
    session.commit()
    p = _pay(
        client_a,
        "quick-key-0001",
        purpose="SALE_PAYMENT",
        quick_sale_id=quick.id,
        amount="80.00",
        method="UPI",
    )
    assert p["quick_sale_id"] == quick.id and p["sale_id"] is None


class TestRaces:
    """Simultaneous requests: the outcome must be the same as one careful request (the database, not the timing, decides)."""

    def race(self, clients, fn):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        barrier = Barrier(len(clients))

        def run(client):
            barrier.wait()
            return fn(client)

        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            return list(pool.map(run, clients))

    @pytest.mark.parametrize("attempt", range(3))
    def test_the_same_webhook_delivered_at_once_is_applied_once(
        self, client_a, make_client, session, tenant_a, monkeypatch, attempt
    ):
        view = _setup(client_a, "generic_webhook", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        client_a.post(
            f"{API}/payments",
            json={
                "method": "ONLINE_PAYMENT",
                "purpose": "KHATA_PAYMENT",
                "amount": "250.00",
                "customer_id": customer.id,
                "provider_txn_id": f"TXN-R{attempt}",
            },
            headers={"Idempotency-Key": f"race-key-0000{attempt}"},
        )
        clients = [make_client(tenant_a) for _ in range(4)]
        body, headers = signed(SECRET, _event(f"TXN-R{attempt}", "CAPTURED", f"evt-race-{attempt}"))
        results = self.race(clients, lambda c: c.post(view["webhook_path"], content=body, headers=headers))
        assert all(r.status_code == 200 for r in results)
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal(
            "750.00"
        )  # 1000 owed, 250 recorded exactly once
        session.expire_all()
        assert session.scalar(select(func.count()).select_from(WebhookEvent)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(CustomerLedgerEntry)
                .where(CustomerLedgerEntry.amount_delta < 0)
            )
            == 1
        )

    @pytest.mark.parametrize("attempt", range(3))
    def test_the_same_payment_request_sent_at_once_creates_one_payment(
        self, client_a, make_client, session, tenant_a, monkeypatch, attempt
    ):
        _setup(client_a, "manual", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        clients = [make_client(tenant_a) for _ in range(4)]
        body = {"method": "UPI", "purpose": "KHATA_PAYMENT", "amount": "50.00", "customer_id": customer.id}
        results = self.race(
            clients,
            lambda c: c.post(
                f"{API}/payments", json=body, headers={"Idempotency-Key": f"same-race-key-{attempt}"}
            ),
        )
        assert sorted({r.status_code for r in results}) in (
            [201],
            [201, 409],
            [201, 500],
        )  # losers are refused or see the winner's row
        session.expire_all()
        assert session.scalar(select(func.count()).select_from(OnlinePayment)) == 1

    @pytest.mark.parametrize("attempt", range(3))
    def test_two_people_confirming_the_same_payment_record_it_once(
        self, client_a, make_client, session, tenant_a, monkeypatch, attempt
    ):
        _setup(client_a, "manual", monkeypatch)
        customer = _customer_owing(session, tenant_a)
        p = _pay(client_a, f"conf-race-key-{attempt}", customer_id=customer.id)
        clients = [make_client(tenant_a) for _ in range(3)]
        self.race(clients, lambda c: c.post(f"{API}/payments/{p['id']}/confirm", json={}))
        assert _balance(session, tenant_a.shop.id, customer.id) == Decimal("750.00")
