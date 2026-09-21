"""Smart error recovery, backend half: a safe standard error, reference ids, redacted diagnostics, categories,
nothing leaked, data preserved after a failure, and safe retry only through idempotency."""

import json
import logging
import re
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core import diagnostics
from app.core.diagnostics import REFERENCE_PATTERN, ErrorCategory, new_reference_id, redact, sanitized_summary
from app.main import app
from app.models import CustomerLedgerEntry, DocumentSequence, Sale
from app.services import khata_service, product_service
from tests.test_purchases_api import make_product
from tests.test_sales_api import item, make_customer, stock
from tests.test_sales_api import shelf as shelf  # noqa: F401

SECRET = "sk-live-ABCDEF0123456789abcdef"
LEAKS = (
    "Traceback",
    'File "',
    "sqlalchemy",
    "sqlite",
    "SQLAlchemy",
    "/Users/",
    "site-packages",
    "fastapi",
    "psycopg",
    SECRET,
    "hunter2",
)


@pytest.fixture
def quiet(client_a) -> TestClient:
    """A client that gets the server's real error response instead of having the exception re-raised."""
    client = TestClient(app, raise_server_exceptions=False)
    client.app.dependency_overrides = client_a.app.dependency_overrides
    return client


def boom(*_a, **_k):
    raise RuntimeError(
        f"failed in /Users/me/app/secret.py with api_key={SECRET} for user@example.com 9876543210"
    )


def clean(response):
    assert all(leak not in response.text for leak in LEAKS), response.text


def entries(caplog):
    return [
        json.loads(r.message)
        for r in caplog.records
        if r.name == "app.diagnostics" and r.message.startswith("{")
    ]


