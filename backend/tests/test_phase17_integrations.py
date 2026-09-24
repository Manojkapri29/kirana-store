"""Integration framework: provider catalogue, configuration without secrets, honest status, call log and retry rule, RBAC, isolation."""

import pytest

from app.integrations import base
from app.integrations.base import ProviderError
from app.models.enums import IntegrationStatus, IntegrationType
from app.services import integration_service as svc
from tests.client_helpers import client_with
from tests.conftest import context_for
from tests.integration_helpers import FakeGateway, FakeSmtp

API = "/api/v1/integrations"
SMTP_CFG = {"host": "127.0.0.1", "port": 2525, "security": "none", "sender": "shop@example.test"}


def _put(client, itype, body, status=200):
    r = client.put(f"{API}/{itype}", json=body)
    assert r.status_code == status, r.text
    return r.json()


class TestSecrets:
    def test_only_kirana_integration_variables_can_be_read(self, monkeypatch):
        monkeypatch.setenv("KIRANA_INTEGRATION_MAIL", "value-1")
        monkeypatch.setenv("KIRANA_DATABASE_URL", "sqlite:///secret.db")
        monkeypatch.setenv("HOME_SECRET", "nope")
        assert base.resolve_secret("KIRANA_INTEGRATION_MAIL") == "value-1"
        for ref in (
            "KIRANA_DATABASE_URL",
            "HOME_SECRET",
            "KIRANA_SECRET_KEY",
            "PATH",
            "kirana_integration_mail",
            "KIRANA_INTEGRATION_",
            None,
            "",
        ):
            assert base.resolve_secret(ref) is None, ref

    def test_masking_keeps_only_enough_to_recognise(self):
        assert base.mask_recipient("ravi@example.com") == "r***@example.com"
        assert base.mask_recipient("+919876543210").endswith("3210") and "98765" not in base.mask_recipient(
            "+919876543210"
        )

    def test_header_injection_is_stripped(self):
        assert "\n" not in base.clean_header("Hello\r\nBcc: x@evil.test") and "\r" not in base.clean_header(
            "a\rb"
        )


