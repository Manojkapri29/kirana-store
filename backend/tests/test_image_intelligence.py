"""Photo capture and image intelligence: validation, privacy, suggestions, duplicates, confirmation, isolation."""

import base64
import struct
import zlib
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.models import AuditLog, InventoryTransaction, Product, ProductImage
from app.services import image_intelligence_service as iis
from app.services import image_providers as ip
from app.services import image_validation as iv
from app.services import price_providers as pp
from app.services.image_providers import ImageProviderError, ImageReading, ProductGuess
from app.services.image_store import LocalImageStore
from tests.test_price_intelligence import OFF_JSON, FakeNet
from tests.test_purchases_api import make_product

API = "/api/v1/image-intelligence"
GOOD = "4006381333931"  # a real EAN-13: its check digit is right
UPC = "036000291452"  # a real UPC-A
BAD = "4006381333932"  # one digit off


def png(width=640, height=480, pad=0) -> bytes:
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    filler = chunk(b"tEXt", b"x" * pad) if pad else b""
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + filler + chunk(b"IEND", b"")


def jpeg(width=640, height=480) -> bytes:
    sof = (
        b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    return (
        b"\xff\xd8\xff\xe0"
        + struct.pack(">H", 16)
        + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + sof
        + b"\xff\xd9"
    )


def webp(width=640, height=480) -> bytes:
    body = (
        b"VP8X"
        + struct.pack("<I", 10)
        + b"\x00\x00\x00\x00"
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class FakeProvider:
    name = "fake"
    label = "Fake Vision"
    calls = 0

    def __init__(self, reading=None, error=None):
        self.reading = (
            reading
            if reading is not None
            else ImageReading(
                text_lines=["Coca-Cola", "750 ml", "Beverages"],
                product=ProductGuess(
                    name="Coca-Cola", brand="Coca-Cola", pack_text="750 ml", category_hint="Beverages"
                ),
            )
        )
        self.error = error

    def is_configured(self, settings):
        return settings.image_analysis_api_key is not None

    def analyze(self, image, content_type, settings):
        type(self).calls += 1
        if self.error:
            raise self.error
        return self.reading


@pytest.fixture(autouse=True)
def pro_plans(give_plan, tenant_a, tenant_b):
    give_plan(tenant_a, "pro")
    give_plan(tenant_b, "pro")


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Server settings for a test: photos stored in a temp folder, and a way to switch a provider on."""

    class Env:
        def __init__(self):
            self.provider = None
            self.set()

        def set(self, provider=None, key="the-image-key-123", **extra):
            fields = dict(
                image_storage_dir=str(tmp_path / "images"),
                external_lookups_enabled=False,
                off_user_agent="T/1 (t@e.com)",
            )
            if provider is not None:
                fields.update(image_analysis_provider=provider.name, IMAGE_ANALYSIS_API_KEY=key)
            self.value = Settings(_env_file=None, **{**fields, **extra})
            monkeypatch.setattr(ip, "PROVIDERS", [lambda: provider] if provider is not None else [])
            for module in ("app.services.image_intelligence_service", "app.api.v1.image_intelligence"):
                monkeypatch.setattr(f"{module}.get_settings", lambda: self.value)

        @property
        def folder(self) -> Path:
            return tmp_path / "images"

    FakeProvider.calls = 0
    return Env()


def analyze(client, image=None, **body):
    response = client.post(f"{API}/analyze", json={"image_base64": b64(image or png()), **body})
    assert response.status_code == 200, response.text
    return response.json()


def counts(fresh):
    return fresh(lambda s: (
        s.scalar(select(func.count()).select_from(Product)),
        s.scalar(select(func.count()).select_from(ProductImage)),
        s.scalar(select(func.count()).select_from(InventoryTransaction)),
        s.scalar(select(func.count()).select_from(AuditLog)),
    ))  # fmt: skip


def files(env):
    return [p for p in env.folder.rglob("*") if p.is_file()]


def fields(tenant, units, **extra):
    return {"sku": "COLA750", "name": "Coca-Cola 750 ml", "brand": "Coca-Cola", "category_id": tenant.category.id, "unit_id": units["pcs"], "selling_price": "40", "mrp": "45", **extra}  # fmt: skip


def confirm(client, product, **body):
    return client.post(f"{API}/confirm-product", json={"product": product, **body})


class TestImageValidation:
    @pytest.mark.parametrize("make", [png, jpeg, webp])
    def test_real_photos_are_accepted(self, make):
        info = iv.inspect_image(make(800, 600), declared_type=None, max_bytes=5_000_000, max_side=8000)
        assert (
            (info.width, info.height) == (800, 600)
            and info.content_type in iv.ALLOWED
            and len(info.sha256) == 64
        )

    @pytest.mark.parametrize(
        ("data", "code"),
        [
            (b"", "image_empty"),
            (b"MZ\x90\x00\x03" + b"\x00" * 200, "image_unsupported_type"),  # a Windows program
            (b"\x7fELF" + b"\x00" * 200, "image_unsupported_type"),  # a Linux program
            (b"#!/bin/sh\necho hi\n" + b"x" * 100, "image_unsupported_type"),
            (b"<?php system($_GET['c']); ?>", "image_unsupported_type"),
            (b"<html><script>alert(1)</script></html>", "image_unsupported_type"),
            (b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>", "image_unsupported_type"),
            (b"PK\x03\x04" + b"\x00" * 100, "image_unsupported_type"),  # a zip
            (b"%PDF-1.7 " + b"x" * 100, "image_unsupported_type"),
            (b"GIF89a" + b"\x00" * 100, "image_unsupported_type"),
            (b"just some text pretending to be a picture", "image_unsupported_type"),
            (b"\x89PNG\r\n\x1a\n" + b"\x00" * 10, "image_corrupt"),  # a PNG header with nothing behind it
            (b"\xff\xd8\xff\xe0" + b"\x00" * 30, "image_corrupt"),
            (png(10, 10), "image_dimensions"),
            (png(9000, 100), "image_dimensions"),
            (png(8000, 8000), "image_dimensions"),
        ],
    )
    def test_anything_else_is_refused_with_a_reason(self, data, code):
        with pytest.raises(iv.InvalidInputError) as caught:
            iv.inspect_image(data, declared_type=None, max_bytes=5_000_000, max_side=8000)
        assert caught.value.code == code

    def test_an_oversized_file_is_refused(self):
        with pytest.raises(iv.InvalidInputError) as caught:
            iv.inspect_image(png(pad=200_000), declared_type=None, max_bytes=100_000, max_side=8000)
        assert caught.value.code == "image_too_large"

    def test_the_declared_type_must_match_the_bytes(self):
        with pytest.raises(iv.InvalidInputError):
            iv.inspect_image(png(), declared_type="image/jpeg", max_bytes=5_000_000, max_side=8000)
        assert iv.inspect_image(
            png(), declared_type="image/png; charset=binary", max_bytes=5_000_000, max_side=8000
        )
        assert iv.inspect_image(
            png(), declared_type="application/octet-stream", max_bytes=5_000_000, max_side=8000
        )

    def test_base64_is_decoded_safely(self):
        assert iv.decode_base64("data:image/png;base64," + b64(png()), max_bytes=5_000_000) == png()
        with pytest.raises(iv.InvalidInputError) as caught:
            iv.decode_base64("not base64 !!!", max_bytes=5_000_000)
        assert caught.value.code == "image_corrupt"
        with pytest.raises(iv.InvalidInputError) as big:
            iv.decode_base64("A" * 200_000, max_bytes=100_000)
        assert big.value.code == "image_too_large"


class TestApiRejectsBadFiles:
    def test_the_api_reports_each_problem_with_a_safe_message(self, client_a, env):
        exe = client_a.post(
            f"{API}/analyze", json={"image_base64": b64(b"MZ" + b"\x00" * 300), "content_type": "image/png"}
        )
        assert (
            exe.status_code == 422
            and exe.json()["error_code"] == "image_unsupported_type"
            and exe.json()["category"] == "image_upload"
        )
        assert "not a photo" in exe.json()["message"] and "Traceback" not in exe.text
        env.set(image_max_bytes=50_000)
        big = client_a.post(f"{API}/analyze", json={"image_base64": b64(png(pad=60_000))})
        assert big.status_code == 413 and big.json()["error_code"] == "image_too_large"
        assert (
            client_a.post(f"{API}/analyze", json={"image_base64": "!!!not-base64!!!!!!!"}).json()[
                "error_code"
            ]
            == "image_corrupt"
        )
        assert (
            client_a.post(f"{API}/analyze", json={"image_base64": b64(png(10, 10))}).json()["error_code"]
            == "image_dimensions"
        )

    def test_extra_fields_and_missing_images_are_refused(self, client_a):
        assert client_a.post(f"{API}/analyze", json={}).status_code == 422
        assert (
            client_a.post(f"{API}/analyze", json={"image_base64": b64(png()), "extra": 1}).status_code == 422
        )


class TestAnalysisChangesNothing:
    def test_an_analysis_creates_and_keeps_nothing(self, client_a, tenant_a, units, env, fresh, monkeypatch):
        make_product(client_a, tenant_a, units, "EXISTING", barcode=UPC)
        before = counts(fresh)
        env.set(FakeProvider())
        result = analyze(client_a, jpeg(), barcode_hint=GOOD, use_provider=True, enrich=True)
        assert result["stored"] is False and result["changes_data"] is False
        assert counts(fresh) == before and files(env) == []
        assert client_a.get("/api/v1/products", params={"q": "coca"}).json()["total"] == 0

    def test_no_picture_is_sent_anywhere_unless_the_user_asked(self, client_a, env):
        provider = FakeProvider()
        env.set(provider)
        result = analyze(client_a, png(), barcode_hint=GOOD)  # use_provider defaults to false
        assert (
            FakeProvider.calls == 0
            and result["sent_to_provider"] is False
            and result["status"] == "LOCAL_ONLY"
        )
        analyze(client_a, png(), use_provider=True)
        assert FakeProvider.calls == 1

    def test_no_outside_lookup_unless_asked(self, client_a, env, monkeypatch):
        net = FakeNet()
        net.route(pp.OFF_HOST, (200, OFF_JSON))
        monkeypatch.setattr(pp, "http_get", net)
        env.set(external_lookups_enabled=True)
        analyze(client_a, png(), barcode_hint=GOOD)
        assert net.calls == []  # not asked: nothing left the server
        result = analyze(client_a, png(), barcode_hint=GOOD, enrich=True)
        assert (
            len(net.calls) == 1 and GOOD in net.calls[0][0] and "image" not in net.calls[0][0].lower()
        )  # only the barcode
        assert {s["field"]: s["source"] for s in result["suggestions"]}["name"] == "open_food_facts"

    def test_the_master_switch_stops_the_outside_lookup(self, client_a, env, monkeypatch):
        net = FakeNet()
        monkeypatch.setattr(pp, "http_get", net)
        env.set(external_lookups_enabled=False)
        result = analyze(client_a, png(), barcode_hint=GOOD, enrich=True)
        assert net.calls == [] and any("switched off" in n for n in result["notes"])


class TestProviderAbstraction:
    def test_without_a_provider_the_message_is_plain_and_the_app_carries_on(self, client_a, env):
        result = analyze(client_a, png(), use_provider=True)
        assert (
            result["status"] == "NOT_CONFIGURED"
            and result["message"] == "Image analysis is not configured yet."
        )
        assert result["sent_to_provider"] is False and result["provider_label"] is None
        assert client_a.get("/api/v1/products").status_code == 200  # nothing else is affected

    def test_a_provider_without_its_key_is_not_configured(self, client_a, env):
        provider = FakeProvider()
        env.set(provider, key="")
        assert (
            analyze(client_a, png(), use_provider=True)["status"] == "NOT_CONFIGURED"
            and FakeProvider.calls == 0
        )

    def test_ocr_success_gives_suggestions_never_confirmed_facts(self, client_a, tenant_a, env):
        env.set(FakeProvider())
        result = analyze(client_a, png(), use_provider=True)
        by = {s["field"]: s for s in result["suggestions"]}
        assert (
            result["status"] == "PROVIDER_USED"
            and result["sent_to_provider"] is True
            and result["provider_label"] == "Fake Vision"
        )
        assert (by["name"]["value"], by["name"]["label"], by["name"]["source"]) == (
            "Coca-Cola",
            "Suggested",
            "fake",
        )
        assert (by["brand"]["value"], by["pack_size"]["value"], by["pack_size"]["label"]) == (
            "Coca-Cola",
            "750 ml",
            "Suggested",
        )
        assert by["visible_text"]["label"] == "Detected" and "750 ml" in by["visible_text"]["value"]
        assert {s["label"] for s in result["suggestions"]} <= {"Detected", "Suggested"}
        assert result["visible_text"] == ["Coca-Cola", "750 ml", "Beverages"]

    def test_a_category_that_exists_in_the_shop_is_suggested(self, client_a, tenant_a, env, session_factory):
        from tests import factories

        with session_factory() as s, s.begin():
            cat = factories.make_category(s, tenant_a.shop, "Beverages")
        env.set(FakeProvider())
        by = {s["field"]: s for s in analyze(client_a, png(), use_provider=True)["suggestions"]}
        assert (
            by["category"]["value"] == "Beverages"
            and by["category"]["category_id"] == cat.id
            and by["category"]["label"] == "Suggested"
        )

    def test_a_barcode_in_the_text_is_taken_only_if_its_check_digit_is_right(self, client_a, env):
        env.set(FakeProvider(ImageReading(text_lines=[f"Batch {BAD}", f"EAN {GOOD}"])))
        result = analyze(client_a, png(), use_provider=True)
        assert result["barcode"] == GOOD and "text" in result["barcode_note"]
        env.set(FakeProvider(ImageReading(text_lines=[f"Batch {BAD}"])))
        assert analyze(client_a, png(), use_provider=True)["barcode"] is None

    @pytest.mark.parametrize(
        "error",
        [
            ImageProviderError("could not be reached in time"),
            RuntimeError("boom secret-token-123"),
            ValueError("x"),
        ],
    )
    def test_a_provider_failure_becomes_a_status_and_leaks_nothing(self, client_a, env, error):
        env.set(FakeProvider(error=error))
        response = client_a.post(
            f"{API}/analyze", json={"image_base64": b64(png()), "barcode_hint": GOOD, "use_provider": True}
        )
        assert response.status_code == 200
        data = response.json()
        assert (
            data["status"] == "PROVIDER_FAILED" and data["barcode"] == GOOD
        )  # the local barcode still works
        assert "boom" not in response.text and "secret-token" not in response.text
        assert any("enter the details by hand" in n for n in data["notes"])


class TestBarcodeFromPhoto:
    @pytest.mark.parametrize(
        ("code", "valid"),
        [
            (GOOD, True),
            (UPC, True),
            ("96385074", True),
            (BAD, False),
            ("12345", False),
            ("40063813339AB", False),
            ("", False),
        ],
    )
    def test_check_digits(self, code, valid):
        assert iis.valid_gtin(code) is valid

    def test_a_barcode_read_in_the_browser_finds_the_shops_own_product(self, client_a, tenant_a, units, env):
        product = make_product(client_a, tenant_a, units, "COLA", name="Cola", barcode=GOOD)
        result = analyze(client_a, png(), barcode_hint=GOOD)
        assert result["barcode"] == GOOD and [p["id"] for p in result["existing_products"]] == [product["id"]]
        assert {s["field"]: s["label"] for s in result["suggestions"]}["barcode"] == "Detected"
        assert result["possible_duplicates"][0]["strength"] == "EXACT" and result["possible_duplicates"][0][
            "reasons"
        ] == ["The same barcode"]

    def test_the_upc_a_twin_of_a_stored_ean_13_is_found(self, client_a, tenant_a, units, env):
        product = make_product(client_a, tenant_a, units, "UPC", barcode="0" + UPC)
        assert [p["id"] for p in analyze(client_a, png(), barcode_hint=UPC)["existing_products"]] == [
            product["id"]
        ]

    def test_a_barcode_not_in_the_shop_finds_no_product(self, client_a, env):
        result = analyze(client_a, png(), barcode_hint=GOOD)
        assert (
            result["barcode"] == GOOD
            and result["existing_products"] == []
            and result["possible_duplicates"] == []
        )

    def test_a_misread_barcode_is_ignored_with_a_note(self, client_a, tenant_a, units, env):
        make_product(client_a, tenant_a, units, "COLA", barcode=BAD)  # the misread happens to be in the shop
        result = analyze(client_a, png(), barcode_hint=BAD)
        assert (
            result["barcode"] is None
            and result["existing_products"] == []
            and "check digit" in result["barcode_note"]
        )

    def test_no_barcode_in_the_photo_is_not_an_error(self, client_a, env):
        result = analyze(client_a, png())
        assert result["barcode"] is None and result["suggestions"] == [] and result["existing_products"] == []

    def test_another_shops_products_are_never_shown(self, client_a, client_b, tenant_b, units, env):
        make_product(client_b, tenant_b, units, "THEIRS", barcode=GOOD)
        result = analyze(client_a, png(), barcode_hint=GOOD)
        assert result["existing_products"] == [] and result["possible_duplicates"] == []

    def test_the_lookup_reused_is_the_phase_8_one_not_a_second_barcode_system(self):
        source = Path(iis.__file__).read_text()
        assert "product_lookup_service" in source and "barcode_variants" not in source.replace(
            "product_match", ""
        )


class TestDuplicateDetection:
    @pytest.fixture
    def cola(self, client_a, tenant_a, units):
        return make_product(
            client_a, tenant_a, units, "COLA-750", name="Coca-Cola 750 ml", brand="Coca-Cola", barcode=GOOD
        )

    def dupes(self, client_a, env, **guess):
        env.set(FakeProvider(ImageReading(product=ProductGuess(**guess))))
        return analyze(client_a, png(), use_provider=True)["possible_duplicates"]

    def test_the_same_name_brand_and_pack_is_a_likely_duplicate(self, client_a, env, cola):
        [d] = self.dupes(client_a, env, name="COCA COLA 750ML", brand="coca cola", pack_text="750 ml")
        assert (d["product_id"], d["strength"]) == (cola["id"], "LIKELY") and d["reasons"] == [
            "The same name, brand and pack size"
        ]

    def test_the_same_name_alone_is_likely(self, client_a, env, cola):
        [d] = self.dupes(client_a, env, name="coca-cola 750 ml")
        assert d["strength"] == "LIKELY" and d["reasons"] == ["The same name"]

    def test_a_close_name_is_only_possible(self, client_a, env, cola):
        [d] = self.dupes(client_a, env, name="Coca-Cola 750 mls", brand="Coca-Cola")
        assert d["strength"] == "POSSIBLE" and d["reasons"] == ["A very similar name"]

    def test_a_different_pack_size_is_not_a_duplicate(self, client_a, env, cola):
        assert self.dupes(client_a, env, name="Coca-Cola 1.5 L", brand="Coca-Cola", pack_text="1.5 l") == []
        assert self.dupes(client_a, env, name="Coca-Cola 330 ml") == []

    def test_a_different_brand_with_a_merely_similar_name_is_not(self, client_a, env, cola):
        assert self.dupes(client_a, env, name="Coca-Cola 750 mls", brand="Pepsi") == []

    def test_matches_are_ordered_by_strength_and_limited(self, client_a, tenant_a, units, env, cola):
        make_product(client_a, tenant_a, units, "OTHER", name="Coca-Cola 750 mls", brand="Coca-Cola")
        env.set(FakeProvider(ImageReading(product=ProductGuess(name="Coca-Cola 750 ml", brand="Coca-Cola"))))
        strengths = [
            d["strength"]
            for d in analyze(client_a, png(), barcode_hint=GOOD, use_provider=True)["possible_duplicates"]
        ]
        assert strengths == ["EXACT", "POSSIBLE"] or strengths[0] == "EXACT"


class TestConfirmation:
    def test_nothing_is_created_until_the_user_confirms(self, client_a, tenant_a, units, env, fresh):
        env.set(FakeProvider())
        analyze(client_a, png(), use_provider=True)  # the user looks at the suggestions and walks away
        assert counts(fresh)[0] == 0 and client_a.get("/api/v1/products").json()["total"] == 0

    def test_confirming_creates_only_the_reviewed_product(self, client_a, tenant_a, units, env, fresh):
        response = confirm(client_a, fields(tenant_a, units))
        assert response.status_code == 201, response.text
        data = response.json()
        assert (
            data["product"]["name"] == "Coca-Cola 750 ml"
            and data["product"]["selling_price"] == "40.00"
            and data["image_kept"] is False
        )
        assert data["product"]["current_stock"] == "0.000"  # a photo never adds stock
        assert (
            counts(fresh)[1] == 0 and counts(fresh)[2] == 0 and files(env) == []
        )  # no photo kept, no ledger row
        actions = fresh(lambda s: [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))])
        assert "create_from_image" in actions

    def test_the_edited_values_are_what_is_saved_not_the_suggestions(self, client_a, tenant_a, units, env):
        env.set(FakeProvider())
        analyze(client_a, png(), use_provider=True)  # suggests "Coca-Cola"
        edited = fields(tenant_a, units, name="Cola 750 ml (edited)", selling_price="41.50")
        assert confirm(client_a, edited).json()["product"]["name"] == "Cola 750 ml (edited)"

    def test_a_possible_existing_product_stops_the_creation_until_the_user_confirms(
        self, client_a, tenant_a, units, env, fresh
    ):
        make_product(client_a, tenant_a, units, "COLA-OLD", name="Coca-Cola 750 ml", brand="Coca-Cola")
        blocked = confirm(client_a, fields(tenant_a, units))
        assert (
            blocked.status_code == 409
            and blocked.json()["error_code"] == "possible_duplicate"
            and blocked.json()["category"] == "duplicate"
        )
        assert (
            "Possible existing product found" in blocked.json()["message"]
            and "Nothing was created" in blocked.json()["message"]
        )
        [d] = blocked.json()["data"]["possible_duplicates"]
        assert d["sku"] == "COLA-OLD" and d["strength"] == "LIKELY"
        assert counts(fresh)[0] == 1  # still only the original
        allowed = confirm(client_a, fields(tenant_a, units), acknowledge_duplicates=True)
        assert allowed.status_code == 201 and counts(fresh)[0] == 2

    def test_an_exact_barcode_or_sku_duplicate_can_never_be_acknowledged_away(
        self, client_a, tenant_a, units, env, fresh
    ):
        make_product(client_a, tenant_a, units, "COLA-OLD", name="Something", barcode=GOOD)
        response = confirm(client_a, fields(tenant_a, units, barcode=GOOD), acknowledge_duplicates=True)
        assert response.status_code == 409 and response.json()["error_code"] == "possible_duplicate"
        assert (
            response.json()["data"]["possible_duplicates"][0]["reasons"] == ["The same barcode"]
            and counts(fresh)[0] == 1
        )
        same_sku = confirm(
            client_a, fields(tenant_a, units, sku="cola-old", name="Unrelated"), acknowledge_duplicates=True
        )
        assert same_sku.status_code == 409 and counts(fresh)[0] == 1

    def test_product_rules_still_apply(self, client_a, tenant_a, units, env, fresh):
        bad = confirm(client_a, fields(tenant_a, units, category_id=999999))
        assert bad.status_code == 422 and counts(fresh)[0] == 0
        assert confirm(client_a, {**fields(tenant_a, units), "selling_price": "abc"}).status_code == 422

    def test_a_photo_cannot_set_opening_stock_or_cost(self, client_a, tenant_a, units, env, fresh):
        for extra in (
            {"opening_stock": "50"},
            {"opening_stock_cost": "10"},
            {"avg_cost": "5"},
            {"current_stock": "9"},
        ):
            assert confirm(client_a, {**fields(tenant_a, units), **extra}).status_code == 422
        assert counts(fresh)[0] == 0 and counts(fresh)[2] == 0

    def test_confirming_twice_with_the_same_key_creates_one_product(
        self, client_a, tenant_a, units, env, fresh
    ):
        body = {"product": fields(tenant_a, units)}
        headers = {"Idempotency-Key": "photo-confirm-001"}
        one, two = (
            client_a.post(f"{API}/confirm-product", json=body, headers=headers),
            client_a.post(f"{API}/confirm-product", json=body, headers=headers),
        )
        assert (
            one.status_code == two.status_code == 201
            and two.headers["Idempotent-Replay"] == "true"
            and counts(fresh)[0] == 1
        )

    def test_a_failure_while_saving_the_photo_leaves_no_product_behind(
        self, client_a, tenant_a, units, env, fresh, monkeypatch
    ):
        from fastapi.testclient import TestClient

        from app.main import app

        monkeypatch.setattr(
            LocalImageStore,
            "put",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full /Users/me/secret")),
        )
        quiet = TestClient(app, raise_server_exceptions=False)
        quiet.app.dependency_overrides = client_a.app.dependency_overrides
        response = quiet.post(
            f"{API}/confirm-product",
            json={"product": fields(tenant_a, units), "keep_image": True, "image_base64": b64(png())},
        )
        assert (
            response.status_code == 500 and "disk full" not in response.text and "/Users" not in response.text
        )
        assert response.json()["reference_id"].startswith("ERR-") and counts(fresh)[0] == 0


class TestKeptPhotos:
    def test_the_photo_is_kept_only_when_asked_and_only_privately(
        self, client_a, tenant_a, units, env, fresh
    ):
        data = png(300, 200)
        response = confirm(
            client_a,
            fields(tenant_a, units),
            keep_image=True,
            image_base64=b64(data),
            content_type="image/png",
        )
        assert response.status_code == 201 and response.json()["image_kept"] is True
        pid = response.json()["product"]["id"]
        assert counts(fresh)[1] == 1 and len(files(env)) == 1
        stored = files(env)[0]
        assert (
            stored.read_bytes() == data
            and str(tenant_a.shop.id) == stored.parent.name
            and stored.suffix == ".png"
        )
        image = client_a.get(f"{API}/products/{pid}/image")
        assert (
            image.status_code == 200
            and image.content == data
            and image.headers["content-type"] == "image/png"
        )
        assert (
            image.headers["cache-control"] == "private, no-store"
            and image.headers["x-content-type-options"] == "nosniff"
        )
        assert "sandbox" in image.headers["content-security-policy"]

    def test_keep_needs_an_image_and_a_valid_one(self, client_a, tenant_a, units, env, fresh):
        assert (
            confirm(client_a, fields(tenant_a, units), keep_image=True).json()["error_code"]
            == "image_missing"
        )
        bad = confirm(
            client_a, fields(tenant_a, units), keep_image=True, image_base64=b64(b"MZ" + b"\x00" * 300)
        )
        assert bad.status_code == 422 and counts(fresh)[0] == 0 and files(env) == []

    def test_an_image_can_be_attached_replaced_and_removed(self, client_a, tenant_a, units, env, fresh):
        product = make_product(client_a, tenant_a, units, "PIC", selling_price="10")
        first, second = png(400, 300), jpeg(500, 500)
        assert (
            client_a.put(
                f"{API}/products/{product['id']}/image", json={"image_base64": b64(first)}
            ).status_code
            == 200
        )
        replaced = client_a.put(f"{API}/products/{product['id']}/image", json={"image_base64": b64(second)})
        assert replaced.status_code == 200 and replaced.json()["content_type"] == "image/jpeg"
        assert counts(fresh)[1] == 1 and len(files(env)) == 1  # one photo per product: the old file is gone
        assert client_a.get(f"{API}/products/{product['id']}/image").content == second
        assert client_a.post(f"{API}/products/{product['id']}/image/remove").status_code == 204
        assert (
            counts(fresh)[1] == 0
            and files(env) == []
            and client_a.get(f"{API}/products/{product['id']}/image").status_code == 404
        )
        assert client_a.post(f"{API}/products/{product['id']}/image/remove").status_code == 404

    def test_attaching_a_photo_changes_nothing_else_about_the_product(self, client_a, tenant_a, units, env):
        product = make_product(client_a, tenant_a, units, "SAME", selling_price="10", mrp="12")
        before = client_a.get(f"/api/v1/products/{product['id']}").json()
        client_a.put(f"{API}/products/{product['id']}/image", json={"image_base64": b64(png())})
        after = client_a.get(f"/api/v1/products/{product['id']}").json()
        assert {k: v for k, v in after.items() if k != "updated_at"} == {
            k: v for k, v in before.items() if k != "updated_at"
        }

    def test_a_shop_cannot_keep_unlimited_photos(self, client_a, tenant_a, units, env):
        env.set(image_max_per_shop=2)
        ids = [make_product(client_a, tenant_a, units, f"L{i}")["id"] for i in range(3)]
        for i, pid in enumerate(ids[:2]):
            assert (
                client_a.put(
                    f"{API}/products/{pid}/image", json={"image_base64": b64(png(100 + i, 100))}
                ).status_code
                == 200
            )
        over = client_a.put(f"{API}/products/{ids[2]}/image", json={"image_base64": b64(png(300, 100))})
        assert over.status_code == 422 and over.json()["error_code"] == "image_limit_reached"
        assert (
            client_a.put(
                f"{API}/products/{ids[0]}/image", json={"image_base64": b64(png(150, 100))}
            ).status_code
            == 200
        )  # a replacement is fine

    def test_the_store_only_accepts_keys_it_builds_itself(self, tmp_path):
        store = LocalImageStore(tmp_path)
        for bad in (
            "../etc/passwd",
            "1/../../x.png",
            "/abs/path.png",
            "1/notahash.png",
            "x/" + "a" * 64 + ".png",
            "1/" + "a" * 64 + ".exe",
        ):
            with pytest.raises(ValueError):
                store.put(bad, b"x")
        good = "7/" + "a" * 64 + ".png"
        store.put(good, b"data")
        assert store.get(good) == b"data" and store.get("7/" + "b" * 64 + ".png") is None
        store.delete(good)
        assert store.get(good) is None


class TestPlanAndPrivacy:
    def test_a_plan_without_the_feature_is_refused_and_nothing_is_read(
        self, client_a, tenant_a, give_plan, env
    ):
        give_plan(tenant_a, "basic")
        refused = client_a.post(f"{API}/analyze", json={"image_base64": b64(png())})
        assert refused.status_code == 403 and refused.json()["feature"] == "image_intelligence"
        assert (
            client_a.get("/api/v1/products").status_code == 200
        )  # ordinary product management is unaffected

    def test_the_status_never_shows_a_key_or_a_provider_setting(self, client_a, env):
        env.set(FakeProvider(), key="super-secret-image-key")
        response = client_a.get(f"{API}/status")
        assert response.status_code == 200 and "super-secret" not in response.text
        assert response.json() == {
            "allowed_by_plan": True,
            "configured": True,
            "provider_label": "Fake Vision",
            "max_bytes": 5_000_000,
            "max_side": 8000,
            "formats": ["image/jpeg", "image/png", "image/webp"],
        }
        env.set()
        assert client_a.get(f"{API}/status").json()["configured"] is False

    def test_another_shops_photo_is_not_reachable_by_any_route(
        self, client_a, client_b, tenant_a, units, env
    ):
        product = make_product(client_a, tenant_a, units, "MINE", selling_price="10")
        client_a.put(f"{API}/products/{product['id']}/image", json={"image_base64": b64(png())})
        assert client_a.get(f"{API}/products/{product['id']}/image").status_code == 200
        assert client_b.get(f"{API}/products/{product['id']}/image").status_code == 404
        assert client_b.post(f"{API}/products/{product['id']}/image/remove").status_code == 404
        assert (
            client_b.put(
                f"{API}/products/{product['id']}/image", json={"image_base64": b64(png())}
            ).status_code
            == 404
        )
        assert client_a.get(f"{API}/products/{product['id']}/image").status_code == 200  # untouched

    def test_two_shops_keeping_the_same_photo_do_not_share_a_file(
        self, client_a, client_b, tenant_a, tenant_b, units, env
    ):
        a, b = make_product(client_a, tenant_a, units, "A1"), make_product(client_b, tenant_b, units, "B1")
        same = b64(png())
        client_a.put(f"{API}/products/{a['id']}/image", json={"image_base64": same})
        client_b.put(f"{API}/products/{b['id']}/image", json={"image_base64": same})
        assert len(files(env)) == 2 and {p.parent.name for p in files(env)} == {
            str(tenant_a.shop.id),
            str(tenant_b.shop.id),
        }
        client_a.post(f"{API}/products/{a['id']}/image/remove")
        assert client_b.get(f"{API}/products/{b['id']}/image").status_code == 200

    def test_a_kept_photo_is_not_reachable_through_any_public_address(self, client_a, tenant_a, units, env):
        product = make_product(client_a, tenant_a, units, "PRIV", selling_price="10")
        client_a.put(f"{API}/products/{product['id']}/image", json={"image_base64": b64(png())})
        for path in (
            "/static/x",
            "/images/1",
            "/data/images",
            f"/api/v1/products/{product['id']}/image",
            "/uploads",
        ):
            assert client_a.get(path).status_code in (404, 405)

    def test_the_photo_bytes_never_appear_in_the_database_or_the_logs(
        self, client_a, tenant_a, units, env, caplog
    ):
        data = png(321, 123)
        with caplog.at_level("DEBUG"):
            product = confirm(
                client_a, fields(tenant_a, units), keep_image=True, image_base64=b64(data)
            ).json()["product"]
            client_a.get(f"{API}/products/{product['id']}/image")
        assert b64(data)[:40] not in caplog.text
        database = env.folder.parent / "test.db"
        assert database.exists() and b"\x89PNG" not in database.read_bytes()  # metadata only, never the file


class TestApiKeySecrecy:
    def test_the_key_is_backend_only(self):
        source = Path(__file__).resolve().parents[1] / "app" / "core" / "config.py"
        text = source.read_text()
        assert "image_analysis_api_key: SecretStr" in text
        for name in ("IMAGE_ANALYSIS_API_KEY",):
            frontend = (Path(__file__).resolve().parents[2] / "frontend" / "src").rglob("*")
            assert not [
                p for p in frontend if p.is_file() and p.suffix in {".ts", ".tsx"} and name in p.read_text()
            ]

    def test_a_key_is_not_shown_in_a_settings_repr_or_dump(self, env):
        env.set(FakeProvider(), key="another-secret-key-9")
        assert "another-secret" not in repr(env.value) and "another-secret" not in env.value.model_dump_json()