class TestUnexpectedErrors:
    def test_the_user_gets_a_plain_message_and_a_reference_and_nothing_technical(self, quiet, monkeypatch):
        monkeypatch.setattr(product_service, "list_products", boom)
        response = quiet.get("/api/v1/products")
        body = response.json()
        assert response.status_code == 500
        assert (body["success"], body["error_code"], body["category"], body["retryable"]) == (
            False,
            "internal_error",
            "unexpected",
            False,
        )
        assert body["message"] == "Something went wrong while completing this action."
        assert REFERENCE_PATTERN.match(body["reference_id"]) and body["reference_id"] in body["detail"]
        clean(response)
        assert set(body) == {
            "success",
            "error_code",
            "message",
            "category",
            "retryable",
            "reference_id",
            "detail",
        }

    def test_reference_ids_are_unique_dated_and_contain_no_identifier(self):
        ids = {new_reference_id() for _ in range(500)}
        assert len(ids) == 500
        today = datetime.now(UTC).strftime("%Y%m%d")
        assert all(i.startswith(f"ERR-{today}-") and REFERENCE_PATTERN.match(i) for i in ids)
        assert new_reference_id(datetime(2026, 9, 21, tzinfo=UTC)).startswith("ERR-20260921-")
        assert not any(c in "ILOU" for i in ids for c in i.rsplit("-", 1)[1])  # easy to read out loud

    def test_each_failure_gets_its_own_reference(self, quiet, monkeypatch):
        monkeypatch.setattr(product_service, "list_products", boom)
        refs = {quiet.get("/api/v1/products").json()["reference_id"] for _ in range(5)}
        assert len(refs) == 5

    def test_the_real_error_is_captured_internally_under_that_reference(
        self, quiet, monkeypatch, caplog, tenant_a
    ):
        monkeypatch.setattr(product_service, "list_products", boom)
        with caplog.at_level(logging.DEBUG, logger="app.diagnostics"):
            response = quiet.get("/api/v1/products", headers={"X-Request-ID": "corr-abcdef123456"})
        [entry] = entries(caplog)
        assert entry["reference_id"] == response.json()["reference_id"] and entry["event"] == "error"
        assert (entry["endpoint"], entry["method"], entry["status"]) == ("GET /api/v1/products", "GET", 500)
        assert (entry["category"], entry["error_code"], entry["exception_type"]) == (
            "unexpected",
            "internal_error",
            "RuntimeError",
        )
        assert entry["shop_id"] == tenant_a.shop.id and entry["user_id"] == tenant_a.user.id
        assert entry["correlation_id"] == "corr-abcdef123456" == response.headers["x-request-id"]
        assert entry["timestamp"] and "failed in" in entry["details"]  # the real cause is kept
        assert "RuntimeError" in entry["traceback"] and "Traceback" in entry["traceback"]  # internal only

    def test_the_diagnostics_never_contain_secrets_paths_or_personal_numbers(
        self, quiet, monkeypatch, caplog
    ):
        monkeypatch.setattr(product_service, "list_products", boom)
        with caplog.at_level(logging.DEBUG, logger="app.diagnostics"):
            quiet.get("/api/v1/products")
        text = caplog.text + " ".join(r.exc_text or "" for r in caplog.records)
        for leak in (SECRET, "/Users/me", "user@example.com", "9876543210"):
            assert leak not in text, leak
        assert "<redacted>" in text and "<email>" in text and "<number>" in text

    def test_request_bodies_and_passwords_are_never_logged(self, quiet, monkeypatch, caplog, tenant_a, units):
        monkeypatch.setattr(product_service, "create_product", boom)
        body = {
            "sku": "S1",
            "name": "password=hunter2 token=abc",
            "category_id": tenant_a.category.id,
            "unit_id": units["pcs"],
            "selling_price": "5",
        }
        with caplog.at_level(logging.DEBUG):
            response = quiet.post("/api/v1/products", json=body)
        assert response.status_code == 500 and "hunter2" not in caplog.text and "hunter2" not in response.text

    def test_a_correlation_id_is_kept_when_well_formed_and_replaced_when_not(self, client_a):
        assert (
            client_a.get("/health", headers={"X-Request-ID": "trace-1234567890"}).headers["x-request-id"]
            == "trace-1234567890"
        )
        for bad in ("x", "has spaces and $ymbols", "a" * 200):
            echoed = client_a.get("/health", headers={"X-Request-ID": bad}).headers["x-request-id"]
            assert echoed != bad and re.match(r"^[0-9a-f]{16}$", echoed)
        assert re.match(r"^[0-9a-f]{16}$", client_a.get("/health").headers["x-request-id"])

    def test_the_diagnostics_can_also_go_to_a_file(self, quiet, monkeypatch, tmp_path):
        target = tmp_path / "diag.jsonl"
        diagnostics.configure(str(target))
        try:
            monkeypatch.setattr(product_service, "list_products", boom)
            ref = quiet.get("/api/v1/products").json()["reference_id"]
        finally:
            for handler in [h for h in diagnostics.log.handlers if isinstance(h, logging.FileHandler)]:
                handler.flush()
                diagnostics.log.removeHandler(handler)
                handler.close()
        lines = [json.loads(line) for line in target.read_text().splitlines()]
        assert lines[-1]["reference_id"] == ref and SECRET not in target.read_text()

    @pytest.mark.parametrize(
        "path",
        ["/api/v1/errors", "/errors", "/api/v1/diagnostics", "/admin/errors", "/api/v1/error-log", "/logs"],
    )
    def test_there_is_no_public_error_log(self, client_a, path):
        assert client_a.get(path).status_code == 404


