"""System administration: identity, server-enforced permissions, audit, support access, shop lifecycle, entitlements."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core import ratelimit
from app.core.config import get_settings
from app.db.types import utc_now
from app.models import AdminAuditLog, AuditLog, Product, SupportAccessGrant, SystemAdmin
from app.models.enums import AccountStatus, AdminRole
from app.services import admin_service
from tests.test_sales_api import item, sold  # noqa: F401
from tests.test_sales_api import shelf as shelf

ADMIN = "/api/v1/admin"


@pytest.fixture
def make_admin(session_factory):
    def _make(role: AdminRole, email: str | None = None) -> str:
        with session_factory() as s, s.begin():
            _admin, token = admin_service.create_admin(
                s, email=email or f"{role.value.lower()}@ops.test", display_name=role.value, role=role
            )
        return token

    return _make


def call(client, method, path, token, **kw):
    return client.request(method, f"{ADMIN}{path}", headers={"X-Admin-Token": token} if token else {}, **kw)


class TestIdentity:
    def test_the_token_is_shown_once_and_only_its_hash_is_stored(self, session_factory):
        with session_factory() as s, s.begin():
            admin, token = admin_service.create_admin(
                s, email="A@Ops.test", display_name="A", role=AdminRole.SUPER_ADMIN
            )
        with session_factory() as s:
            row = s.scalars(select(SystemAdmin)).one()
            assert (
                row.email == "a@ops.test"
                and token not in (row.token_hash, row.token_prefix)
                and row.token_hash == admin_service.hash_token(token)
            )
            assert token.startswith("kadm_") and len(token) > 40 and row.token_prefix == token[:10]

    def test_a_duplicate_email_and_a_bad_email_are_refused(self, session_factory):
        from app.services.errors import ConflictError, InvalidInputError

        with session_factory() as s, s.begin():
            admin_service.create_admin(s, email="a@ops.test", display_name="A", role=AdminRole.SUPER_ADMIN)
        with session_factory() as s, pytest.raises(ConflictError):
            with s.begin():
                admin_service.create_admin(
                    s, email="a@ops.test", display_name="B", role=AdminRole.SUPPORT_ADMIN
                )
        with session_factory() as s, pytest.raises(InvalidInputError):
            with s.begin():
                admin_service.create_admin(s, email="nope", display_name="B", role=AdminRole.SUPPORT_ADMIN)

    def test_rotating_a_token_ends_the_old_one_and_deactivating_ends_all(
        self, client_a, make_admin, session_factory
    ):
        old = make_admin(AdminRole.SUPER_ADMIN, "s@ops.test")
        assert call(client_a, "GET", "/me", old).status_code == 200
        with session_factory() as s, s.begin():
            new = admin_service.rotate_token(s, "s@ops.test")
        assert (
            call(client_a, "GET", "/me", old).status_code == 401
            and call(client_a, "GET", "/me", new).status_code == 200
        )
        with session_factory() as s, s.begin():
            admin_service.deactivate(s, "s@ops.test")
        assert call(client_a, "GET", "/me", new).status_code == 401


class TestAuthenticationAndPermissions:
    @pytest.mark.parametrize("token", [None, "", "nope", "kadm_" + "x" * 60, "Bearer abc"])
    def test_a_missing_or_wrong_token_is_401_and_audited(self, client_a, token, session_factory):
        response = call(client_a, "GET", "/shops", token)
        assert response.status_code == 401 and response.json()["category"] == "authentication"
        with session_factory() as s:
            row = s.scalars(select(AdminAuditLog).order_by(AdminAuditLog.id.desc())).first()
            assert (row.admin_id, row.outcome, row.detail) == (None, "DENIED", {"reason": "authentication"})

    def test_a_shop_context_is_not_an_admin_identity(self, client_a):
        assert client_a.get(f"{ADMIN}/shops").status_code == 401  # the shop owner's own access is not enough

    MATRIX = [
        # (method, path, body, roles that may call it)
        ("GET", "/shops", None, {"SUPER_ADMIN", "OPERATIONS_ADMIN", "SUPPORT_ADMIN"}),
        ("GET", "/events", None, {"SUPER_ADMIN", "OPERATIONS_ADMIN", "SUPPORT_ADMIN"}),
        ("GET", "/system/health", None, {"SUPER_ADMIN", "OPERATIONS_ADMIN"}),
        ("GET", "/backups", None, {"SUPER_ADMIN", "OPERATIONS_ADMIN"}),
        ("GET", "/integrity", None, {"SUPER_ADMIN", "OPERATIONS_ADMIN"}),
        ("GET", "/audit", None, {"SUPER_ADMIN"}),
        ("POST", "/shops/1/subscription", {"plan_code": "pro"}, {"SUPER_ADMIN"}),
        (
            "POST",
            "/shops/1/status",
            {"status": "ACTIVE", "reason": "test"},
            {"SUPER_ADMIN", "OPERATIONS_ADMIN"},
        ),
        ("POST", "/shops/1/support-grants", {"admin_email": "x@ops.test", "reason": "r"}, {"SUPER_ADMIN"}),
        ("POST", "/backups/retention", {"apply": False}, {"SUPER_ADMIN", "OPERATIONS_ADMIN"}),
        ("POST", "/backups/bkp_x/restore", {"confirmation": "x"}, {"SUPER_ADMIN"}),
    ]

    @pytest.mark.parametrize(("method", "path", "body", "allowed"), MATRIX)
    def test_every_route_enforces_its_permission_on_the_server(
        self, client_a, make_admin, tenant_a, method, path, body, allowed
    ):
        for role in AdminRole:
            token = make_admin(role)
            response = call(client_a, method, path, token, json=body)
            if role.value in allowed:
                assert response.status_code != 403, (role, response.text)
            else:
                assert response.status_code == 403, (role, path, response.status_code)
                assert response.json()["category"] == "authorization"

    def test_denied_and_allowed_calls_are_both_audited_with_route_and_request_id(
        self, client_a, make_admin, session_factory
    ):
        support = make_admin(AdminRole.SUPPORT_ADMIN)
        call(client_a, "GET", "/audit", support, headers=None) if False else client_a.get(
            f"{ADMIN}/audit", headers={"X-Admin-Token": support, "X-Request-ID": "req_adm0001"}
        )
        client_a.get(
            f"{ADMIN}/shops",
            headers={"X-Admin-Token": support, "X-Request-ID": "req_adm0002"},
            params={"status": "ACTIVE"},
        )
        with session_factory() as s:
            rows = {r.request_id: r for r in s.scalars(select(AdminAuditLog))}
            assert (
                rows["req_adm0001"].outcome,
                rows["req_adm0001"].action,
                rows["req_adm0001"].permission,
            ) == ("DENIED", "GET /api/v1/admin/audit", "audit.view")
            assert rows["req_adm0002"].outcome == "ALLOWED" and rows["req_adm0002"].detail == {
                "query": ["status"]
            }

    def test_the_admin_audit_log_is_insert_only(self, client_a, make_admin, session_factory):
        from tests.conftest import assert_sql_rejected

        make_admin(AdminRole.SUPER_ADMIN)
        call(client_a, "GET", "/me", make_admin(AdminRole.SUPPORT_ADMIN))
        with session_factory() as s:
            assert_sql_rejected(s, "UPDATE admin_audit_logs SET action = 'x'", match="insert-only")
            assert_sql_rejected(s, "DELETE FROM admin_audit_logs", match="insert-only")

    def test_repeated_bad_tokens_from_one_address_are_rate_limited(self, client_a, monkeypatch):
        monkeypatch.setenv("KIRANA_RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("KIRANA_RATE_LIMIT_ADMIN_AUTH_FAILURES", "3")
        get_settings.cache_clear()
        ratelimit.limiter.reset()
        codes = [call(client_a, "GET", "/me", "kadm_" + "y" * 50).status_code for _ in range(5)]
        assert codes[:3] == [401, 401, 401] and 429 in codes[3:]


class TestShopsAndLifecycle:
    def test_the_shop_list_shows_account_facts_and_never_business_data(self, client_a, make_admin, shelf):
        token = make_admin(AdminRole.SUPPORT_ADMIN)
        body = call(client_a, "GET", "/shops", token).json()
        row = body["items"][0]
        assert set(row) == {
            "id",
            "name",
            "account_status",
            "status_reason",
            "status_changed_at",
            "plan_code",
            "plan_source",
            "subscription_status",
            "created_at",
            "products",
            "users",
        }
        assert row["products"] == 3 and body["total"] >= 1 and body["page"] == 1 and "total_pages" in body
        assert "sales" not in str(row).lower() and "customer" not in str(row).lower()

    def test_changing_a_shop_state_needs_a_reason_is_audited_and_deletes_nothing(
        self, client_a, make_admin, tenant_a, shelf, session_factory
    ):
        token = make_admin(AdminRole.OPERATIONS_ADMIN)
        assert (
            call(
                client_a,
                "POST",
                f"/shops/{tenant_a.shop.id}/status",
                token,
                json={"status": "SUSPENDED", "reason": "  "},
            ).status_code
            == 422
        )
        done = call(
            client_a,
            "POST",
            f"/shops/{tenant_a.shop.id}/status",
            token,
            json={"status": "SUSPENDED", "reason": "unpaid invoice"},
        )
        assert (
            done.status_code == 200
            and done.json()["account_status"] == "SUSPENDED"
            and done.json()["status_reason"] == "unpaid invoice"
        )
        with session_factory() as s:
            assert s.scalar(select(func.count()).select_from(Product)) == 3  # nothing was deleted
            shop_audit = s.scalars(
                select(AuditLog).where(AuditLog.entity_type == "shop", AuditLog.action == "account_status")
            ).one()
            assert (
                shop_audit.user_id is None
                and shop_audit.after_json["reason"] == "unpaid invoice"
                and "operations_admin@ops.test" == shop_audit.after_json["by"]
            )
            assert s.scalar(
                select(AdminAuditLog.detail).where(AdminAuditLog.action == "shops.status_change")
            ) == {"status": "SUSPENDED", "reason": "unpaid invoice"}

    def test_an_unknown_shop_is_404(self, client_a, make_admin):
        assert call(client_a, "GET", "/shops/99999", make_admin(AdminRole.SUPER_ADMIN)).status_code == 404

    def test_subscription_changes_are_audited_on_both_sides(
        self, client_a, make_admin, tenant_a, session_factory
    ):
        token = make_admin(AdminRole.SUPER_ADMIN)
        r = call(
            client_a, "POST", f"/shops/{tenant_a.shop.id}/subscription", token, json={"plan_code": "basic"}
        )
        assert r.status_code == 200 and r.json()["plan_code"] == "basic"
        assert call(
            client_a, "POST", f"/shops/{tenant_a.shop.id}/subscription", token, json={"plan_code": "nope"}
        ).status_code in (404, 409, 422)
        with session_factory() as s:
            assert (
                s.scalars(select(AuditLog).where(AuditLog.entity_type == "subscription"))
                .one()
                .after_json["plan"]
                == "basic"
            )
            assert (
                s.scalars(select(AdminAuditLog).where(AdminAuditLog.action == "subscriptions.assign"))
                .one()
                .target_shop_id
                == tenant_a.shop.id
            )


class TestSupportAccess:
    def test_without_a_grant_support_sees_nothing_and_with_one_only_counts(
        self, client_a, make_admin, tenant_a, shelf
    ):
        boss, support = make_admin(AdminRole.SUPER_ADMIN), make_admin(AdminRole.SUPPORT_ADMIN)
        assert call(client_a, "GET", f"/shops/{tenant_a.shop.id}/support-view", support).status_code == 403
        grant = call(
            client_a,
            "POST",
            f"/shops/{tenant_a.shop.id}/support-grants",
            boss,
            json={"admin_email": "support_admin@ops.test", "reason": "ticket 42", "hours": 2},
        )
        assert grant.status_code == 201
        view = call(client_a, "GET", f"/shops/{tenant_a.shop.id}/support-view", support)
        assert view.status_code == 200
        assert (
            set(view.json()) == {"shop", "usage", "events", "integrity"}
            and view.json()["usage"]["products"] == 3
        )
        assert "sale" not in str(view.json()["shop"]).lower() and view.json()["integrity"]["ok"] is True

    def test_a_grant_expires_and_is_reasoned_and_bounded(
        self, client_a, make_admin, tenant_a, session_factory
    ):
        boss, support = make_admin(AdminRole.SUPER_ADMIN), make_admin(AdminRole.SUPPORT_ADMIN)
        url = f"/shops/{tenant_a.shop.id}/support-grants"
        assert (
            call(
                client_a,
                "POST",
                url,
                boss,
                json={"admin_email": "support_admin@ops.test", "reason": " ", "hours": 2},
            ).status_code
            == 422
        )
        assert (
            call(
                client_a,
                "POST",
                url,
                boss,
                json={"admin_email": "support_admin@ops.test", "reason": "r", "hours": 500},
            ).status_code
            == 422
        )
        call(
            client_a,
            "POST",
            url,
            boss,
            json={"admin_email": "support_admin@ops.test", "reason": "r", "hours": 1},
        )
        with session_factory() as s, s.begin():
            s.scalars(select(SupportAccessGrant)).one().expires_at = utc_now() - timedelta(minutes=1)
        assert call(client_a, "GET", f"/shops/{tenant_a.shop.id}/support-view", support).status_code == 403

    def test_a_grant_for_one_shop_does_not_open_another(self, client_a, make_admin, tenant_a, tenant_b):
        boss, support = make_admin(AdminRole.SUPER_ADMIN), make_admin(AdminRole.SUPPORT_ADMIN)
        call(
            client_a,
            "POST",
            f"/shops/{tenant_a.shop.id}/support-grants",
            boss,
            json={"admin_email": "support_admin@ops.test", "reason": "r", "hours": 2},
        )
        assert call(client_a, "GET", f"/shops/{tenant_b.shop.id}/support-view", support).status_code == 403


class TestSystemViews:
    def test_system_health_detail_is_for_operators_and_holds_no_secrets(
        self, client_a, make_admin, monkeypatch
    ):
        monkeypatch.setenv("AI_API_KEY", "sk-secret-value-123456")
        get_settings.cache_clear()
        body = call(client_a, "GET", "/system/health", make_admin(AdminRole.OPERATIONS_ADMIN)).json()
        assert (
            body["database"]["connected"] is True
            and body["database"]["up_to_date"] is True
            and body["status"] == "ok"
        )
        assert set(body["feature_flags"]) == {"ai", "exports", "notifications", "price_lookups"}
        text = str(body)
        assert "sk-secret" not in text and "/Users" not in text and "sqlite:///" not in text
        assert client_a.get("/health/ready").json()["checks"] == {
            "database": "ok",
            "schema": "ok",
            "configuration": "ok",
        }  # public: names only

    def test_the_public_endpoints_give_no_detail_at_all(self, client_a):
        assert (
            "revision" not in client_a.get("/health/ready").text
            and client_a.get(f"{ADMIN}/system/health").status_code == 401
        )

    def test_events_and_overview(self, client_a, make_admin, session_factory, tenant_a):
        from app.models.enums import EventSeverity
        from app.services import system_event_service

        with session_factory() as s, s.begin():
            system_event_service.record(s, category="integration", severity=EventSeverity.WARNING, source="price:x", code="unavailable",
                                        message="It failed for a@b.com at /Users/x/file", shop_id=tenant_a.shop.id)  # fmt: skip
        token = make_admin(AdminRole.SUPPORT_ADMIN)
        rows = call(client_a, "GET", "/events", token, params={"category": "integration"}).json()
        assert (
            rows["total"] == 1
            and "a@b.com" not in rows["items"][0]["message"]
            and "/Users" not in rows["items"][0]["message"]
        )
        overview = call(client_a, "GET", "/system/overview", make_admin(AdminRole.OPERATIONS_ADMIN)).json()
        assert (
            overview["shops_by_status"]["ACTIVE"] >= 1
            and overview["events_last_24h"]["integration"]["WARNING"] == 1
        )

    def test_the_owner_never_sees_system_wide_information(self, client_a):
        for path in ("/system/health", "/events", "/audit", "/backups", "/shops"):
            assert client_a.get(f"{ADMIN}{path}").status_code == 401


class TestShopLifecycle:
    def suspend(self, session_factory, tenant, status):
        from app.services import account_service

        with session_factory() as s, s.begin():
            account_service.change_status(s, tenant.shop.id, status, reason="test", actor="ops@test")

    def test_active_and_trial_work_normally(self, client_a, tenant_a, session_factory, shelf):
        for status in (AccountStatus.TRIAL, AccountStatus.ACTIVE):
            self.suspend(session_factory, tenant_a, status)
            assert client_a.get("/api/v1/products").status_code == 200
            assert client_a.post("/api/v1/customers", json={"name": f"C {status.value}"}).status_code == 201

    def test_suspended_is_read_only_by_default_with_a_safe_message(
        self, client_a, tenant_a, session_factory, shelf
    ):
        self.suspend(session_factory, tenant_a, AccountStatus.SUSPENDED)
        assert client_a.get("/api/v1/products").status_code == 200
        blocked = client_a.post("/api/v1/customers", json={"name": "Nope"})
        assert (
            blocked.status_code == 403
            and blocked.json()["category"] == "account_restricted"
            and blocked.json()["account_state"] == "SUSPENDED"
        )
        assert (
            "suspended" in blocked.json()["message"]
            and "contact support" in blocked.json()["message"].lower()
        )
        assert "unpaid" not in blocked.text  # the internal reason is not shown to the shop

    def test_deactivated_is_blocked_except_the_account_page_and_keeps_the_data(
        self, client_a, tenant_a, session_factory, shelf
    ):
        self.suspend(session_factory, tenant_a, AccountStatus.DEACTIVATED)
        assert client_a.get("/api/v1/products").status_code == 403
        assert client_a.post("/api/v1/sales", json={"items": []}).status_code == 403
        account = client_a.get("/api/v1/account")
        assert (
            account.status_code == 200
            and account.json()["account_status"] == "DEACTIVATED"
            and "deactivated" in account.json()["message"]
        )
        with session_factory() as s:
            assert s.scalar(select(func.count()).select_from(Product)) == 3

    def test_the_policies_are_configuration(self, client_a, tenant_a, session_factory, monkeypatch):
        monkeypatch.setenv("KIRANA_SUSPENDED_POLICY", "blocked")
        monkeypatch.setenv("KIRANA_DEACTIVATED_POLICY", "read_only")
        get_settings.cache_clear()
        self.suspend(session_factory, tenant_a, AccountStatus.SUSPENDED)
        assert client_a.get("/api/v1/products").status_code == 403
        self.suspend(session_factory, tenant_a, AccountStatus.DEACTIVATED)
        assert (
            client_a.get("/api/v1/products").status_code == 200
            and client_a.post("/api/v1/customers", json={"name": "x"}).status_code == 403
        )

    def test_reactivation_restores_use_and_only_the_affected_shop_is_restricted(
        self, client_a, client_b, tenant_a, session_factory
    ):
        self.suspend(session_factory, tenant_a, AccountStatus.DEACTIVATED)
        assert client_b.get("/api/v1/products").status_code == 200
        self.suspend(session_factory, tenant_a, AccountStatus.ACTIVE)
        assert client_a.post("/api/v1/customers", json={"name": "Back"}).status_code == 201

    def test_a_restricted_shop_cannot_use_the_assistant_or_exports_when_blocked(
        self, client_a, tenant_a, session_factory
    ):
        self.suspend(session_factory, tenant_a, AccountStatus.DEACTIVATED)
        assert client_a.post("/api/v1/ai/ask", json={"question": "sales"}).status_code == 403
        assert client_a.get("/api/v1/exports/products").status_code == 403
