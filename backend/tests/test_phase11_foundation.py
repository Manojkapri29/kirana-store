"""Production configuration, health checks, request ids, structured logging, security headers, rate limiting, feature flags."""

import json
import logging

import pytest

from app.core import observability, ratelimit
from app.core.config import Settings, get_settings
from app.main import create_app

KEY = "k" * 40


def settings(**values):
    values.setdefault(
        "rate_limit_enabled", True
    )  # the test environment switches limiting off; these tests look at the defaults
    return Settings(_env_file=None, **values)


PROD = dict(
    environment="production",
    secret_key=KEY,
    frontend_url="https://shop.example.com",
    cors_origins="https://shop.example.com",
)


class TestConfiguration:
    def test_development_needs_nothing_and_has_safe_defaults(self):
        s = settings()
        assert s.environment == "development" and s.production_problems() == []
        assert s.debug is False and s.rate_limit_enabled is True and s.suspended_policy == "read_only"
        assert s.allowed_image_types == ["image/jpeg", "image/png", "image/webp"]

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("testing", "test"),
            ("test", "test"),
            ("prod", "production"),
            ("dev", "development"),
            ("Production", "production"),
        ],
    )
    def test_environment_spellings(self, given, expected):
        assert settings(environment=given).environment == expected

    def test_a_valid_production_configuration_has_no_problems(self):
        assert settings(**PROD).production_problems() == []

    @pytest.mark.parametrize(
        ("override", "fragment"),
        [
            ({"secret_key": None}, "KIRANA_SECRET_KEY"),
            ({"secret_key": "short"}, "KIRANA_SECRET_KEY"),
            ({"frontend_url": None}, "KIRANA_FRONTEND_URL must be set"),
            ({"frontend_url": "http://shop.example.com"}, "https"),
            ({"cors_origins": "*"}, "not '*'"),
            ({"cors_origins": "https://other.example.com"}, "must include KIRANA_FRONTEND_URL"),
            ({"debug": True}, "KIRANA_DEBUG"),
            ({"log_level": "DEBUG"}, "KIRANA_LOG_LEVEL"),
            ({"rate_limit_enabled": False}, "RATE_LIMIT"),
        ],
    )
    def test_production_refuses_unsafe_settings_by_name_never_value(self, override, fragment):
        problems = settings(**{**PROD, **override}).production_problems()
        assert any(fragment in p for p in problems)
        assert KEY not in " ".join(problems) and "short" not in " ".join(problems)

    def test_the_app_refuses_to_start_in_an_unsafe_production(self, monkeypatch):
        monkeypatch.setenv("KIRANA_ENVIRONMENT", "production")
        monkeypatch.delenv("KIRANA_SECRET_KEY", raising=False)
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match="Unsafe production configuration"):
            create_app()

    def test_secrets_never_appear_in_a_settings_dump(self):
        s = settings(**PROD, ai_api_key="sk-ai-secret-value", ai_provider="anthropic")
        assert KEY not in repr(s) and KEY not in s.model_dump_json() and "sk-ai-secret-value" not in repr(s)

    def test_the_env_example_documents_the_phase_11_settings_with_placeholders_only(self):
        from app.core.config import BACKEND_DIR

        text = (BACKEND_DIR / ".env.example").read_text()
        for name in (
            "KIRANA_SECRET_KEY",
            "KIRANA_FRONTEND_URL",
            "KIRANA_RATE_LIMIT_ENABLED",
            "KIRANA_BACKUP_DIR",
            "KIRANA_SUSPENDED_POLICY",
            "KIRANA_FEATURE_AI",
            "KIRANA_LOG_FORMAT",
        ):
            assert name in text, name
        assert "KIRANA_SECRET_KEY=change" not in text and "sk-" not in text


class TestHealth:
    def test_liveness_touches_nothing_and_says_only_ok(self, client_a):
        for path in ("/health", "/health/live"):
            response = client_a.get(path)
            assert response.status_code == 200 and response.json() == {"status": "ok"}

    def test_readiness_checks_database_schema_and_configuration(self, client_a):
        body = client_a.get("/health/ready").json()
        assert body == {"status": "ok", "checks": {"database": "ok", "schema": "ok", "configuration": "ok"}}

    def test_readiness_fails_safely_when_the_schema_is_behind(self, client_a, monkeypatch):
        from app.core import schema_state

        monkeypatch.setattr(schema_state, "code_head", lambda: "9999")
        response = client_a.get("/health/ready")
        assert (
            response.status_code == 503
            and response.json()["status"] == "unavailable"
            and response.json()["checks"]["schema"] == "unavailable"
        )

    def test_readiness_fails_when_the_database_is_unreachable_and_leaks_nothing(self, client_a, monkeypatch):
        from app.api.routes import health

        def boom():
            raise RuntimeError("could not connect to /Users/me/secret.db password=hunter2")

        monkeypatch.setattr(health, "read_session", boom)
        response = client_a.get("/health/ready")
        assert (
            response.status_code == 503 and "hunter2" not in response.text and "/Users" not in response.text
        )
        assert set(response.json()) == {"status", "checks"}

    def test_readiness_reports_a_configuration_problem_by_name_only(self, client_a, monkeypatch):
        monkeypatch.setattr(Settings, "production_problems", lambda self: ["KIRANA_SECRET_KEY must be set"])
        body = client_a.get("/health/ready").json()
        assert body["checks"]["configuration"] == "unavailable" and "SECRET" not in json.dumps(body)