class TestCategories:
    def test_a_busy_database_is_retryable_and_says_nothing_about_the_database(self, quiet, monkeypatch):
        def locked(*a, **k):
            raise OperationalError("SELECT 1", {}, Exception("database is locked"))

        monkeypatch.setattr(product_service, "list_products", locked)
        response = quiet.get("/api/v1/products")
        body = response.json()
        assert response.status_code == 503 and (body["category"], body["error_code"], body["retryable"]) == (
            "database",
            "database_error",
            True,
        )
        assert "save your changes" in body["message"] and REFERENCE_PATTERN.match(body["reference_id"])
        clean(response)

    def test_any_other_database_failure_is_not_marked_retryable(self, quiet, monkeypatch):
        def bad(*a, **k):
            raise OperationalError("SELECT x", {}, Exception("no such table: secrets at /var/db.sqlite"))

        monkeypatch.setattr(product_service, "list_products", bad)
        response = quiet.get("/api/v1/products")
        assert (
            response.status_code == 500
            and response.json()["category"] == "database"
            and response.json()["retryable"] is False
        )
        clean(response)
        assert "no such table" not in response.text

    def test_a_uniqueness_violation_that_slipped_through_is_a_duplicate(self, quiet, monkeypatch):
        def dup(*a, **k):
            raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed: products.sku"))

        monkeypatch.setattr(product_service, "list_products", dup)
        response = quiet.get("/api/v1/products")
        assert (
            response.status_code == 409
            and response.json()["category"] == "duplicate"
            and response.json()["error_code"] == "duplicate_record"
        )
        clean(response)
        assert "products.sku" not in response.text

    def test_another_integrity_failure_is_a_database_error(self, quiet, monkeypatch):
        def fk(*a, **k):
            raise IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed"))

        monkeypatch.setattr(product_service, "list_products", fk)
        response = quiet.get("/api/v1/products")
        assert (
            response.status_code == 500
            and response.json()["category"] == "database"
            and "FOREIGN" not in response.text
        )

    def test_a_timeout_is_retryable(self, quiet, monkeypatch):
        def slow(*a, **k):
            raise TimeoutError("waited 30s for 10.0.0.5:5432")

        monkeypatch.setattr(product_service, "list_products", slow)
        response = quiet.get("/api/v1/products")
        body = response.json()
        assert response.status_code == 504 and (body["category"], body["error_code"], body["retryable"]) == (
            "timeout",
            "timeout",
            True,
        )
        assert "10.0.0.5" not in response.text

    def test_validation_errors_show_where_and_why_but_never_the_submitted_value(self, client_a):
        response = client_a.post(
            "/api/v1/customers", json={"name": 12345, "phone": ["x"], "secret_field": "hunter2"}
        )
        body = response.json()
        assert response.status_code == 422 and (body["category"], body["error_code"], body["retryable"]) == (
            "validation",
            "validation_error",
            False,
        )
        assert body["reference_id"] is None and body["message"]
        assert all(set(d) == {"loc", "msg", "type"} for d in body["detail"])
        assert "hunter2" not in response.text and "12345" not in response.text

    def test_a_business_rule_problem_keeps_the_field_shape_screens_rely_on(self, client_a):
        response = client_a.post(
            "/api/v1/sales/calculate", json={"items": [{"product_id": 999999, "quantity": "1"}]}
        )
        assert response.status_code == 200  # a preview reports per line
        response = client_a.post("/api/v1/sales", json={"items": [{"product_id": 999999, "quantity": "1"}]})
        body = response.json()
        assert (
            response.status_code == 422
            and body["detail"][0]["loc"] == ["body", "items", 0, "product_id"]
            and body["category"] == "validation"
        )

    def test_not_found_keeps_its_plain_detail_and_is_not_retryable(self, client_a):
        response = client_a.get("/api/v1/sales/999999")
        body = response.json()
        assert response.status_code == 404 and body["detail"] == "Sale not found" == body["message"]
        assert (body["category"], body["error_code"], body["retryable"], body["reference_id"]) == (
            "not_found",
            "not_found",
            False,
            None,
        )

    def test_an_unknown_route_and_a_wrong_method_use_the_same_format(self, client_a):
        missing, wrong = client_a.get("/api/v1/nothing-here"), client_a.delete("/api/v1/sales")
        assert (
            missing.status_code == 404
            and missing.json()["success"] is False
            and missing.json()["category"] == "not_found"
        )
        assert (
            wrong.status_code == 405
            and wrong.json()["success"] is False
            and wrong.json()["reference_id"] is None
        )

    def test_insufficient_stock_is_its_own_category_with_the_line_marked(self, client_a, shelf):
        draft = client_a.post("/api/v1/sales", json={"items": [item(shelf["rice"], "11")]}).json()
        response = client_a.post(f"/api/v1/sales/{draft['id']}/post", json={"payment_method": "CASH"})
        body = response.json()
        assert response.status_code == 409 and (body["category"], body["error_code"], body["retryable"]) == (
            "insufficient_stock",
            "insufficient_stock",
            False,
        )
        assert body["detail"][0]["loc"] == ["body", "items", 0, "quantity"] and "10" in body["message"]

    def test_a_duplicate_record_is_its_own_category(self, client_a, tenant_a, units):
        make_product(client_a, tenant_a, units, "DUPE")
        response = client_a.post(
            "/api/v1/products",
            json={
                "sku": "dupe",
                "name": "x",
                "category_id": tenant_a.category.id,
                "unit_id": units["pcs"],
                "selling_price": "5",
            },
        )
        assert (
            response.status_code == 409
            and response.json()["category"] == "duplicate"
            and response.json()["error_code"] == "duplicate_record"
        )

    def test_a_conflict_that_is_not_stock_or_duplicate_stays_a_plain_conflict(self, client_a, shelf):
        sale = client_a.post("/api/v1/sales", json={"items": [item(shelf["rice"], "1")]}).json()
        client_a.post(f"/api/v1/sales/{sale['id']}/post", json={"payment_method": "CASH"})
        again = client_a.post(f"/api/v1/sales/{sale['id']}/post", json={"payment_method": "CASH"})
        assert (
            again.status_code == 409
            and again.json()["error_code"] == "already_done"
            and again.json()["retryable"] is False
        )

    def test_a_plan_limit_is_its_own_category(self, client_a, tenant_a, give_plan):
        give_plan(tenant_a, "free")
        response = client_a.post(
            "/api/v1/promotions", json={"name": "x", "promo_type": "PERCENT", "percent": "5"}
        )
        body = response.json()
        assert response.status_code == 403 and (body["category"], body["error_code"], body["feature"]) == (
            "plan_limit",
            "plan_limit",
            "promotions",
        )
        assert body["detail"][0]["feature"] == "promotions"

    def test_permission_and_sign_in_problems(self, client_a, make_client, tenant_a, monkeypatch):
        from app.models.enums import UserRole

        staff = make_client(tenant_a, role=UserRole.STAFF).post(
            "/api/v1/promotions", json={"name": "x", "promo_type": "PERCENT", "percent": "5"}
        )
        assert (
            staff.status_code == 403
            and staff.json()["category"] == "authorization"
            and staff.json()["error_code"] == "forbidden"
        )

        def unauthenticated():
            raise HTTPException(status_code=401, detail="Please sign in.")

        from app.api import deps

        quiet = TestClient(app, raise_server_exceptions=False)
        quiet.app.dependency_overrides = {
            **client_a.app.dependency_overrides,
            deps.get_request_context: unauthenticated,
        }
        response = quiet.get("/api/v1/products")
        assert (
            response.status_code == 401
            and response.json()["category"] == "authentication"
            and response.json()["error_code"] == "unauthenticated"
        )

    @pytest.mark.parametrize(
        ("method", "path", "body", "category", "message"),
        [
            (
                "POST",
                "/api/v1/sales/1/post",
                {"payment_method": "CASH"},
                "checkout",
                "We couldn't complete this sale.",
            ),
            (
                "POST",
                "/api/v1/sales/calculate",
                {"items": []},
                "promotion_calculation",
                "The offers for this bill couldn't be worked out right now.",
            ),
            (
                "POST",
                "/api/v1/price-intelligence/check",
                {"barcode": "4006381333931"},
                "external_api",
                "An outside service is temporarily unavailable.",
            ),
            (
                "POST",
                "/api/v1/image-intelligence/analyze",
                {"image_base64": "A" * 40},
                "image_upload",
                "The image couldn't be processed.",
            ),
        ],
    )
    def test_an_unexpected_failure_is_named_after_what_the_user_was_doing(
        self, quiet, monkeypatch, method, path, body, category, message
    ):
        from app.services import entitlement_service

        monkeypatch.setattr(entitlement_service, "get_entitlements", boom)
        monkeypatch.setattr("app.services.sale_service.post_sale", boom)
        monkeypatch.setattr("app.services.sale_service.calculate_preview", boom)
        response = quiet.request(method, path, json=body)
        data = response.json()
        assert response.status_code == 500 and data["category"] == category and data["message"] == message
        assert REFERENCE_PATTERN.match(data["reference_id"])
        clean(response)


