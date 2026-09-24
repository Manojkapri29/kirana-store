"""Storage providers, accounting exports and mappings, location helpers, platform entries."""

import csv
import hashlib
import io
import re
from decimal import Decimal

import pytest

from app.integrations import base, location, s3_store
from app.integrations.base import ProviderError
from app.services import image_store
from tests import factories
from tests.client_helpers import client_with
from tests.conftest import context_for
from tests.factories import today_in_shop_timezone
from tests.finance_helpers import make_purchase, make_quick_sale, make_sale
from tests.integration_helpers import FakeS3

API = "/api/v1/integrations"
TODAY = today_in_shop_timezone()
KEY = f"1/{hashlib.sha256(b'x').hexdigest()}.png"


class TestS3Signing:
    def test_the_signing_key_derivation_matches_the_published_aws_example(self):
        # From the AWS Signature V4 documentation ("Examples of how to derive a signing key"): secret wJalr..., 20150830, us-east-1, iam.
        key = s3_store.signing_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20150830", "us-east-1", "iam")
        assert key.hex() == "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9"

    def test_the_authorization_header_has_the_documented_shape(self):
        header = s3_store.authorization(
            method="GET",
            path="/b/k",
            host="h",
            payload_hash=hashlib.sha256(b"").hexdigest(),
            amz_date="20260924T101010Z",
            region="us-east-1",
            access_key="AK",
            secret="SK",
        )
        assert header.startswith(
            "AWS4-HMAC-SHA256 Credential=AK/20260924/us-east-1/s3/aws4_request, SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature="
        )


class TestS3Store:
    @pytest.fixture
    def server(self):
        s = FakeS3()
        yield s
        s.stop()

    def _store(self, server, **over):
        args = {
            "endpoint": server.endpoint,
            "bucket": "kirana",
            "region": "us-east-1",
            "access_key": "AKIDEXAMPLE",
            "secret": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        }
        return s3_store.S3Store(**{**args, **over})

    def test_put_get_delete_round_trip_with_a_verified_signature(self, server):
        store = self._store(server)
        store.put(KEY, b"image-bytes")
        assert store.get(KEY) == b"image-bytes"
        assert list(server.objects) == [f"/kirana/{KEY}"]
        store.delete(KEY)
        assert store.get(KEY) is None and server.rejected == 0

    def test_wrong_credentials_are_rejected_by_the_server(self, server):
        with pytest.raises(ProviderError) as info:
            self._store(server, secret="not-the-secret").put(KEY, b"x")
        assert info.value.code == "invalid_credentials" and server.objects == {}

    def test_keys_with_path_tricks_are_refused_before_any_request(self, server):
        store = self._store(server)
        for key in (
            "../../etc/passwd",
            "1/../2/" + "a" * 64 + ".png",
            "1/abc.png",
            "1/" + "g" * 64 + ".png",
            "/1/" + "a" * 64 + ".png",
            "1/" + "a" * 64 + ".exe",
        ):
            with pytest.raises(ValueError):
                store.put(key, b"x")
        assert server.objects == {}

    def test_an_unreachable_server_is_a_retryable_error(self):
        with pytest.raises(ProviderError) as info:
            s3_store.S3Store(
                endpoint="http://127.0.0.1:9", bucket="b", region="r", access_key="a", secret="s", timeout=1
            ).get(KEY)
        assert info.value.code == "connection_failed" and info.value.retryable

    def test_redirects_are_not_followed(self, server):
        import http.server
        import threading

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/steal")
                self.end_headers()

            def log_message(self, *a):  # noqa: ANN002
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with pytest.raises(ProviderError):
                s3_store.S3Store(
                    endpoint=f"http://127.0.0.1:{srv.server_address[1]}",
                    bucket="b",
                    region="r",
                    access_key="a",
                    secret="s",
                ).get(KEY)
        finally:
            srv.shutdown()

    def test_the_default_store_is_local_and_s3_needs_full_configuration(self, monkeypatch, tmp_path):
        from app.core.config import Settings, get_settings

        assert isinstance(image_store.default_store(get_settings()), image_store.LocalImageStore)
        with pytest.raises(RuntimeError):
            image_store.default_store(Settings(storage_provider="s3"))
        monkeypatch.setenv("KIRANA_INTEGRATION_STORAGE_CREDENTIALS", "AK:SK")
        store = image_store.default_store(
            Settings(
                storage_provider="s3", storage_s3_endpoint="https://s3.example.test", storage_s3_bucket="b"
            )
        )
        assert isinstance(store, s3_store.S3Store)

    def test_the_storage_probe_round_trips_and_leaves_nothing_behind(self, client_a, tmp_path, monkeypatch):
        folder = tmp_path / "files"
        monkeypatch.setenv("KIRANA_IMAGE_STORAGE_DIR", str(folder))
        from app.core.config import get_settings

        get_settings.cache_clear()
        r = client_a.post(f"{API}/platform/storage/test").json()
        assert r == {"ok": True, "code": None, "provider": "local"}
        assert [p for p in folder.rglob("*") if p.is_file()] == []

    def test_the_storage_probe_works_against_s3_and_reports_failures_without_secrets(
        self, client_a, monkeypatch
    ):
        server = FakeS3()
        try:
            monkeypatch.setenv("KIRANA_STORAGE_PROVIDER", "s3")
            monkeypatch.setenv("KIRANA_STORAGE_S3_ENDPOINT", server.endpoint)
            monkeypatch.setenv("KIRANA_STORAGE_S3_BUCKET", "kirana")
            monkeypatch.setenv(
                "KIRANA_INTEGRATION_STORAGE_CREDENTIALS",
                "AKIDEXAMPLE:wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
            )
            from app.core.config import get_settings

            get_settings.cache_clear()
            assert client_a.post(f"{API}/platform/storage/test").json()["ok"] is True
            monkeypatch.setenv("KIRANA_INTEGRATION_STORAGE_CREDENTIALS", "AKIDEXAMPLE:wrong-secret")
            bad = client_a.post(f"{API}/platform/storage/test").json()
            assert (
                bad["ok"] is False and bad["code"] == "invalid_credentials" and "wrong-secret" not in str(bad)
            )
            platform = {p["integration_type"]: p for p in client_a.get(API).json()["platform"]}
            assert platform["STORAGE"]["provider"] == "s3" and platform["STORAGE"]["status"] == "CONFIGURED"
        finally:
            server.stop()

    def test_local_store_keys_are_still_validated(self, tmp_path):
        store = image_store.LocalImageStore(tmp_path)
        with pytest.raises(ValueError):
            store.put("../x.png", b"x")


