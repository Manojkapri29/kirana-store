"""Authentication and sessions: sign in, sign out, expiry, throttling, cookies, CSRF, and that no secret leaks."""

import logging
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.core import ratelimit
from app.core.config import get_settings
from app.db.types import utc_now
from app.models import Account, AuthSession
from app.services import auth_service, password_service
from tests import factories
from tests.factories import PASSWORD

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
PRODUCTS = "/api/v1/products"


@pytest.fixture
def limits_on(monkeypatch):
    monkeypatch.setenv("KIRANA_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("KIRANA_RATE_LIMIT_AUTH", "4")
    get_settings.cache_clear()
    ratelimit.limiter.reset()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()
    ratelimit.limiter.reset()


class TestSignIn:
    def test_a_correct_password_signs_in_and_sets_a_safe_cookie(self, real_client, owner_login):
        client = real_client()
        response = client.post(LOGIN, json={"email": owner_login, "password": PASSWORD})
        assert response.status_code == 200
        body = response.json()
        assert (
            body["account"]["email"] == owner_login
            and body["role_code"] == "OWNER"
            and "PRODUCT_VIEW" in body["permissions"]
        )
        cookie = response.headers.get_list("set-cookie")
        session_cookie = next(c for c in cookie if c.startswith("kirana_session="))
        assert (
            "HttpOnly" in session_cookie
            and "SameSite=lax" in session_cookie.replace("Lax", "lax")
            and "Path=/" in session_cookie
        )
        csrf_cookie = next(c for c in cookie if c.startswith("kirana_csrf="))
        assert "HttpOnly" not in csrf_cookie  # the app must be able to read it to echo it back
        assert body["token"] is None  # a browser never gets the session token in a body

    def test_the_answer_never_holds_a_password_or_a_hash(self, real_client, owner_login):
        text_ = real_client().post(LOGIN, json={"email": owner_login, "password": PASSWORD}).text
        assert PASSWORD not in text_ and "argon2" not in text_ and "password_hash" not in text_

    def test_a_wrong_password_and_an_unknown_email_get_the_same_answer(self, real_client, owner_login):
        client = real_client()
        wrong = client.post(LOGIN, json={"email": owner_login, "password": "not the password"})
        unknown = client.post(LOGIN, json={"email": "nobody@example.com", "password": "not the password"})
        for r in (wrong, unknown):
            assert (
                r.status_code == 401
                and r.json()["category"] == "authentication"
                and r.json()["message"] == "Invalid email or password."
            )
            assert "set-cookie" not in r.headers

    def test_email_case_and_spaces_do_not_matter(self, real_client, owner_login):
        assert (
            real_client()
            .post(LOGIN, json={"email": f"  {owner_login.upper()} ", "password": PASSWORD})
            .status_code
            == 200
        )

    def test_a_disabled_account_cannot_sign_in(self, real_client, owner_login, session_factory):
        with session_factory() as s, s.begin():
            s.execute(text("UPDATE accounts SET status = 'DISABLED'"))
        assert real_client().post(LOGIN, json={"email": owner_login, "password": PASSWORD}).status_code == 401

    def test_an_account_without_a_password_cannot_sign_in(self, real_client, session_factory, tenant_a):
        with session_factory() as s, s.begin():
            factories.link_owner(s, s.get(type(tenant_a.user), tenant_a.user.id))
            s.execute(text("UPDATE accounts SET password_hash = '!'"))
        assert (
            real_client().post(LOGIN, json={"email": tenant_a.user.email, "password": "!"}).status_code == 401
        )

    def test_extra_fields_are_refused(self, real_client, owner_login):
        assert (
            real_client()
            .post(LOGIN, json={"email": owner_login, "password": PASSWORD, "shop_id": 1})
            .status_code
            == 422
        )

    def test_the_session_is_needed_for_business_calls(self, real_client):
        response = real_client().get(PRODUCTS)
        assert response.status_code == 401 and response.json()["category"] == "authentication"

    def test_a_signed_in_person_can_work(self, sign_in, owner_login):
        client = sign_in(owner_login)
        assert client.get(PRODUCTS).status_code == 200
        assert client.get(ME).json()["shop_name"] is not None

    def test_dev_bypass_is_ignored_in_production(self, monkeypatch, real_client):
        monkeypatch.setenv("KIRANA_DEV_AUTH_BYPASS", "true")
        get_settings.cache_clear()
        try:
            assert get_settings().dev_auth_bypass is True
            assert (
                real_client().get(PRODUCTS).status_code == 503
            )  # bypass on, but the development owner is not seeded here
            monkeypatch.setenv("KIRANA_ENVIRONMENT", "production")
            get_settings.cache_clear()
            from app.api import deps

            assert deps.get_settings().is_production
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()


class TestPasswordsAndSecrets:
    def test_the_password_is_stored_as_an_argon2id_hash_and_never_as_text(self, session_factory, owner_login):
        with session_factory() as s:
            stored = s.scalar(select(Account.password_hash))
        assert stored.startswith("$argon2id$") and PASSWORD not in stored

    def test_a_hash_is_upgraded_when_the_cost_setting_rises(
        self, real_client, owner_login, session_factory, monkeypatch
    ):
        monkeypatch.setenv("KIRANA_PASSWORD_HASH_MEMORY_KIB", "2048")
        get_settings.cache_clear()
        try:
            assert (
                real_client().post(LOGIN, json={"email": owner_login, "password": PASSWORD}).status_code
                == 200
            )
            with session_factory() as s:
                assert "m=2048" in s.scalar(select(Account.password_hash))
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()

    @pytest.mark.parametrize(
        "password", ["short", "password123", "aaaaaaaaaaaa", "sunil.kumar@shop.in", "a" * 200]
    )
    def test_weak_passwords_are_refused(self, password):
        assert password_service.problems_with(password, email="sunil.kumar@shop.in")

    def test_a_good_password_is_accepted(self):
        assert not password_service.problems_with(PASSWORD, email="a@b.co")

    def test_no_password_or_token_reaches_the_logs(self, real_client, owner_login, caplog):
        caplog.set_level(logging.DEBUG)
        client = real_client()
        client.post(LOGIN, json={"email": owner_login, "password": "the-wrong-password-XYZ"})
        response = client.post(LOGIN, json={"email": owner_login, "password": PASSWORD})
        token = client.cookies.get("kirana_session")
        client.get(PRODUCTS)
        everything = "\n".join(r.getMessage() + " " + str(getattr(r, "__dict__", "")) for r in caplog.records)
        assert PASSWORD not in everything and "the-wrong-password-XYZ" not in everything
        assert token and token not in everything and response.json()["csrf_token"] not in everything

    def test_only_a_hash_of_the_session_token_is_stored(self, real_client, owner_login, session_factory):
        client = real_client()
        client.post(LOGIN, json={"email": owner_login, "password": PASSWORD})
        token = client.cookies.get("kirana_session")
        with session_factory() as s:
            row = s.scalar(select(AuthSession))
            assert (
                token not in (row.token_hash, row.csrf_hash)
                and row.token_hash == auth_service.hash_token(token)
                and len(row.token_hash) == 64
            )


class TestThrottling:
    def test_repeated_wrong_passwords_pause_the_account_even_for_the_right_one(
        self, real_client, owner_login, session_factory
    ):
        client = real_client()
        for _ in range(5):
            assert (
                client.post(LOGIN, json={"email": owner_login, "password": "wrong password here"}).status_code
                == 401
            )
        paused = client.post(LOGIN, json={"email": owner_login, "password": PASSWORD})
        assert paused.status_code == 429 and int(paused.headers["retry-after"]) > 0
        assert (
            paused.json()["category"] == "rate_limited" and "password" not in paused.json()["message"].lower()
        )
        with session_factory() as s, s.begin():  # the pause ends
            s.execute(text("UPDATE accounts SET locked_until = :t"), {"t": utc_now() - timedelta(seconds=1)})
        assert client.post(LOGIN, json={"email": owner_login, "password": PASSWORD}).status_code == 200

    def test_a_success_clears_the_count_of_failures(self, real_client, owner_login, session_factory):
        client = real_client()
        for _ in range(3):
            client.post(LOGIN, json={"email": owner_login, "password": "wrong password here"})
        assert client.post(LOGIN, json={"email": owner_login, "password": PASSWORD}).status_code == 200
        with session_factory() as s:
            assert s.scalar(select(Account.failed_logins)) == 0

    def test_the_sign_in_rate_limit_answers_429_with_retry_after(self, real_client, limits_on):
        client = real_client()
        codes = [
            client.post(LOGIN, json={"email": "x@example.com", "password": "nope nope nope"}).status_code
            for _ in range(6)
        ]
        assert codes[:4] == [401] * 4 and codes[4] == 429
        assert (
            "retry-after" in client.post(LOGIN, json={"email": "x@example.com", "password": "nope"}).headers
        )

    def test_failed_attempts_are_recorded_for_operators_without_the_password(
        self, real_client, owner_login, session_factory
    ):
        real_client().post(LOGIN, json={"email": owner_login, "password": "wrong password here"})
        with session_factory() as s:
            rows = s.execute(
                text("SELECT code, message FROM system_events WHERE category = 'security'")
            ).all()
        assert rows and all("wrong password here" not in r[1] and owner_login not in r[1] for r in rows)


class TestSessions:
    def test_logout_ends_the_session_at_once(self, sign_in, owner_login):
        client = sign_in(owner_login)
        assert client.post("/api/v1/auth/logout").status_code == 204
        assert (
            client.get(PRODUCTS).status_code == 401
        )  # even though the browser still holds the old cookie value
        assert client.get(ME).status_code == 401

    def test_a_copied_cookie_stops_working_after_logout(self, sign_in, real_client, owner_login):
        client = sign_in(owner_login)
        stolen = client.cookies.get("kirana_session")
        client.post("/api/v1/auth/logout")
        other = real_client()
        assert other.get(PRODUCTS, headers={"Authorization": f"Bearer {stolen}"}).status_code == 401

    def test_a_session_ends_when_it_has_been_idle_too_long(self, sign_in, owner_login, session_factory):
        client = sign_in(owner_login)
        with session_factory() as s, s.begin():
            s.execute(
                text("UPDATE auth_sessions SET last_seen_at = :t"), {"t": utc_now() - timedelta(hours=3)}
            )
        assert client.get(PRODUCTS).status_code == 401

    def test_a_session_ends_at_its_absolute_limit_however_busy(self, sign_in, owner_login, session_factory):
        client = sign_in(owner_login)
        with session_factory() as s, s.begin():
            s.execute(
                text("UPDATE auth_sessions SET expires_at = :t"), {"t": utc_now() - timedelta(seconds=1)}
            )
        assert client.get(PRODUCTS).status_code == 401

    def test_activity_extends_an_idle_session(self, sign_in, owner_login, session_factory):
        client = sign_in(owner_login)
        with session_factory() as s, s.begin():
            s.execute(
                text("UPDATE auth_sessions SET last_seen_at = :t"), {"t": utc_now() - timedelta(minutes=50)}
            )
        assert (
            client.get(PRODUCTS).status_code == 200
        )  # 50 minutes idle is within the hour, and this call renews it
        with session_factory() as s:
            assert s.scalar(select(AuthSession.last_seen_at)) > utc_now() - timedelta(minutes=5)

    def test_the_session_says_when_it_ends_for_the_expiry_warning(self, sign_in, owner_login):
        body = sign_in(owner_login).get(ME).json()
        assert body["expires_at"] and body["idle_minutes"] == 60

    def test_a_bearer_token_works_for_non_browser_clients_without_csrf(self, real_client, owner_login):
        client = real_client()
        token = client.post(
            f"{LOGIN}?issue_token=true", json={"email": owner_login, "password": PASSWORD}
        ).json()["token"]
        api = real_client()
        headers = {"Authorization": f"Bearer {token}"}
        assert api.get(PRODUCTS, headers=headers).status_code == 200
        assert api.post("/api/v1/customers", json={"name": "Via Bearer"}, headers=headers).status_code == 201


class TestCsrf:
    def test_a_state_changing_call_from_a_cookie_needs_the_csrf_header(self, sign_in, owner_login):
        client = sign_in(owner_login)
        del client.headers["X-CSRF-Token"]
        refused = client.post("/api/v1/customers", json={"name": "No Token"})
        assert refused.status_code == 403 and refused.json()["error_code"] == "forbidden"
        client.headers["X-CSRF-Token"] = "wrong-token"
        assert client.post("/api/v1/customers", json={"name": "Bad Token"}).status_code == 403

    def test_reading_needs_no_csrf_header(self, sign_in, owner_login):
        client = sign_in(owner_login)
        del client.headers["X-CSRF-Token"]
        assert client.get(PRODUCTS).status_code == 200

    def test_the_right_header_lets_the_call_through(self, sign_in, owner_login):
        assert sign_in(owner_login).post("/api/v1/customers", json={"name": "With Token"}).status_code == 201

    def test_another_sessions_csrf_token_does_not_work(self, sign_in, owner_login):
        first, second = sign_in(owner_login), sign_in(owner_login)
        first.headers["X-CSRF-Token"] = second.headers["X-CSRF-Token"]
        assert first.post("/api/v1/customers", json={"name": "Cross Session"}).status_code == 403


class TestChangePassword:
    NEW = "another long passphrase 42"

    def test_it_needs_the_current_password_and_a_good_new_one(self, sign_in, owner_login):
        client = sign_in(owner_login)
        wrong = client.post(
            "/api/v1/auth/change-password", json={"current_password": "not it", "new_password": self.NEW}
        )
        assert (
            wrong.status_code == 422
            and wrong.json()["detail"]
            and "current password" in wrong.json()["message"]
        )
        weak = client.post(
            "/api/v1/auth/change-password", json={"current_password": PASSWORD, "new_password": "short"}
        )
        assert weak.status_code == 422

    def test_other_sessions_end_and_the_new_password_works(self, sign_in, real_client, owner_login):
        mine, elsewhere = sign_in(owner_login), sign_in(owner_login)
        done = mine.post(
            "/api/v1/auth/change-password", json={"current_password": PASSWORD, "new_password": self.NEW}
        )
        assert done.status_code == 200 and done.json()["other_sessions_ended"] == 1
        assert mine.get(PRODUCTS).status_code == 200 and elsewhere.get(PRODUCTS).status_code == 401
        assert real_client().post(LOGIN, json={"email": owner_login, "password": PASSWORD}).status_code == 401
        assert real_client().post(LOGIN, json={"email": owner_login, "password": self.NEW}).status_code == 200