class TestNothingIsLostOrFaked:
    def test_a_failed_posting_changes_nothing_and_the_draft_can_be_posted_afterwards(
        self, client_a, quiet, shelf, fresh, monkeypatch
    ):
        customer = make_customer(client_a)
        draft = client_a.post(
            "/api/v1/sales", json={"items": [item(shelf["rice"], "2")], "customer_id": customer["id"]}
        ).json()
        with monkeypatch.context() as m:
            m.setattr(khata_service, "record_credit_sale", boom)
            failed = quiet.post(f"/api/v1/sales/{draft['id']}/post", json={"amount_paid": "0"})
        assert (
            failed.status_code == 500
            and failed.json()["success"] is False
            and "sale" not in failed.json().get("data", {})
        )
        clean(failed)
        current = client_a.get(f"/api/v1/sales/{draft['id']}").json()
        assert (
            current["status"] == "DRAFT" and current["invoice_no"] is None and len(current["items"]) == 1
        )  # the draft is preserved
        assert stock(client_a, shelf["rice"]) == 10
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(CustomerLedgerEntry))) == 0
        assert (
            fresh(
                lambda s: s.scalar(
                    select(func.count())
                    .select_from(DocumentSequence)
                    .where(DocumentSequence.doc_type == "SALE")
                )
            )
            == 0
        )
        ok = client_a.post(f"/api/v1/sales/{draft['id']}/post", json={"amount_paid": "0"})
        assert (
            ok.status_code == 200 and ok.json()["status"] == "POSTED" and stock(client_a, shelf["rice"]) == 8
        )

    def test_a_failure_while_committing_is_never_reported_as_success(
        self, client_a, quiet, shelf, monkeypatch
    ):
        from sqlalchemy.orm import Session

        draft = client_a.post("/api/v1/sales", json={"items": [item(shelf["rice"], "1")]}).json()
        real = Session.commit

        def failing(self):
            raise OperationalError("COMMIT", {}, Exception("database is locked"))

        with monkeypatch.context() as m:
            m.setattr(Session, "commit", failing)
            response = quiet.post(f"/api/v1/sales/{draft['id']}/post", json={"payment_method": "CASH"})
        assert Session.commit is real and response.status_code == 503 and response.json()["success"] is False
        assert (
            client_a.get(f"/api/v1/sales/{draft['id']}").json()["status"] == "DRAFT"
            and stock(client_a, shelf["rice"]) == 10
        )

    def test_every_error_response_says_success_false_and_no_2xx_is_an_error(
        self, client_a, quiet, monkeypatch
    ):
        assert client_a.get("/api/v1/sales/999999").json()["success"] is False
        monkeypatch.setattr(product_service, "list_products", boom)
        assert quiet.get("/api/v1/products").json()["success"] is False
        monkeypatch.undo()
        ok = client_a.get("/api/v1/products")
        assert ok.status_code == 200 and "success" not in ok.json()

    def test_a_retry_after_a_failed_attempt_with_the_same_key_runs_once(
        self, client_a, quiet, shelf, monkeypatch
    ):
        draft = client_a.post("/api/v1/sales", json={"items": [item(shelf["rice"], "2")]}).json()
        headers = {"Idempotency-Key": "retry-attempt-0001"}
        url = f"/api/v1/sales/{draft['id']}/post"
        with monkeypatch.context() as m:
            m.setattr("app.services.numbering_service.next_number", boom)
            assert quiet.post(url, json={"payment_method": "CASH"}, headers=headers).status_code == 500
        assert stock(client_a, shelf["rice"]) == 10  # the failed attempt left nothing behind, key included
        first = client_a.post(url, json={"payment_method": "CASH"}, headers=headers)
        second = client_a.post(url, json={"payment_method": "CASH"}, headers=headers)
        assert (
            first.status_code == second.status_code == 200 and second.headers["Idempotent-Replay"] == "true"
        )
        assert stock(client_a, shelf["rice"]) == 8

    def test_an_unsafe_repeat_without_a_key_is_refused_not_repeated(self, client_a, shelf):
        draft = client_a.post("/api/v1/sales", json={"items": [item(shelf["rice"], "2")]}).json()
        url = f"/api/v1/sales/{draft['id']}/post"
        assert client_a.post(url, json={"payment_method": "CASH"}).status_code == 200
        assert client_a.post(url, json={"payment_method": "CASH"}).status_code == 409
        assert stock(client_a, shelf["rice"]) == 8
        assert client_a.get("/api/v1/sales").json()["total"] == 1

    def test_a_failed_create_leaves_no_duplicate_and_the_retry_creates_one(
        self, client_a, quiet, tenant_a, units, fresh, monkeypatch
    ):
        body = {
            "sku": "ONE",
            "name": "One",
            "category_id": tenant_a.category.id,
            "unit_id": units["pcs"],
            "selling_price": "5",
        }
        headers = {"Idempotency-Key": "product-attempt-01"}
        with monkeypatch.context() as m:
            m.setattr(product_service, "create_product", boom)
            assert quiet.post("/api/v1/products", json=body, headers=headers).status_code == 500
        assert client_a.get("/api/v1/products", params={"q": "ONE"}).json()["total"] == 0
        one = client_a.post("/api/v1/products", json=body, headers=headers)
        two = client_a.post("/api/v1/products", json=body, headers=headers)
        assert (
            one.status_code == two.status_code == 201
            and client_a.get("/api/v1/products", params={"q": "ONE"}).json()["total"] == 1
        )
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(Sale))) == 0