class TestLocation:
    def test_distance_needs_no_provider_and_sends_nothing(self, client_a):
        r = client_a.post(
            f"{API}/location/distance",
            json={"lat1": "28.6139", "lon1": "77.2090", "lat2": "19.0760", "lon2": "72.8777"},
        ).json()
        assert (
            Decimal("1140") < Decimal(r["distance_km"]) < Decimal("1170")
            and "not a road distance" in r["basis"]
        )
        assert location.distance_km(Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10")) == Decimal(
            "0.00"
        )

    def test_out_of_range_coordinates_are_refused(self, client_a):
        assert (
            client_a.post(
                f"{API}/location/distance", json={"lat1": "91", "lon1": "0", "lat2": "0", "lon2": "0"}
            ).status_code
            == 422
        )

    def test_geocoding_says_provider_not_configured(self, client_a):
        r = client_a.post(f"{API}/location/geocode", json={"address": "1 Main Road"})
        assert r.status_code == 409 and "Provider Not Configured" in r.text

    def test_maps_and_storage_are_not_per_shop_types(self, client_a):
        assert client_a.put(f"{API}/MAPS", json={"provider": "x", "config": {}}).status_code == 422


class TestPlatformEntries:
    def test_product_data_follows_the_barcode_key(self, client_a, monkeypatch):
        from app.core.config import get_settings

        assert (
            next(p for p in client_a.get(API).json()["platform"] if p["integration_type"] == "PRODUCT_DATA")[
                "message"
            ]
            == "Provider Not Configured"
        )
        monkeypatch.setenv("UPCITEMDB_API_KEY", "some-key-value")
        get_settings.cache_clear()
        entry = next(
            p for p in client_a.get(API).json()["platform"] if p["integration_type"] == "PRODUCT_DATA"
        )
        assert entry["status"] == "CONFIGURED" and "some-key-value" not in str(client_a.get(API).json())
        assert "Suggestions only" in entry["note"]


