"""Notifications: in-app first, de-duplicated, preference-aware, isolated, and unable to fail a business transaction."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.types import utc_now
from app.models import NotificationDelivery, NotificationEvent, Sale, SystemEvent
from app.models.enums import DeliveryStatus, NotificationChannel
from app.services import notification_service as ns
from tests.factories import make_user
from tests.test_promotions import shop as shop  # noqa: F401
from tests.test_promotions import sold
from tests.test_sales_api import item

API = "/api/v1/notifications"


def emit(session_factory, tenant, key="k1", event_type="LOW_STOCK", **kw):
    with session_factory() as s, s.begin():
        return ns.emit(
            s,
            tenant.shop.id,
            event_type,
            title=kw.get("title", "T"),
            message=kw.get("message", "M"),
            dedupe_key=key,
        ).id


def inbox(client, **params):
    r = client.get(API, params=params)
    assert r.status_code == 200, r.text
    return r.json()


class TestInApp:
    def test_an_event_reaches_the_inbox_with_unread_count_and_fields(
        self, client_a, tenant_a, session_factory
    ):
        emit(session_factory, tenant_a, title="Stock alert", message="Rice is below its reorder level.")
        body = inbox(client_a)
        assert body["unread"] == 1 and body["total"] == 1 and body["page"] == 1
        row = body["items"][0]
        assert (row["title"], row["message"], row["event_type"], row["category"], row["read"]) == (
            "Stock alert",
            "Rice is below its reorder level.",
            "LOW_STOCK",
            "LOW_STOCK",
            False,
        )
        assert row["created_at"] and set(row) >= {"id", "entity_type", "entity_id"}
        assert client_a.get(f"{API}/unread-count").json() == {"unread": 1}

    def test_the_same_event_is_never_created_twice(self, client_a, tenant_a, session_factory):
        first = emit(session_factory, tenant_a, key="dup")
        second = emit(session_factory, tenant_a, key="dup")
        assert first == second and inbox(client_a)["total"] == 1
        with session_factory() as s:
            assert s.scalar(select(func.count()).select_from(NotificationEvent)) == 1

    def test_mark_read_and_mark_all_read(self, client_a, tenant_a, session_factory):
        for key in ("a", "b", "c"):
            emit(session_factory, tenant_a, key=key)
        rows = inbox(client_a)["items"]
        assert client_a.post(f"{API}/{rows[0]['id']}/read").json() == {"unread": 2}
        assert inbox(client_a, unread_only=True)["total"] == 2
        assert client_a.post(f"{API}/read-all").json() == {"unread": 0}
        assert inbox(client_a)["items"][0]["read"] is True and inbox(client_a)["unread"] == 0
        assert client_a.post(f"{API}/999999/read").status_code == 404

    def test_newest_first_and_paged(self, client_a, tenant_a, session_factory):
        for i in range(5):
            emit(session_factory, tenant_a, key=f"k{i}", title=f"N{i}")
        page = inbox(client_a, limit=2, offset=2)
        assert (
            [r["title"] for r in page["items"]] == ["N2", "N1"]
            and page["total"] == 5
            and page["page"] == 2
            and page["total_pages"] == 3
        )

    def test_shops_never_see_each_others_notifications(
        self, client_a, client_b, tenant_a, tenant_b, session_factory
    ):
        a_id = emit(session_factory, tenant_a, key="a", title="For A")
        emit(session_factory, tenant_b, key="b", title="For B")
        assert [r["title"] for r in inbox(client_a)["items"]] == ["For A"] and [
            r["title"] for r in inbox(client_b)["items"]
        ] == ["For B"]
        with session_factory() as s:
            delivery_id = s.scalar(
                select(NotificationDelivery.id).where(NotificationDelivery.event_id == a_id)
            )
        assert (
            client_b.post(f"{API}/{delivery_id}/read").status_code == 404
        )  # another shop's notification looks missing
        assert inbox(client_a)["unread"] == 1

    def test_users_in_one_shop_have_their_own_inbox(self, client_a, tenant_a, session_factory, make_client):
        with session_factory() as s, s.begin():
            other = make_user(s, tenant_a.shop, email="second@shop.test")
        emit(session_factory, tenant_a, key="shared")
        with session_factory() as s:
            assert (
                s.scalar(select(func.count()).select_from(NotificationDelivery)) == 2
            )  # one per active user
        client_a.post(f"{API}/read-all")
        with session_factory() as s:
            unread = s.scalars(
                select(NotificationDelivery).where(NotificationDelivery.read_at.is_(None))
            ).all()
            assert [d.user_id for d in unread] == [other.id]  # only the other person's stays unread

    def test_unknown_notification_types_are_refused(self, session_factory, tenant_a):
        from app.services.errors import InvalidInputError

        with session_factory() as s, pytest.raises(InvalidInputError), s.begin():
            ns.emit(s, tenant_a.shop.id, "MADE_UP", title="t", message="m", dedupe_key="x")

    def test_every_documented_event_type_has_a_category(self):
        for name in (
            "ONLINE_ORDER_PLACED",
            "ONLINE_ORDER_ACCEPTED",
            "ONLINE_ORDER_REJECTED",
            "ONLINE_ORDER_READY",
            "ONLINE_ORDER_OUT_FOR_DELIVERY",
            "ONLINE_ORDER_DELIVERED",
            "LOW_STOCK",
            "PAYMENT_RECEIVED",
            "KHATA_REMINDER",
            "BACKUP_FAILED",
            "AI_USAGE_LIMIT",
            "SUBSCRIPTION_LIMIT",
        ):
            assert ns.EVENT_CATEGORY[name] in ns.CATEGORIES


class TestPreferences:
    def test_defaults_then_a_validated_change_that_dispatch_respects(
        self, client_a, tenant_a, session_factory
    ):
        prefs = client_a.get(f"{API}/preferences").json()
        assert prefs["preferences"]["LOW_STOCK"] == {
            "in_app": True,
            "email": False,
            "sms": False,
            "whatsapp": False,
            "push": False,
        }
        assert prefs["channels"] == {
            "IN_APP": True,
            "EMAIL": False,
            "SMS": False,
            "WHATSAPP": False,
            "PUSH": False,
        }  # honest: only in-app can deliver
        assert (
            client_a.put(f"{API}/preferences/low_stock", json={"channels": {"in_app": False}}).status_code
            == 200
        )
        emit(session_factory, tenant_a, key="muted", event_type="LOW_STOCK")
        assert inbox(client_a)["total"] == 0
        emit(
            session_factory, tenant_a, key="other", event_type="PAYMENT_RECEIVED"
        )  # a different category is unaffected
        assert inbox(client_a)["total"] == 1

    @pytest.mark.parametrize(
        ("category", "body"),
        [
            ("NOPE", {"channels": {"in_app": True}}),
            ("LOW_STOCK", {"channels": {"telepathy": True}}),
            ("LOW_STOCK", {"extra": 1}),
        ],
    )
    def test_invalid_preferences_are_refused(self, client_a, category, body):
        assert client_a.put(f"{API}/preferences/{category}", json=body).status_code == 422

    def test_an_external_channel_without_a_provider_creates_no_delivery(
        self, client_a, tenant_a, session_factory
    ):
        client_a.put(f"{API}/preferences/LOW_STOCK", json={"channels": {"email": True, "sms": True}})
        emit(session_factory, tenant_a, key="ext")
        with session_factory() as s:
            assert {d.channel for d in s.scalars(select(NotificationDelivery))} == {
                NotificationChannel.IN_APP
            }  # nothing pretends to be sent


class Provider:
    name = "fake-mail"

    def __init__(self, *errors):
        self.errors, self.sent = list(errors), []

    def send(self, *, recipient, title, message, timeout):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append((recipient, title, timeout))


@pytest.fixture
def with_email(monkeypatch):
    monkeypatch.setitem(ns.PROVIDERS[NotificationChannel.EMAIL], "fake-mail", lambda: Provider())
    monkeypatch.setenv("KIRANA_NOTIFICATION_EMAIL_PROVIDER", "fake-mail")
    get_settings.cache_clear()


class TestExternalDelivery:
    def queue(self, client_a, session_factory, tenant_a):
        client_a.put(f"{API}/preferences/LOW_STOCK", json={"channels": {"email": True}})
        emit(session_factory, tenant_a, key="mail")

    def test_pending_email_is_sent_through_the_provider_with_a_timeout(
        self, client_a, tenant_a, session_factory, with_email
    ):
        self.queue(client_a, session_factory, tenant_a)
        provider = Provider()
        with session_factory() as s, s.begin():
            run = ns.process_due(s, providers={NotificationChannel.EMAIL: provider})
        assert (
            (run.attempted, run.sent) == (1, 1)
            and provider.sent[0][2] == 10.0
            and provider.sent[0][0].endswith("@shop.test")
            or provider.sent[0][0]
        )
        with session_factory() as s:
            d = s.scalars(
                select(NotificationDelivery).where(NotificationDelivery.channel == NotificationChannel.EMAIL)
            ).one()
            assert (d.status, d.attempts, d.error_code) == (DeliveryStatus.SENT, 1, None) and d.sent_at

    def test_a_retryable_failure_backs_off_exponentially_then_fails_after_the_limit(
        self, client_a, tenant_a, session_factory, with_email, monkeypatch
    ):
        monkeypatch.setenv("KIRANA_NOTIFICATION_MAX_ATTEMPTS", "3")
        monkeypatch.setenv("KIRANA_NOTIFICATION_BACKOFF_SECONDS", "60")
        get_settings.cache_clear()
        self.queue(client_a, session_factory, tenant_a)
        provider = Provider(
            ns.ProviderError("timeout"), ns.ProviderError("timeout"), ns.ProviderError("timeout")
        )
        now = utc_now()
        waits = []
        for _attempt in range(3):
            with session_factory() as s, s.begin():
                ns.process_due(s, now=now, providers={NotificationChannel.EMAIL: provider})
            with session_factory() as s:
                d = s.scalars(
                    select(NotificationDelivery).where(
                        NotificationDelivery.channel == NotificationChannel.EMAIL
                    )
                ).one()
                waits.append(
                    (
                        d.status,
                        d.attempts,
                        None
                        if d.next_attempt_at is None
                        else round((d.next_attempt_at - now).total_seconds()),
                    )
                )
                now = (d.next_attempt_at or now) + timedelta(seconds=1)
        assert waits == [
            (DeliveryStatus.RETRYING, 1, 60),
            (DeliveryStatus.RETRYING, 2, 120),
            (DeliveryStatus.FAILED, 3, None),
        ]
        with session_factory() as s:
            event = s.scalars(select(SystemEvent).where(SystemEvent.category == "notification")).one()
            assert event.code == "timeout" and event.message == "A notification could not be delivered."

    def test_a_non_retryable_failure_fails_at_once_and_a_provider_bug_never_crashes(
        self, client_a, tenant_a, session_factory, with_email
    ):
        self.queue(client_a, session_factory, tenant_a)
        with session_factory() as s, s.begin():
            ns.process_due(
                s,
                providers={
                    NotificationChannel.EMAIL: Provider(ns.ProviderError("rejected", retryable=False))
                },
            )
        with session_factory() as s:
            assert (
                s.scalars(
                    select(NotificationDelivery).where(
                        NotificationDelivery.channel == NotificationChannel.EMAIL
                    )
                )
                .one()
                .status
                is DeliveryStatus.FAILED
            )
        client_a.put(f"{API}/preferences/PAYMENTS", json={"channels": {"email": True}})
        emit(session_factory, tenant_a, key="bug", event_type="PAYMENT_RECEIVED")
        with session_factory() as s, s.begin():
            run = ns.process_due(s, providers={NotificationChannel.EMAIL: Provider(RuntimeError("boom"))})
        assert run.failed == 1

    def test_nothing_is_sent_twice_when_the_worker_runs_again(
        self, client_a, tenant_a, session_factory, with_email
    ):
        self.queue(client_a, session_factory, tenant_a)
        provider = Provider()
        for _ in range(3):
            with session_factory() as s, s.begin():
                ns.process_due(s, providers={NotificationChannel.EMAIL: provider})
        assert len(provider.sent) == 1


class TestBusinessTransactionsAreNeverBlocked:
    def test_low_stock_after_a_sale_notifies_once_per_day_and_the_sale_is_intact(
        self, client_a, shop, tenant_a
    ):
        client_a.patch(f"/api/v1/products/{shop['rice']['id']}", json={"reorder_level": "15"})
        made = sold(client_a, [item(shop["rice"], "10")])  # 20 -> 10, below 15
        sold(client_a, [item(shop["rice"], "1")])
        body = inbox(client_a)
        assert made["status"] == "POSTED" and body["total"] == 1
        assert (
            body["items"][0]["message"] == "Product RICE is below its reorder level."
            and body["items"][0]["entity_type"] == "product"
        )

    def test_out_of_stock_has_its_own_wording(self, client_a, shop):
        client_a.patch(f"/api/v1/products/{shop['rice']['id']}", json={"reorder_level": "5"})
        sold(client_a, [item(shop["rice"], "20")])
        assert inbox(client_a)["items"][0]["message"] == "Product RICE is out of stock."

    def test_a_notification_failure_does_not_roll_back_the_sale(
        self, client_a, shop, session_factory, monkeypatch
    ):
        client_a.patch(f"/api/v1/products/{shop['rice']['id']}", json={"reorder_level": "15"})

        def boom(*a, **k):
            raise RuntimeError("notification store exploded")

        monkeypatch.setattr(ns, "emit", boom)
        made = sold(client_a, [item(shop["rice"], "10")])
        assert made["status"] == "POSTED" and made["invoice_no"]
        with session_factory() as s:
            assert s.scalar(select(func.count()).select_from(Sale).where(Sale.status == "POSTED")) == 1
            event = s.scalars(select(SystemEvent).where(SystemEvent.category == "notification")).first()
            assert event is not None and event.code == "emit_failed"
        assert (
            client_a.get(f"/api/v1/inventory/products/{shop['rice']['id']}").json()["current_stock"]
            == "10.000"
        )

    def test_a_khata_payment_notifies_without_amounts_or_names_and_survives_a_failure(
        self, client_a, shop, monkeypatch
    ):
        c = client_a.post("/api/v1/customers", json={"name": "Ramesh Kumar", "phone": "9876543210"}).json()[
            "customer"
        ]
        r = client_a.post(f"/api/v1/customers/{c['id']}/payments", json={"amount": "250"})
        assert r.status_code in (200, 201)
        row = inbox(client_a)["items"][0]
        assert (
            row["event_type"] == "PAYMENT_RECEIVED"
            and "250" not in row["message"]
            and "Ramesh" not in row["message"]
            and "9876" not in row["message"]
        )
        monkeypatch.setattr(ns, "emit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        assert client_a.post(f"/api/v1/customers/{c['id']}/payments", json={"amount": "10"}).status_code in (
            200,
            201,
        )

    def test_reaching_a_plan_limit_notifies_once(self, client_a, tenant_a, give_plan, session_factory):
        from app.models import PlanFeature
        from app.services import entitlement_service

        give_plan(tenant_a, "free")
        with session_factory() as s, s.begin():
            plan = entitlement_service.get_plan(s, "free")
            s.execute(
                PlanFeature.__table__.update()
                .where(PlanFeature.plan_id == plan.id, PlanFeature.feature_key == "max_ai_requests_per_month")
                .values(limit_value=1)
            )
        client_a.post("/api/v1/ai/ask", json={"question": "sales today"})
        rows = inbox(client_a)["items"]
        assert [r["event_type"] for r in rows] == ["AI_USAGE_LIMIT"] and "limit" in rows[0]["message"]

    def test_the_notification_switch_off_creates_nothing_and_breaks_nothing(
        self, client_a, shop, monkeypatch
    ):
        monkeypatch.setenv("KIRANA_FEATURE_NOTIFICATIONS", "false")
        get_settings.cache_clear()
        client_a.patch(f"/api/v1/products/{shop['rice']['id']}", json={"reorder_level": "15"})
        assert sold(client_a, [item(shop["rice"], "10")])["status"] == "POSTED"
        monkeypatch.setenv("KIRANA_FEATURE_NOTIFICATIONS", "true")
        get_settings.cache_clear()
        assert inbox(client_a)["total"] == 0


class TestBusinessHealthAlerts:
    def test_the_health_view_and_refresh_are_neutral_and_once_a_day(self, client_a, shop, tenant_a):
        client_a.patch(f"/api/v1/products/{shop['rice']['id']}", json={"reorder_level": "25"})
        health = client_a.get("/api/v1/account/health").json()
        assert [a["kind"] for a in health["alerts"]] == ["low_stock"] and health["backup"]["state"] in (
            "none",
            "not_configured",
            "recent",
            "stale",
        )
        assert client_a.post(f"{API}/refresh-alerts").json()["unread"] == 1
        assert client_a.post(f"{API}/refresh-alerts").json()["unread"] == 1  # the same day: no second copy

    def test_unusual_activity_uses_neutral_wording(self, client_a, shop):
        from datetime import timedelta as td

        from tests.factories import today_in_shop_timezone

        today = today_in_shop_timezone()
        for back in (8, 9, 10):
            sold(
                client_a,
                [item(shop["sugar"], "5")],
                header={"sale_date": (today - td(days=back)).isoformat()},
            )
        sold(client_a, [item(shop["chips"], "1")])
        text = " ".join(a["message"] for a in client_a.get("/api/v1/account/health").json()["alerts"]).lower()
        assert "changed significantly compared with the selected baseline" in text
        for word in ("fraud", "theft", "stealing", "cheat", "suspect"):
            assert word not in text