class TestRequestIds:
    def test_a_well_formed_id_is_reused_and_a_bad_one_replaced(self, client_a):
        ok = client_a.get("/health", headers={"X-Request-ID": "req_abc12345"})
        assert ok.headers["x-request-id"] == "req_abc12345"
        bad = client_a.get("/health", headers={"X-Request-ID": "x y; DROP TABLE"})
        assert (
            bad.headers["x-request-id"].startswith("req_")
            and bad.headers["x-request-id"] != "x y; DROP TABLE"
        )
        assert client_a.get("/health").headers["x-request-id"].startswith("req_")

    def test_the_id_is_on_error_responses_and_in_the_access_log(self, client_a, caplog):
        with caplog.at_level(logging.INFO, logger="app.access"):
            response = client_a.get("/api/v1/products/999999", headers={"X-Request-ID": "req_trace0001"})
        assert response.status_code == 404 and response.headers["x-request-id"] == "req_trace0001"
        line = next(r for r in caplog.records if r.name == "app.access" and "products" in r.getMessage())
        assert line.fields["request_id"] == "req_trace0001" and line.fields["status"] == 404
        assert (
            line.fields["method"] == "GET"
            and line.fields["endpoint"] == "/api/v1/products/{id}"
            and line.fields["duration_ms"] >= 0
        )
        assert line.fields["shop_id"] and "user_id" in line.fields

    def test_the_access_log_never_contains_the_query_string_or_body(self, client_a, caplog):
        with caplog.at_level(logging.INFO, logger="app.access"):
            client_a.get("/api/v1/products", params={"q": "secret-search-term-77"})
        ours = "\n".join(
            r.getMessage() + json.dumps(getattr(r, "fields", {}), default=str)
            for r in caplog.records
            if r.name.startswith("app.")
        )
        assert "secret-search-term-77" not in ours and "app.access" in {r.name for r in caplog.records}

    def test_audit_rows_carry_the_request_id(self, client_a, tenant_a, units, session_factory):
        from sqlalchemy import select

        from app.models import AuditLog

        client_a.post(
            "/api/v1/products",
            json={
                "sku": "AUD1",
                "name": "Audited",
                "category_id": tenant_a.category.id,
                "unit_id": units["pcs"],
                "selling_price": "10",
            },
            headers={"X-Request-ID": "req_audit0001"},
        )
        with session_factory() as s:
            row = s.scalars(
                select(AuditLog).where(AuditLog.entity_type == "product").order_by(AuditLog.id.desc())
            ).first()
            assert row.request_id == "req_audit0001"


class TestLogging:
    def test_json_lines_have_the_standard_fields_and_redact_secrets(self, caplog):
        observability.set_request_id("req_json0001")
        record = logging.LogRecord(
            "app.security", logging.WARNING, "x", 1, "login failed for user@example.com token=abc", None, None
        )
        record.category, record.fields, record.request_id = (
            "security",
            {"endpoint": "/x", "shop_id": 3},
            "req_json0001",
        )
        entry = json.loads(observability.JsonFormatter().format(record))
        assert (
            entry["level"] == "WARNING"
            and entry["category"] == "security"
            and entry["request_id"] == "req_json0001"
        )
        assert entry["endpoint"] == "/x" and entry["shop_id"] == 3 and "timestamp" in entry
        assert "user@example.com" not in entry["message"] and "token=abc" not in entry["message"]

    def test_sensitive_field_names_are_dropped_from_log_events(self, caplog):
        with caplog.at_level(logging.INFO, logger="app.integration"):
            observability.log_event(
                "integration",
                "call",
                api_key="sk-live-1",
                password="p",
                authorization="Bearer x",
                token="t",
                status=200,
            )
        fields = caplog.records[-1].fields
        assert fields == {"status": 200}

    def test_every_documented_category_has_its_own_logger(self):
        assert set(observability.CATEGORIES) >= {
            "access",
            "security",
            "audit",
            "integration",
            "ai",
            "backup",
            "notification",
            "application",
        }

    def test_the_log_level_is_configurable(self):
        observability.configure_logging("WARNING", "text")
        assert logging.getLogger("app.access").level == logging.WARNING
        observability.configure_logging("INFO", "text")