class TestRedaction:
    @pytest.mark.parametrize(
        ("raw", "gone"),
        [
            ("password=hunter2", "hunter2"),
            ('{"token": "abc.def.ghi"}', "abc.def"),
            ("api_key: SECRETVALUE99", "SECRETVALUE99"),
            ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGci"),
            ("user_key=0123456789abcdef", "0123456789abcdef"),
            ("used sk-proj-ABCDEFGHIJKLMNOPQRSTUV", "ABCDEFGHIJKLMNOP"),
            ("postgresql://admin:pw@db.internal:5432/kirana", "admin:pw"),
            ("sqlite:////Users/me/data/kirana.db", "/Users/me"),
            ("open /Users/me/project/app/x.py failed", "/Users/me"),
            (r"open C:\Users\me\app\x.py failed", r"C:\Users"),
            ("mail to person@example.com", "person@example.com"),
            ("call +91 98765 43210 now", "98765 43210"),
            ("card 4111 1111 1111 1111", "4111 1111"),
        ],
    )
    def test_secrets_paths_addresses_and_numbers_are_removed(self, raw, gone):
        assert gone not in redact(raw)

    def test_ordinary_text_is_kept_readable(self):
        text = "Stock replay mismatch for product 42: ledger says 10, replay says 9"
        assert redact(text) == text

    def test_a_summary_names_the_type_and_is_short(self):
        summary = sanitized_summary(ValueError("x" * 1000 + " api_key=SECRETVALUE99"))
        assert summary.startswith("ValueError: ") and len(summary) <= 300 and "SECRETVALUE99" not in summary

    def test_every_category_has_a_message_and_a_code(self):
        from app.api.errors import _CODES_BY_CATEGORY

        assert set(_CODES_BY_CATEGORY) >= set(ErrorCategory) - {ErrorCategory.NETWORK}