class TestAccountingExport:
    def _rows(self, response):
        text = response.content.decode("utf-8").lstrip("﻿")
        rows = list(csv.reader(io.StringIO(text)))
        blank = [i for i, r in enumerate(rows) if not r]
        header = rows[blank[0] + 1]
        data = rows[blank[0] + 2 : blank[1] if len(blank) > 1 else len(rows)]
        return header, [dict(zip(header, r, strict=True)) for r in data], rows

    def _get(self, client, kind, status=200, **params):
        r = client.get(
            f"{API}/accounting/export/{kind}",
            params={"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat(), **params},
        )
        assert r.status_code == status, r.text
        return r

    def test_transactions_come_from_the_finance_ledger_with_your_account_codes(
        self, client_a, session, tenant_a
    ):
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        make_purchase(session, tenant_a, factories.make_supplier(session, tenant_a.shop), "300.00", day=TODAY)
        session.commit()
        assert (
            client_a.put(
                f"{API}/accounting/mappings",
                json={"source_key": "EVENT:SALE", "external_code": "4000", "external_name": "Sales"},
            ).status_code
            == 200
        )
        assert (
            client_a.put(
                f"{API}/accounting/mappings", json={"source_key": "METHOD:CASH", "external_code": "1000"}
            ).status_code
            == 200
        )
        header, rows, raw = self._rows(self._get(client_a, "transactions"))
        sale = next(r for r in rows if r["Event"] == "SALE")
        purchase = next(r for r in rows if r["Event"] == "PURCHASE")
        assert (
            sale["Amount"] == "1000.00"
            and sale["Account code"] == "4000"
            and sale["Payment account code"] == "1000"
            and sale["Mapping"] == "MAPPED"
        )
        assert purchase["Account code"] == "" and purchase["Mapping"] == "UNMAPPED"  # nothing is guessed
        assert any("1 of 2 rows have no account code" in " ".join(r) for r in raw)

    def test_the_export_agrees_with_the_finance_ledger_service(self, client_a, session, tenant_a):
        from app.services import finance_ledger_service

        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        make_quick_sale(session, tenant_a, "250.00", day=TODAY)
        session.commit()
        _, rows, _ = self._rows(self._get(client_a, "transactions"))
        ledger = finance_ledger_service.movements(session, tenant_a.shop.id, TODAY, TODAY)
        assert sorted(Decimal(r["Amount"]) for r in rows) == sorted(m.amount for m in ledger)

    def test_invoices_customers_suppliers_and_tax(self, client_a, session, tenant_a):
        c = factories.make_customer(session, tenant_a.shop, name="Asha", phone="9876543210")
        factories.make_supplier(session, tenant_a.shop, name="Alpha")
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00", customer_id=c.id)
        make_quick_sale(session, tenant_a, "250.00", day=TODAY)
        session.commit()
        _, inv, _ = self._rows(self._get(client_a, "invoices"))
        assert {(r["Kind"], r["Total"]) for r in inv} == {
            ("Detailed sale", "1000.00"),
            ("Quick sale", "250.00"),
        }
        assert next(r for r in inv if r["Kind"] == "Detailed sale")["Customer"] == "Asha"
        _, customers, _ = self._rows(self._get(client_a, "customers"))
        assert customers[0]["Name"] == "Asha"
        _, suppliers, _ = self._rows(self._get(client_a, "suppliers"))
        assert suppliers[0]["Name"] == "Alpha"
        _, tax, raw = self._rows(self._get(client_a, "tax"))
        assert tax == [] and any(
            "not configured" in " ".join(r) for r in raw
        )  # no tax set up: no rows, nothing guessed

    def test_xlsx_and_a_bad_format(self, client_a):
        assert (
            self._get(client_a, "invoices", format="xlsx")
            .headers["content-type"]
            .startswith("application/vnd.openxmlformats")
        )
        assert self._get(client_a, "invoices", status=422, format="pdf")
        assert self._get(client_a, "nonsense", status=404)
        assert (
            client_a.get(
                f"{API}/accounting/export/invoices",
                params={"date_from": "2026-02-01", "date_to": "2026-01-01"},
            ).status_code
            == 422
        )

    def test_formula_text_in_a_customer_name_is_neutralised(self, client_a, session, tenant_a):
        factories.make_customer(session, tenant_a.shop, name='=HYPERLINK("http://evil")')
        session.commit()
        _, rows, _ = self._rows(self._get(client_a, "customers"))
        assert rows[0]["Name"].startswith("'=")

    def test_mapping_rules(self, client_a):
        assert (
            client_a.put(
                f"{API}/accounting/mappings", json={"source_key": "EVENT:NOPE", "external_code": "1"}
            ).status_code
            == 422
        )
        assert (
            client_a.put(
                f"{API}/accounting/mappings", json={"source_key": "EVENT:SALE", "external_code": "  "}
            ).status_code
            == 422
        )
        assert (
            client_a.put(
                f"{API}/accounting/mappings", json={"source_key": "EVENT:SALE", "external_code": "4\x00"}
            ).status_code
            == 422
        )
        client_a.put(f"{API}/accounting/mappings", json={"source_key": "EVENT:SALE", "external_code": "4000"})
        client_a.put(
            f"{API}/accounting/mappings", json={"source_key": "EVENT:SALE", "external_code": "4100"}
        )  # changing replaces, never duplicates
        assert [m["external_code"] for m in client_a.get(f"{API}/accounting/mappings").json()["items"]] == [
            "4100"
        ]
        assert (
            client_a.post(f"{API}/accounting/mappings/clear", json={"source_key": "EVENT:SALE"}).status_code
            == 200
        )
        assert (
            client_a.post(f"{API}/accounting/mappings/clear", json={"source_key": "EVENT:SALE"}).status_code
            == 404
        )
        assert client_a.delete(f"{API}/accounting/mappings").status_code == 405

    def test_mappings_and_exports_are_shop_scoped(self, client_a, client_b, session, tenant_a):
        make_sale(session, tenant_a, "1000.00", day=TODAY, cogs="600.00")
        session.commit()
        client_a.put(f"{API}/accounting/mappings", json={"source_key": "EVENT:SALE", "external_code": "4000"})
        assert client_b.get(f"{API}/accounting/mappings").json()["items"] == []
        _, rows, _ = self._rows(self._get(client_b, "transactions"))
        assert rows == []

    def test_permissions(self, session, tenant_a, make_client):
        assert (
            client_with(make_client, tenant_a, ["INTEGRATION_VIEW"])
            .get(
                f"{API}/accounting/export/invoices",
                params={"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()},
            )
            .status_code
            == 403
        )
        only_export = client_with(make_client, tenant_a, ["INTEGRATION_VIEW", "FINANCE_EXPORT"])
        q = {"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()}
        assert (
            only_export.get(f"{API}/accounting/export/transactions", params=q).status_code == 403
        )  # also needs FINANCE_VIEW
        assert (
            only_export.get(f"{API}/accounting/export/customers", params=q).status_code == 403
        )  # also needs CUSTOMER_VIEW
        assert (
            only_export.put(
                f"{API}/accounting/mappings", json={"source_key": "EVENT:SALE", "external_code": "1"}
            ).status_code
            == 403
        )
        assert (
            client_with(make_client, tenant_a, ["INTEGRATION_VIEW", "FINANCE_EXPORT", "REPORT_VIEW"])
            .get(f"{API}/accounting/export/invoices", params=q)
            .status_code
            == 200
        )


def test_no_secret_appears_anywhere_in_the_source_tree_defaults():
    from pathlib import Path

    from app.core.config import Settings

    assert (
        Settings().storage_provider == "local"
        and base.resolve_secret("KIRANA_INTEGRATION_STORAGE_CREDENTIALS") is None
    )
    root = Path(__file__).resolve().parents[1] / "app"
    for path in root.rglob("*.py"):
        text = path.read_text()
        assert not re.search(r"AKIA[0-9A-Z]{16}", text) and "BEGIN PRIVATE KEY" not in text, path
    assert context_for is not None