class TestSecurityHeadersAndCors:
    def test_standard_security_headers_and_no_store_on_the_api(self, client_a):
        r = client_a.get("/api/v1/products")
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer" and r.headers["cache-control"] == "no-store"

    def test_cors_allows_only_the_configured_origin_and_no_delete(self, client_a):
        ok = client_a.options(
            "/api/v1/products",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
        )
        assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert "DELETE" not in ok.headers["access-control-allow-methods"]
        evil = client_a.options(
            "/api/v1/products",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in evil.headers


@pytest.fixture
def limits_on(monkeypatch):
    monkeypatch.setenv("KIRANA_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("KIRANA_RATE_LIMIT_AI", "3")
    monkeypatch.setenv("KIRANA_RATE_LIMIT_EXPORT", "2")
    get_settings.cache_clear()
    ratelimit.limiter.reset()
    yield
    ratelimit.limiter.reset()


class TestRateLimiting:
    def test_the_sliding_window_allows_then_refuses_then_recovers(self):
        limiter = ratelimit.SlidingWindowLimiter()
        assert [limiter.hit("k", 2, 60, now=0) for _ in range(2)] == [None, None]
        wait = limiter.hit("k", 2, 60, now=10)
        assert wait is not None and 49 < wait <= 50
        assert limiter.hit("k", 2, 60, now=61) is None
        assert limiter.hit("other", 2, 60, now=10) is None

    def test_too_many_assistant_calls_get_429_with_retry_after_and_a_safe_message(self, client_a, limits_on):
        codes = [
            client_a.post("/api/v1/ai/ask", json={"question": "sales today"}).status_code for _ in range(4)
        ]
        assert codes[:3] == [200, 200, 200] and codes[3] == 429
        blocked = client_a.post("/api/v1/ai/ask", json={"question": "sales today"})
        body = blocked.json()
        assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1
        assert (
            body["category"] == "rate_limited"
            and body["error_code"] == "rate_limited"
            and body["retryable"] is True
        )
        assert (
            body["message"] == "Too many requests. Please wait a moment and try again."
            and body["success"] is False
        )

    def test_limits_are_per_shop_so_one_shop_cannot_starve_another(self, client_a, client_b, limits_on):
        for _ in range(4):
            client_a.post("/api/v1/ai/ask", json={"question": "sales today"})
        assert client_a.post("/api/v1/ai/ask", json={"question": "sales today"}).status_code == 429
        assert client_b.post("/api/v1/ai/ask", json={"question": "sales today"}).status_code == 200

    def test_exports_are_limited_too(self, client_a, limits_on):
        codes = [client_a.get("/api/v1/exports/products").status_code for _ in range(3)]
        assert codes == [200, 200, 429]

    def test_ordinary_use_is_not_limited_with_the_defaults(self, client_a, monkeypatch):
        monkeypatch.setenv("KIRANA_RATE_LIMIT_ENABLED", "true")
        get_settings.cache_clear()
        ratelimit.limiter.reset()
        assert all(client_a.get("/api/v1/products").status_code == 200 for _ in range(40))
        assert all(
            client_a.post("/api/v1/sales/calculate", json={"items": []}).status_code == 200 for _ in range(60)
        )

    def test_the_operator_can_switch_limiting_off(self, client_a, monkeypatch):
        monkeypatch.setenv("KIRANA_RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("KIRANA_RATE_LIMIT_AI", "1")
        monkeypatch.setenv("KIRANA_RATE_LIMIT_ENABLED", "false")
        get_settings.cache_clear()
        assert all(
            client_a.post("/api/v1/ai/ask", json={"question": "sales today"}).status_code == 200
            for _ in range(5)
        )


class TestFeatureFlags:
    @pytest.mark.parametrize(
        ("flag", "call"),
        [
            ("KIRANA_FEATURE_AI", lambda c: c.post("/api/v1/ai/ask", json={"question": "sales today"})),
            ("KIRANA_FEATURE_EXPORTS", lambda c: c.get("/api/v1/exports/products")),
            ("KIRANA_FEATURE_NOTIFICATIONS", lambda c: c.get("/api/v1/notifications")),
            ("KIRANA_FEATURE_PRICE_LOOKUPS", lambda c: c.get("/api/v1/price-intelligence/providers")),
        ],
    )
    def test_a_switched_off_capability_answers_503_and_nothing_else_is_affected(
        self, client_a, monkeypatch, flag, call
    ):
        monkeypatch.setenv(flag, "false")
        get_settings.cache_clear()
        response = call(client_a)
        assert response.status_code == 503 and response.json()["error_code"] == "feature_off"
        assert "temporarily turned off" in response.json()["message"]
        assert client_a.get("/api/v1/products").status_code == 200  # the rest of the application works
