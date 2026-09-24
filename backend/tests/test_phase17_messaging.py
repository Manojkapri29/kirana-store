"""Customer and staff messages, email and gateway adapters against real local protocol servers, consent, retries, campaigns and reports."""

import pytest
from sqlalchemy import func, select

from app.models import MessageDelivery
from app.models.enums import CampaignSendStatus, MessageStatus, NotificationChannel
from app.services import campaign_service, crm_segment_service, messaging_service
from tests import factories
from tests.client_helpers import client_with
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.integration_helpers import FakeGateway, FakeSmtp

API = "/api/v1"
TODAY = today_in_shop_timezone()


@pytest.fixture
def smtp():
    server = FakeSmtp()
    yield server
    server.stop()


@pytest.fixture
def gateway():
    server = FakeGateway()
    yield server
    server.stop()


def _email_on(client, smtp, monkeypatch, *, password="s3cret-pass"):
    monkeypatch.setenv("KIRANA_INTEGRATION_SMTP_PASSWORD", password)
    r = client.put(
        f"{API}/integrations/EMAIL",
        json={
            "provider": "smtp",
            "config": {
                "host": "127.0.0.1",
                "port": smtp.port,
                "security": "none",
                "sender": "shop@example.test",
                "username": "mailer",
            },
            "credential_ref": "KIRANA_INTEGRATION_SMTP_PASSWORD",
            "is_enabled": True,
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "CONFIGURED", r.text


def _sms_on(client, gateway, monkeypatch, channel="SMS", token="gw-token"):
    monkeypatch.setenv("KIRANA_INTEGRATION_GW_TOKEN", token)
    r = client.put(
        f"{API}/integrations/{channel}",
        json={
            "provider": "http_json",
            "config": {"url": gateway.url},
            "credential_ref": "KIRANA_INTEGRATION_GW_TOKEN",
            "is_enabled": True,
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "CONFIGURED", r.text


def _customer(session, tenant, **fields):
    c = factories.make_customer(session, tenant.shop, name="Asha", phone=fields.pop("phone", "9876543210"))
    for k, v in fields.items():
        setattr(c, k, v)
    session.commit()
    return c


def _send(client, customer, key="msg-key-00001", status=201, **over):
    body = {
        "customer_id": customer.id,
        "channel": "EMAIL",
        "kind": "TRANSACTIONAL",
        "purpose": "PAYMENT_CONFIRMATION",
        "subject": "Payment received",
        "body": "Thank you for your payment.",
        **over,
    }
    r = client.post(f"{API}/integrations/messages", json=body, headers={"Idempotency-Key": key})
    assert r.status_code == status, r.text
    return r.json()


class TestEmail:
    def test_a_transactional_email_really_goes_out_over_smtp(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        _email_on(client_a, smtp, monkeypatch)
        c = _customer(session, tenant_a, email="asha@example.test")
        m = _send(client_a, c)
        assert (
            m["status"] == "SENT"
            and m["message"] == "Sent"
            and m["recipient"] == "a***@example.test"
            and m["provider"] == "smtp"
        )
        assert len(smtp.received) == 1 and smtp.received[0]["to"] == "asha@example.test"
        mail = smtp.received[0]["data"]
        assert (
            "Subject: Payment received" in mail
            and "Thank you for your payment." in mail
            and "From: shop@example.test" in mail
        )

    def test_no_provider_says_provider_not_configured_and_sends_nothing(self, client_a, session, tenant_a):
        c = _customer(session, tenant_a, email="asha@example.test")
        m = _send(client_a, c)
        assert m["status"] == "NOT_CONFIGURED" and m["message"] == "Provider Not Configured"

    def test_a_disabled_provider_is_not_used(self, client_a, session, tenant_a, smtp, monkeypatch):
        _email_on(client_a, smtp, monkeypatch)
        client_a.post(f"{API}/integrations/EMAIL/disable")
        c = _customer(session, tenant_a, email="asha@example.test")
        assert _send(client_a, c)["status"] == "NOT_CONFIGURED" and smtp.received == []

    def test_a_customer_without_an_email_is_skipped_not_faked(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        _email_on(client_a, smtp, monkeypatch)
        c = _customer(session, tenant_a)
        m = _send(client_a, c)
        assert m["status"] == "SKIPPED_NO_CONTACT" and smtp.received == []

    def test_header_injection_through_the_subject_or_recipient_is_neutralised(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        _email_on(client_a, smtp, monkeypatch)
        c = _customer(session, tenant_a, email="asha@example.test")
        _send(client_a, c, subject="Hello\r\nBcc: attacker@evil.test")
        headers = smtp.received[0]["data"].split("\n\n")[0]
        assert "Bcc:" not in headers.replace(
            "Subject: Hello Bcc: attacker@evil.test", ""
        )  # folded into the subject text, not a header
        bad = _customer(session, tenant_a, phone="9000000001", email="a@b.test\r\nBcc: x@evil.test")
        assert _send(client_a, bad, key="msg-key-00002")["status"] in ("FAILED", "SKIPPED_NO_CONTACT")
        assert len(smtp.received) == 1

    def test_the_provider_rejecting_the_recipient_is_a_failure_not_a_retry(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        _email_on(client_a, smtp, monkeypatch)
        smtp.mode = "reject"
        c = _customer(session, tenant_a, email="gone@example.test")
        m = _send(client_a, c)
        assert m["status"] == "FAILED" and m["error_code"] == "recipient_rejected" and m["attempts"] == 1

    def test_a_temporary_failure_is_queued_then_retried_by_the_job_and_sent_once(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        _email_on(client_a, smtp, monkeypatch)
        smtp.mode = "temp"
        c = _customer(session, tenant_a, email="asha@example.test")
        m = _send(client_a, c)
        assert m["status"] == "QUEUED" and m["attempts"] == 1 and smtp.received == []
        from datetime import timedelta

        from app.db.types import utc_now

        smtp.mode = "ok"
        session.commit()
        first = messaging_service.process_due(session, now=utc_now() + timedelta(days=1))
        session.commit()
        assert first["sent"] == 1 and len(smtp.received) == 1
        second = messaging_service.process_due(session, now=utc_now() + timedelta(days=2))
        assert second["attempted"] == 0 and len(smtp.received) == 1  # a sent message is never sent again

    def test_retries_stop_at_the_attempt_limit(self, client_a, session, tenant_a, smtp, monkeypatch):
        from datetime import timedelta

        from app.db.types import utc_now

        monkeypatch.setenv("KIRANA_NOTIFICATION_MAX_ATTEMPTS", "2")
        from app.core.config import get_settings

        get_settings.cache_clear()
        _email_on(client_a, smtp, monkeypatch)
        smtp.mode = "temp"
        c = _customer(session, tenant_a, email="asha@example.test")
        _send(client_a, c)
        session.commit()
        messaging_service.process_due(session, now=utc_now() + timedelta(days=1))
        session.commit()
        row = session.scalars(select(MessageDelivery)).one()
        assert row.status is MessageStatus.FAILED and row.attempts == 2

    def test_wrong_credentials_fail_without_retrying(self, client_a, session, tenant_a, smtp, monkeypatch):
        _email_on(client_a, smtp, monkeypatch, password="wrong")
        c = _customer(session, tenant_a, email="asha@example.test")
        m = _send(client_a, c)
        assert m["status"] == "FAILED" and m["error_code"] == "invalid_credentials" and "wrong" not in str(m)

    def test_the_same_key_never_sends_twice(self, client_a, session, tenant_a, smtp, monkeypatch):
        _email_on(client_a, smtp, monkeypatch)
        c = _customer(session, tenant_a, email="asha@example.test")
        first = _send(client_a, c, key="dup-key-00001")
        second = _send(client_a, c, key="dup-key-00001")
        assert first["id"] == second["id"] and len(smtp.received) == 1
        r = client_a.post(
            f"{API}/integrations/messages",
            json={
                "customer_id": c.id,
                "channel": "EMAIL",
                "kind": "TRANSACTIONAL",
                "purpose": "GENERAL",
                "body": "Different",
            },
            headers={"Idempotency-Key": "dup-key-00001"},
        )
        assert r.status_code == 409

    def test_a_server_that_disappears_mid_conversation_is_a_failure(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        _email_on(client_a, smtp, monkeypatch)
        smtp.stop()
        c = _customer(session, tenant_a, email="asha@example.test")
        m = _send(client_a, c)
        assert m["status"] in ("QUEUED", "FAILED") and m["error_code"] in (
            "connection_failed",
            "connection_timeout",
            "provider_error",
        )


class TestConsent:
    def test_marketing_needs_the_customers_opt_in_for_that_channel(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        _email_on(client_a, smtp, monkeypatch)
        c = _customer(session, tenant_a, email="asha@example.test")
        m = _send(client_a, c, kind="MARKETING", purpose="CAMPAIGN")
        assert m["status"] == "SKIPPED_NO_CONSENT" and smtp.received == []
        c.marketing_opt_in_sms = True  # a different channel's consent does not count
        session.commit()
        assert (
            _send(client_a, c, key="msg-key-00002", kind="MARKETING", purpose="CAMPAIGN")["status"]
            == "SKIPPED_NO_CONSENT"
        )
        c.marketing_opt_in_email = True
        session.commit()
        assert (
            _send(client_a, c, key="msg-key-00003", kind="MARKETING", purpose="CAMPAIGN")["status"] == "SENT"
        )

    def test_transactional_does_not_need_marketing_consent(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        _email_on(client_a, smtp, monkeypatch)
        c = _customer(session, tenant_a, email="asha@example.test")
        assert _send(client_a, c, kind="TRANSACTIONAL")["status"] == "SENT"

    def test_consent_withdrawn_before_a_retry_stops_the_marketing_message(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        from datetime import timedelta

        from app.db.types import utc_now

        _email_on(client_a, smtp, monkeypatch)
        smtp.mode = "temp"
        c = _customer(session, tenant_a, email="asha@example.test", marketing_opt_in_email=True)
        assert _send(client_a, c, kind="MARKETING", purpose="CAMPAIGN")["status"] == "QUEUED"
        c.marketing_opt_in_email = False
        session.commit()
        smtp.mode = "ok"
        messaging_service.process_due(session, now=utc_now() + timedelta(days=1))
        session.commit()
        assert (
            session.scalars(select(MessageDelivery)).one().status is MessageStatus.SKIPPED_NO_CONSENT
            and smtp.received == []
        )

    def test_a_marketing_message_must_be_a_campaign_or_general(self, client_a, session, tenant_a):
        c = _customer(session, tenant_a, email="a@b.test")
        assert _send(client_a, c, status=422, kind="MARKETING", purpose="INVOICE")

    def test_bad_input_is_refused(self, client_a, session, tenant_a):
        c = _customer(session, tenant_a, email="a@b.test")
        assert _send(client_a, c, status=422, channel="IN_APP")
        assert _send(client_a, c, status=422, purpose="NOPE")
        assert _send(client_a, c, status=422, body="   ")
        assert _send(client_a, c, status=422, body="x" * 1001)
        assert (
            client_a.post(
                f"{API}/integrations/messages",
                json={
                    "customer_id": 999999,
                    "channel": "EMAIL",
                    "kind": "TRANSACTIONAL",
                    "purpose": "GENERAL",
                    "body": "hi",
                },
            ).status_code
            == 404
        )

    def test_a_message_cannot_reach_another_shops_customer(
        self, client_a, client_b, session, tenant_a, tenant_b, smtp, monkeypatch
    ):
        _email_on(client_b, smtp, monkeypatch)
        c = _customer(session, tenant_a, email="asha@example.test")
        assert (
            client_b.post(
                f"{API}/integrations/messages",
                json={
                    "customer_id": c.id,
                    "channel": "EMAIL",
                    "kind": "TRANSACTIONAL",
                    "purpose": "GENERAL",
                    "body": "hi",
                },
            ).status_code
            == 404
        )
        assert smtp.received == [] and client_b.get(f"{API}/integrations/messages").json()["items"] == []


class TestGatewayChannels:
    def test_sms_goes_out_through_the_gateway_with_the_bearer_token_and_no_email_channel_confusion(
        self, client_a, session, tenant_a, gateway, monkeypatch
    ):
        _sms_on(client_a, gateway, monkeypatch)
        c = _customer(session, tenant_a)
        m = _send(client_a, c, channel="SMS", subject=None)
        assert m["status"] == "SENT" and gateway.requests[0]["auth"] == "Bearer gw-token"
        body = gateway.requests[0]["body"]
        assert body["to"] == "9876543210" and body["channel"] == "SMS" and "Thank you" in body["message"]

    def test_whatsapp_uses_its_own_integration(self, client_a, session, tenant_a, gateway, monkeypatch):
        _sms_on(client_a, gateway, monkeypatch)
        c = _customer(session, tenant_a)
        assert (
            _send(client_a, c, channel="WHATSAPP")["status"] == "NOT_CONFIGURED"
        )  # SMS being set up does not enable WhatsApp
        _sms_on(client_a, gateway, monkeypatch, channel="WHATSAPP")
        assert _send(client_a, c, key="msg-key-00002", channel="WHATSAPP")["status"] == "SENT"

    def test_a_gateway_that_rejects_the_token_is_a_credential_failure(
        self, client_a, session, tenant_a, gateway, monkeypatch
    ):
        _sms_on(client_a, gateway, monkeypatch, token="wrong-token")
        c = _customer(session, tenant_a)
        assert _send(client_a, c, channel="SMS")["error_code"] == "invalid_credentials"

    def test_gateway_5xx_is_queued_and_4xx_is_not(self, client_a, session, tenant_a, gateway, monkeypatch):
        _sms_on(client_a, gateway, monkeypatch)
        c = _customer(session, tenant_a)
        gateway.status = 503
        assert _send(client_a, c, channel="SMS")["status"] == "QUEUED"
        gateway.status = 422
        assert _send(client_a, c, key="msg-key-00002", channel="SMS")["status"] == "FAILED"

    def test_the_gateway_is_never_called_for_a_customer_without_a_phone(
        self, client_a, session, tenant_a, gateway, monkeypatch
    ):
        _sms_on(client_a, gateway, monkeypatch)
        c = factories.make_customer(session, tenant_a.shop, name="NoPhone", phone=None)
        session.commit()
        assert _send(client_a, c, channel="SMS")["status"] == "SKIPPED_NO_CONTACT" and gateway.requests == []

    def test_push_has_no_customer_address_so_it_is_skipped(
        self, client_a, session, tenant_a, gateway, monkeypatch
    ):
        _sms_on(client_a, gateway, monkeypatch, channel="PUSH")
        c = _customer(session, tenant_a)
        assert _send(client_a, c, channel="PUSH")["status"] == "SKIPPED_NO_CONTACT"

    def test_a_private_gateway_address_is_never_called_in_production(self, monkeypatch):
        from app.core.config import get_settings
        from app.integrations import http_json
        from app.integrations.base import ProviderError

        monkeypatch.setenv("KIRANA_ENVIRONMENT", "production")
        monkeypatch.setenv("KIRANA_SECRET_KEY", "x" * 40)
        get_settings.cache_clear()
        provider = http_json.HttpJsonProvider(channel="SMS", url="https://169.254.169.254/latest", token="t")
        with pytest.raises(ProviderError) as info:
            provider.send(recipient="123", title="", message="m", timeout=1)
        assert info.value.code == "url_not_allowed"


class TestCampaigns:
    def _group(self, session, ctx, ids):
        group = crm_segment_service.create_manual_group(session, ctx, name="G", customer_ids=ids)
        session.commit()
        return group

    def test_a_campaign_reaches_only_customers_who_opted_in_and_records_the_truth(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        _email_on(client_a, smtp, monkeypatch)
        ctx = context_for(tenant_a)
        yes = _customer(session, tenant_a, email="yes@example.test", marketing_opt_in_email=True)
        no = factories.make_customer(session, tenant_a.shop, name="No", phone="9111111111")
        no.email = "no@example.test"
        nomail = factories.make_customer(session, tenant_a.shop, name="NoMail", phone="9222222222")
        nomail.marketing_opt_in_email = True
        session.commit()
        group = self._group(session, ctx, [yes.id, no.id, nomail.id])
        campaign = campaign_service.create(
            session,
            ctx,
            name="Diwali",
            description=None,
            channel=NotificationChannel.EMAIL,
            target_group_id=group.id,
            target_segment=None,
            promotion_id=None,
            message_template="Festival offers!",
        )
        session.commit()
        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()
        outcomes = {
            o.customer_id: o for o in campaign_service.send_outcomes(session, tenant_a.shop.id, campaign.id)
        }
        assert outcomes[yes.id].status is CampaignSendStatus.SENT
        assert outcomes[no.id].status is CampaignSendStatus.SKIPPED_NO_CONSENT
        assert (
            outcomes[nomail.id].status is CampaignSendStatus.FAILED
            and "no contact" in outcomes[nomail.id].detail.lower()
        )
        assert [m["to"] for m in smtp.received] == ["yes@example.test"]
        rows = session.scalars(
            select(MessageDelivery).where(MessageDelivery.campaign_id == campaign.id)
        ).all()
        assert {r.status for r in rows} >= {MessageStatus.SENT}

    def test_a_campaign_with_no_provider_says_not_configured_for_everyone(self, session, tenant_a):
        ctx = context_for(tenant_a)
        c = _customer(session, tenant_a, email="a@b.test", marketing_opt_in_email=True)
        group = self._group(session, ctx, [c.id])
        campaign = campaign_service.create(
            session,
            ctx,
            name="X",
            description=None,
            channel=NotificationChannel.EMAIL,
            target_group_id=group.id,
            target_segment=None,
            promotion_id=None,
            message_template="Hi",
        )
        session.commit()
        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()
        assert [o.status for o in campaign_service.send_outcomes(session, tenant_a.shop.id, campaign.id)] == [
            CampaignSendStatus.NOT_CONFIGURED
        ]

    def test_a_campaign_provider_failure_is_recorded_as_failed_never_sent(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        _email_on(client_a, smtp, monkeypatch)
        smtp.mode = "temp"
        ctx = context_for(tenant_a)
        c = _customer(session, tenant_a, email="a@b.test", marketing_opt_in_email=True)
        group = self._group(session, ctx, [c.id])
        campaign = campaign_service.create(
            session,
            ctx,
            name="X",
            description=None,
            channel=NotificationChannel.EMAIL,
            target_group_id=group.id,
            target_segment=None,
            promotion_id=None,
            message_template="Hi",
        )
        session.commit()
        campaign_service.launch(session, ctx, campaign.id, TODAY)
        session.commit()
        (outcome,) = campaign_service.send_outcomes(session, tenant_a.shop.id, campaign.id)
        assert outcome.status is CampaignSendStatus.FAILED and smtp.received == []


class TestStaffNotifications:
    def test_a_staff_email_notification_uses_the_shops_provider(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        from app.models import NotificationDelivery
        from app.services import notification_service

        _email_on(client_a, smtp, monkeypatch)
        ctx = context_for(tenant_a)
        notification_service.set_preference(session, ctx, "LOW_STOCK", {"email": True})
        notification_service.emit(
            session,
            tenant_a.shop.id,
            "LOW_STOCK",
            title="Low stock",
            message="Sugar is low.",
            dedupe_key="t1",
            entity_type="product",
            entity_id=1,
        )
        session.commit()
        run = notification_service.process_due(session)
        session.commit()
        assert run.sent == 1 and smtp.received[0]["to"] == tenant_a.user.email
        assert (
            session.scalar(
                select(func.count())
                .select_from(NotificationDelivery)
                .where(NotificationDelivery.provider == "smtp")
            )
            == 1
        )


class TestScheduledReportEmail:
    def test_a_scheduled_report_is_emailed_once_and_says_so(
        self, client_a, session, tenant_a, smtp, monkeypatch
    ):
        _email_on(client_a, smtp, monkeypatch)
        r = client_a.post(
            f"{API}/scheduled-reports/advanced",
            json={
                "kind": "adv_sales",
                "schedule": "DAILY",
                "delivery_channel": "EMAIL",
                "recipients": ["boss@example.test", "acct@example.test"],
            },
        )
        assert r.status_code == 201, r.text
        rid = r.json()["id"]
        for _ in range(3):
            client_a.post(f"{API}/scheduled-reports/{rid}/run-now")
        (run,) = client_a.get(f"{API}/scheduled-reports/{rid}/runs").json()
        assert run["delivery_status"] == "SENT" and run["summary"]["delivery"] == "Email sent"
        assert sorted(m["to"] for m in smtp.received) == [
            "acct@example.test",
            "boss@example.test",
        ]  # three runs, two mails
        assert "Sales summary" in smtp.received[0]["data"] and "combined_revenue" in smtp.received[0]["data"]

    def test_without_a_provider_the_run_says_delivery_channel_not_configured(self, client_a):
        rid = client_a.post(
            f"{API}/scheduled-reports/advanced",
            json={
                "kind": "adv_sales",
                "schedule": "DAILY",
                "delivery_channel": "EMAIL",
                "recipients": ["boss@example.test"],
            },
        ).json()["id"]
        client_a.post(f"{API}/scheduled-reports/{rid}/run-now")
        (run,) = client_a.get(f"{API}/scheduled-reports/{rid}/runs").json()
        assert (
            run["delivery_status"] == "NOT_CONFIGURED"
            and run["summary"]["delivery"] == "Delivery Channel Not Configured"
        )


class TestPermissions:
    def test_sending_needs_the_notification_permission_and_the_log_needs_logs(
        self, session, tenant_a, make_client
    ):
        c = _customer(session, tenant_a, email="a@b.test")
        body = {
            "customer_id": c.id,
            "channel": "EMAIL",
            "kind": "TRANSACTIONAL",
            "purpose": "GENERAL",
            "body": "hi",
        }
        assert (
            client_with(make_client, tenant_a, ["INTEGRATION_VIEW", "INTEGRATION_LOGS"])
            .post(f"{API}/integrations/messages", json=body)
            .status_code
            == 403
        )
        assert (
            client_with(make_client, tenant_a, ["NOTIFICATION_INTEGRATION_MANAGE"])
            .get(f"{API}/integrations/messages")
            .status_code
            == 403
        )
        assert (
            client_with(make_client, tenant_a, ["INTEGRATION_LOGS"])
            .get(f"{API}/integrations/messages")
            .status_code
            == 200
        )


class TestSecretsNeverLeak:
    def test_a_failing_provider_writes_no_secret_to_logs_events_rows_or_responses(
        self, client_a, session, tenant_a, smtp, monkeypatch, caplog
    ):
        import logging

        from sqlalchemy import text

        caplog.set_level(logging.DEBUG)
        secret = "wrong-Password-Value-98765"
        _email_on(client_a, smtp, monkeypatch, password=secret)
        c = _customer(session, tenant_a, email="asha@example.test")
        response = client_a.post(
            f"{API}/integrations/messages",
            json={
                "customer_id": c.id,
                "channel": "EMAIL",
                "kind": "TRANSACTIONAL",
                "purpose": "GENERAL",
                "body": "hi",
            },
        )
        everything = response.text + caplog.text
        for table in (
            "integrations",
            "integration_events",
            "message_deliveries",
            "system_events",
            "audit_log",
        ):
            everything += " ".join(
                str(v) for row in session.execute(text(f"SELECT * FROM {table}")) for v in row
            )
        everything += client_a.get(f"{API}/dashboard").text + client_a.get(f"{API}/logs").text
        assert secret not in everything and "gw-token" not in everything

    def test_the_gateway_token_is_not_in_any_response_row_or_log(
        self, client_a, session, tenant_a, gateway, monkeypatch, caplog
    ):
        import logging

        from sqlalchemy import text

        caplog.set_level(logging.DEBUG)
        _sms_on(client_a, gateway, monkeypatch, token="gw-token")
        c = _customer(session, tenant_a)
        response = client_a.post(
            f"{API}/integrations/messages",
            json={
                "customer_id": c.id,
                "channel": "SMS",
                "kind": "TRANSACTIONAL",
                "purpose": "GENERAL",
                "body": "hi",
            },
        )
        rows = " ".join(
            str(v)
            for t in ("integrations", "integration_events", "message_deliveries", "audit_log")
            for r in session.execute(text(f"SELECT * FROM {t}"))
            for v in r
        )
        assert "gw-token" not in response.text + caplog.text + rows