class TestOutboundUrls:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://x.test/a",
            "https://user:pw@example.test/a",
            "https:///nohost",
            "javascript:alert(1)",
            "http://example.test/plain",
        ],
    )
    def test_bad_urls_are_refused(self, url):
        with pytest.raises(ProviderError):
            base.safe_outbound_url(url, resolve=False)

    def test_private_hosts_are_refused_in_production(self, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setenv("KIRANA_ENVIRONMENT", "production")
        monkeypatch.setenv("KIRANA_SECRET_KEY", "x" * 40)
        get_settings.cache_clear()
        for url in (
            "https://127.0.0.1/x",
            "https://169.254.169.254/latest/meta-data",
            "https://10.0.0.5/x",
            "https://localhost/x",
            "http://127.0.0.1:9/x",
        ):
            with pytest.raises(ProviderError):
                base.safe_outbound_url(url)

    def test_localhost_http_is_allowed_only_outside_production(self):
        assert base.safe_outbound_url("http://127.0.0.1:9999/x")


class TestConfiguring:
    def test_nothing_is_configured_at_first_and_says_so(self, client_a):
        items = {i["integration_type"]: i for i in client_a.get(API).json()["items"]}
        assert (
            items["EMAIL"]["status"] == "NOT_CONFIGURED"
            and items["EMAIL"]["message"] == "Provider Not Configured"
        )
        assert items["PAYMENT"]["available_providers"][0]["provider"] in ("manual", "generic_webhook")
        platform = {p["integration_type"]: p for p in client_a.get(API).json()["platform"]}
        assert (
            platform["MAPS"]["message"] == "Provider Not Configured"
            and platform["STORAGE"]["provider"] == "local"
        )

    def test_configured_only_when_the_credential_really_exists(self, client_a, monkeypatch):
        body = {
            "provider": "http_json",
            "config": {"url": "https://gw.example.test/send"},
            "credential_ref": "KIRANA_INTEGRATION_SMS_TOKEN",
            "is_enabled": True,
        }
        view = _put(client_a, "SMS", body)
        assert (
            view["status"] == "NOT_CONFIGURED"
            and view["message"] == "Credentials Not Configured"
            and view["credentials_present"] is False
        )
        monkeypatch.setenv("KIRANA_INTEGRATION_SMS_TOKEN", "tok-123456")
        view = _put(client_a, "SMS", body)
        assert view["status"] == "CONFIGURED" and view["credentials_present"] is True

    def test_no_response_or_row_ever_contains_the_secret(self, client_a, monkeypatch, session):
        monkeypatch.setenv("KIRANA_INTEGRATION_SMS_TOKEN", "super-secret-token-value")
        _put(
            client_a,
            "SMS",
            {
                "provider": "http_json",
                "config": {"url": "https://gw.example.test/send"},
                "credential_ref": "KIRANA_INTEGRATION_SMS_TOKEN",
                "is_enabled": True,
            },
        )
        blob = " ".join(
            r.text for r in (client_a.get(API), client_a.get(f"{API}/dashboard"), client_a.get(f"{API}/logs"))
        )
        assert (
            "super-secret-token-value" not in blob and "KIRANA_INTEGRATION_SMS_TOKEN" in blob
        )  # the NAME may be shown, the value never
        from sqlalchemy import text

        stored = " ".join(str(v) for row in session.execute(text("SELECT * FROM integrations")) for v in row)
        assert "super-secret-token-value" not in stored

    @pytest.mark.parametrize(
        "body",
        [
            {"provider": "nope", "config": {}},
            {
                "provider": "smtp",
                "config": {
                    "host": "h.test",
                    "port": 25,
                    "security": "none",
                    "sender": "a@b.test",
                    "password": "x",
                },
            },
            {
                "provider": "smtp",
                "config": {
                    "host": "https://h.test/path",
                    "port": 25,
                    "security": "none",
                    "sender": "a@b.test",
                },
            },
            {
                "provider": "smtp",
                "config": {"host": "h.test", "port": 99999, "security": "none", "sender": "a@b.test"},
            },
            {
                "provider": "smtp",
                "config": {"host": "h.test", "port": 25, "security": "weird", "sender": "a@b.test"},
            },
            {
                "provider": "smtp",
                "config": {"host": "h.test", "port": 25, "security": "none", "sender": "a@b.test"},
                "credential_ref": "DATABASE_URL",
            },
            {
                "provider": "smtp",
                "config": {"host": "h.test", "port": 25, "security": "none", "sender": "a@b.test"},
                "webhook_credential_ref": "KIRANA_INTEGRATION_X",
            },
            {"provider": "smtp", "config": {}, "unknown_field": 1},
        ],
    )
    def test_bad_configuration_is_refused(self, client_a, body):
        assert client_a.put(f"{API}/EMAIL", json=body).status_code == 422

    def test_a_secret_in_the_settings_is_refused_with_advice(self, client_a):
        r = client_a.put(
            f"{API}/SMS",
            json={"provider": "http_json", "config": {"url": "https://g.test/x", "api_key": "abc"}},
        )
        assert r.status_code == 422 and "environment variable" in r.text

    def test_the_url_must_be_https_without_credentials(self, client_a):
        for url in ("http://gw.example.test/x", "https://u:p@gw.example.test/x", "ftp://x"):
            assert (
                client_a.put(f"{API}/SMS", json={"provider": "http_json", "config": {"url": url}}).status_code
                == 422
            )

    def test_types_without_a_provider_cannot_be_configured(self, client_a):
        assert client_a.put(f"{API}/MAPS", json={"provider": "x", "config": {}}).status_code == 422
        assert client_a.put(f"{API}/STORAGE", json={"provider": "x", "config": {}}).status_code == 422
        assert client_a.put(f"{API}/NOPE", json={"provider": "x"}).status_code == 422

    def test_enable_disable_and_rotation(self, client_a, monkeypatch):
        monkeypatch.setenv("KIRANA_INTEGRATION_SMS_TOKEN", "a-token-1234")
        _put(
            client_a,
            "SMS",
            {
                "provider": "http_json",
                "config": {"url": "https://g.test/x"},
                "credential_ref": "KIRANA_INTEGRATION_SMS_TOKEN",
                "is_enabled": True,
            },
        )
        assert client_a.post(f"{API}/SMS/disable").json()["status"] == "DISABLED"
        assert client_a.post(f"{API}/SMS/enable").json()["status"] == "CONFIGURED"
        assert client_a.post(f"{API}/SMS/rotate-credentials", json={}).status_code == 422
        assert (
            client_a.post(f"{API}/SMS/rotate-credentials", json={"credential_ref": "not-allowed"}).status_code
            == 422
        )
        rotated = client_a.post(
            f"{API}/SMS/rotate-credentials", json={"credential_ref": "KIRANA_INTEGRATION_SMS_TOKEN_V2"}
        ).json()
        assert (
            rotated["credential_ref"] == "KIRANA_INTEGRATION_SMS_TOKEN_V2"
            and rotated["rotated_at"]
            and rotated["status"] == "NOT_CONFIGURED"
        )
        monkeypatch.setenv("KIRANA_INTEGRATION_SMS_TOKEN_V2", "new-token-1234")
        assert client_a.post(f"{API}/SMS/enable").json()["status"] == "CONFIGURED"

    def test_an_action_on_an_unset_integration_is_not_found(self, client_a):
        assert client_a.post(f"{API}/PUSH/enable").status_code == 404

    def test_changes_are_audited_without_secrets(self, client_a, session, monkeypatch):
        from sqlalchemy import select

        from app.models import AuditLog

        monkeypatch.setenv("KIRANA_INTEGRATION_SMS_TOKEN", "audit-secret-value")
        _put(
            client_a,
            "SMS",
            {
                "provider": "http_json",
                "config": {"url": "https://g.test/x"},
                "credential_ref": "KIRANA_INTEGRATION_SMS_TOKEN",
            },
        )
        session.expire_all()
        rows = list(session.scalars(select(AuditLog).where(AuditLog.entity_type == "integration")))
        assert rows and "audit-secret-value" not in " ".join(str(r.after_json) for r in rows)


class TestCallsAndMonitoring:
    def _row(self, session, tenant_a, monkeypatch):
        monkeypatch.setenv("KIRANA_INTEGRATION_SMS_TOKEN", "tok-abcdefgh")
        ctx = context_for(tenant_a)
        row = svc.configure(
            session,
            ctx,
            IntegrationType.SMS,
            provider="http_json",
            config={"url": "https://g.test/x"},
            credential_ref="KIRANA_INTEGRATION_SMS_TOKEN",
            is_enabled=True,
        )
        session.commit()
        return row

    def test_safe_calls_retry_transient_failures_and_unsafe_calls_do_not(
        self, session, tenant_a, monkeypatch
    ):
        row = self._row(session, tenant_a, monkeypatch)
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ProviderError("connection_failed", retryable=True)
            return "ok"

        assert (
            svc.call(session, row, "http_json", "status", flaky, kind=base.SAFE, sleep=lambda s: None) == "ok"
            and calls["n"] == 3
        )
        calls["n"] = 0
        with pytest.raises(ProviderError):
            svc.call(session, row, "http_json", "send", flaky, kind=base.UNSAFE, sleep=lambda s: None)
        assert calls["n"] == 1  # an unsafe call is tried once, whatever the error says

    def test_a_non_retryable_failure_is_not_retried_even_when_safe(self, session, tenant_a, monkeypatch):
        row = self._row(session, tenant_a, monkeypatch)
        calls = {"n": 0}

        def bad():
            calls["n"] += 1
            raise ProviderError("invalid_credentials", retryable=False)

        with pytest.raises(ProviderError):
            svc.call(session, row, "http_json", "status", bad, kind=base.SAFE, sleep=lambda s: None)
        assert calls["n"] == 1

    def test_repeated_failures_turn_the_status_to_error_and_a_success_recovers_it(
        self, session, tenant_a, monkeypatch
    ):
        row = self._row(session, tenant_a, monkeypatch)

        def bad():
            raise ProviderError("http_500", retryable=False)

        for _ in range(3):
            with pytest.raises(ProviderError):
                svc.call(session, row, "http_json", "send", bad, kind=base.UNSAFE)
        assert (
            row.status is IntegrationStatus.ERROR
            and row.failure_count == 3
            and row.last_error_code == "http_500"
            and row.last_failure_at
        )
        svc.call(session, row, "http_json", "send", lambda: "fine", kind=base.UNSAFE)
        assert row.status is IntegrationStatus.CONFIGURED and row.failure_count == 0 and row.last_success_at

    def test_an_adapter_bug_is_a_failed_call_not_a_crash(self, session, tenant_a, monkeypatch):
        row = self._row(session, tenant_a, monkeypatch)
        with pytest.raises(ProviderError) as info:
            svc.call(session, row, "http_json", "send", lambda: 1 / 0, kind=base.UNSAFE)
        assert info.value.code == "provider_error"

    def test_the_log_and_dashboard_carry_no_payload(self, client_a, session, tenant_a, monkeypatch):
        row = self._row(session, tenant_a, monkeypatch)
        svc.note_result(
            session,
            row,
            itype=row.integration_type,
            provider="http_json",
            operation="send_message",
            ok=False,
            error_code="http_500",
        )
        session.commit()
        logs = client_a.get(f"{API}/logs").json()["items"]
        assert (
            logs[0]["operation"] == "send_message"
            and logs[0]["outcome"] == "FAILURE"
            and set(logs[0])
            == {
                "id",
                "integration_type",
                "provider",
                "operation",
                "outcome",
                "error_code",
                "duration_ms",
                "created_at",
            }
        )
        d = client_a.get(f"{API}/dashboard").json()
        sms = next(i for i in d["integrations"] if i["integration_type"] == "SMS")
        assert sms["last_failure_at"] and sms["failure_count"] == 1 and sms["last_error_code"] == "http_500"
        assert set(d["summary"]) == {"configured", "errors", "not_configured", "disabled"}


class TestConnectionTests:
    def test_smtp_connection_is_checked_without_sending_mail(self, client_a, monkeypatch):
        smtp = FakeSmtp()
        try:
            monkeypatch.setenv("KIRANA_INTEGRATION_SMTP_PASSWORD", "s3cret-pass")
            _put(
                client_a,
                "EMAIL",
                {
                    "provider": "smtp",
                    "config": {**SMTP_CFG, "port": smtp.port, "username": "mailer"},
                    "credential_ref": "KIRANA_INTEGRATION_SMTP_PASSWORD",
                    "is_enabled": True,
                },
            )
            result = client_a.post(f"{API}/EMAIL/test").json()
            assert result == {
                "ok": True,
                "code": None,
                "message": "The connection and credentials were accepted.",
            }
            assert smtp.received == []  # nothing was sent
        finally:
            smtp.stop()

    def test_wrong_smtp_credentials_are_reported_safely(self, client_a, monkeypatch):
        smtp = FakeSmtp()
        try:
            monkeypatch.setenv("KIRANA_INTEGRATION_SMTP_PASSWORD", "wrong-password")
            _put(
                client_a,
                "EMAIL",
                {
                    "provider": "smtp",
                    "config": {**SMTP_CFG, "port": smtp.port, "username": "mailer"},
                    "credential_ref": "KIRANA_INTEGRATION_SMTP_PASSWORD",
                    "is_enabled": True,
                },
            )
            result = client_a.post(f"{API}/EMAIL/test").json()
            assert (
                result["ok"] is False
                and result["code"] == "invalid_credentials"
                and "wrong-password" not in str(result)
            )
        finally:
            smtp.stop()

    def test_an_unreachable_server_is_a_failed_test_not_a_500(self, client_a, monkeypatch):
        _put(client_a, "EMAIL", {"provider": "smtp", "config": {**SMTP_CFG, "port": 9}, "is_enabled": True})
        result = client_a.post(f"{API}/EMAIL/test").json()
        assert result["ok"] is False and result["code"] in ("connection_failed", "connection_timeout")

    def test_testing_an_unconfigured_integration_says_so(self, client_a):
        _put(
            client_a,
            "SMS",
            {
                "provider": "http_json",
                "config": {"url": "https://g.test/x"},
                "credential_ref": "KIRANA_INTEGRATION_SMS_TOKEN",
                "is_enabled": True,
            },
        )
        assert client_a.post(f"{API}/SMS/test").json()["code"] == "not_configured"

    def test_gateway_check_needs_no_call_and_never_sends(self, client_a, monkeypatch):
        gw = FakeGateway()
        try:
            monkeypatch.setenv("KIRANA_INTEGRATION_SMS_TOKEN", "gw-token")
            base_url = gw.url  # http://127.0.0.1: allowed in tests, refused in production
            svc_cfg = {
                "provider": "http_json",
                "config": {"url": base_url},
                "credential_ref": "KIRANA_INTEGRATION_SMS_TOKEN",
                "is_enabled": True,
            }
            r = client_a.put(f"{API}/SMS", json=svc_cfg)
            assert r.status_code == 200, r.text
            assert client_a.post(f"{API}/SMS/test").json()["ok"] is True and gw.requests == []
        finally:
            gw.stop()


class TestPermissions:
    def test_each_route_needs_its_own_permission(self, session, tenant_a, make_client):
        view = client_with(make_client, tenant_a, ["INTEGRATION_VIEW"])
        assert view.get(API).status_code == 200 and view.get(f"{API}/dashboard").status_code == 200
        assert view.get(f"{API}/logs").status_code == 403
        assert view.get(f"{API}/webhook-events").status_code == 403
        assert view.put(f"{API}/EMAIL", json={"provider": "smtp", "config": {}}).status_code == 403
        assert view.post(f"{API}/EMAIL/test").status_code == 403
        assert view.post(f"{API}/EMAIL/enable").status_code == 403
        assert view.post(f"{API}/PAYMENT/rotate-webhook-key").status_code == 403
        assert view.post(f"{API}/platform/storage/test").status_code == 403
        assert client_with(make_client, tenant_a, []).get(API).status_code == 403

    def test_the_kind_of_integration_has_its_own_permission(self, session, tenant_a, make_client):
        mail_only = client_with(
            make_client,
            tenant_a,
            ["INTEGRATION_CONFIGURE", "INTEGRATION_MANAGE", "NOTIFICATION_INTEGRATION_MANAGE"],
        )
        assert mail_only.put(f"{API}/EMAIL", json={"provider": "smtp", "config": SMTP_CFG}).status_code == 200
        assert mail_only.put(f"{API}/PAYMENT", json={"provider": "manual", "config": {}}).status_code == 403
        pay_only = client_with(make_client, tenant_a, ["INTEGRATION_CONFIGURE", "PAYMENT_INTEGRATION_MANAGE"])
        assert pay_only.put(f"{API}/PAYMENT", json={"provider": "manual", "config": {}}).status_code == 200
        assert (
            pay_only.put(
                f"{API}/SMS", json={"provider": "http_json", "config": {"url": "https://g.test/x"}}
            ).status_code
            == 403
        )

    def test_system_role_defaults(self):
        from app.core.permissions import SYSTEM_ROLES

        grants = {c: set(r[2]) for c, r in SYSTEM_ROLES.items()}
        owner_only = {
            "INTEGRATION_MANAGE",
            "INTEGRATION_CONFIGURE",
            "STORAGE_INTEGRATION_MANAGE",
            "WEBHOOK_MANAGE",
        }
        assert owner_only <= grants["OWNER"] and not owner_only & grants["MANAGER"]
        assert {
            "INTEGRATION_VIEW",
            "INTEGRATION_TEST",
            "INTEGRATION_LOGS",
            "PAYMENT_INTEGRATION_MANAGE",
            "NOTIFICATION_INTEGRATION_MANAGE",
        } <= grants["MANAGER"]
        assert "INTEGRATION_VIEW" in grants["ACCOUNTANT"] and not any(
            p.startswith(("INTEGRATION_", "PAYMENT_INTEGRATION", "WEBHOOK_")) for p in grants["CASHIER"]
        )


class TestIsolation:
    def test_another_shops_configuration_is_invisible_and_untouchable(self, client_a, client_b, monkeypatch):
        _put(
            client_a,
            "SMS",
            {
                "provider": "http_json",
                "config": {"url": "https://g.test/a"},
                "credential_ref": "KIRANA_INTEGRATION_A",
                "is_enabled": True,
            },
        )
        b = {i["integration_type"]: i for i in client_b.get(API).json()["items"]}
        assert b["SMS"]["provider"] is None and b["SMS"]["config"] == {}
        assert client_b.post(f"{API}/SMS/enable").status_code == 404
        assert client_b.get(f"{API}/logs").json()["items"] == []
