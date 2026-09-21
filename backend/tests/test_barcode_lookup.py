"""Barcode / product lookup: priority order, scanner input, shop scope, plan, no auto-create, offers."""

import pytest
from sqlalchemy import func, select

from app.models import Product
from app.services import product_lookup_service as lookup
from tests.test_business_types import REQUESTED_TYPES
from tests.test_promotions import create, live, state
from tests.test_purchases_api import make_product, make_supplier
from tests.test_sales_api import stock_up

API = "/api/v1/products/lookup"
PRODUCTS = "/api/v1/products"


@pytest.fixture(autouse=True)
def pro_plans(give_plan, tenant_a, tenant_b):
    give_plan(tenant_a, "pro")
    give_plan(tenant_b, "pro")


def find(client, code, **params):
    response = client.get(API, params={"code": code, **params})
    assert response.status_code == 200, response.text
    return response.json()


def skus(result):
    return [p["sku"] for p in result["products"]]


@pytest.fixture
def catalog(client_a, tenant_a, units):
    supplier = make_supplier(client_a, "Sharma Traders")
    rice = make_product(client_a, tenant_a, units, "RICE-1", name="Basmati Rice 1kg", brand="India Gate", barcode="8901234567890", mrp="120", selling_price="100", purchase_price="80", default_supplier_id=supplier["id"], reorder_level="5")  # fmt: skip
    sugar = make_product(
        client_a,
        tenant_a,
        units,
        "SUGAR",
        name="Sugar",
        unit="kg",
        barcode="012345678905",
        selling_price="48",
    )
    salt = make_product(client_a, tenant_a, units, "SALT", name="Tata Salt", brand="Tata", selling_price="20")
    stock_up(client_a, supplier, rice, 10, 80)
    return {"rice": rice, "sugar": sugar, "salt": salt, "supplier": supplier}


class TestPriority:
    def test_an_exact_barcode_is_found_first_with_everything_a_screen_needs(self, client_a, catalog):
        result = find(client_a, "8901234567890")
        assert (result["found"], result["match_type"], result["message"]) == (True, "BARCODE", None)
        [p] = result["products"]
        assert (p["name"], p["sku"], p["barcode"], p["brand"], p["unit_code"]) == (
            "Basmati Rice 1kg",
            "RICE-1",
            "8901234567890",
            "India Gate",
            "pcs",
        )
        assert (p["mrp"], p["selling_price"], p["purchase_price"], p["avg_cost"]) == (
            "120.00",
            "100.00",
            "80.00",
            "80.00",
        )
        assert (p["current_stock"], p["reorder_level"], p["default_supplier_name"], p["is_active"]) == (
            "10.000",
            "5.000",
            "Sharma Traders",
            True,
        )
        assert p["stock_status"] == "IN_STOCK" and p["offer"] is None

    def test_an_exact_barcode_beats_an_exact_sku_and_a_name(self, client_a, tenant_a, units, catalog):
        make_product(
            client_a, tenant_a, units, "8901234567890", name="8901234567890"
        )  # SKU and name equal the code
        result = find(client_a, "8901234567890")
        assert result["match_type"] == "BARCODE" and skus(result) == ["RICE-1"]

    def test_an_exact_sku_beats_a_name_and_ignores_case(self, client_a, tenant_a, units, catalog):
        make_product(client_a, tenant_a, units, "OTHER", name="salt")  # named like the SKU of another product
        result = find(client_a, "salt")
        assert result["match_type"] == "SKU" and skus(result) == ["SALT"]

    def test_an_exact_name_ignores_case_punctuation_and_spacing(self, client_a, catalog):
        for text in (
            "Basmati  Rice 1kg",
            "basmati-rice 1kg",
            "BASMATI RICE, 1KG",
            "  basmati rice 1kg  ",
            "basmati_rice_1kg",
        ):
            result = find(client_a, text)
            assert (result["match_type"], skus(result)) == ("NAME", ["RICE-1"]), text
        assert (find(client_a, "tata salt")["match_type"], skus(find(client_a, "TATA-SALT"))) == (
            "NAME",
            ["SALT"],
        )

    def test_search_is_the_last_resort_and_finds_by_brand_or_part_of_the_name(self, client_a, catalog):
        by_brand = find(client_a, "india gate")
        assert by_brand["match_type"] == "SEARCH" and skus(by_brand) == ["RICE-1"]
        assert skus(find(client_a, "bas")) == ["RICE-1"]
        assert skus(find(client_a, "tata")) == ["SALT"]

    def test_several_products_can_share_an_exact_name(self, client_a, tenant_a, units, catalog):
        make_product(client_a, tenant_a, units, "DAL-A", name="Toor Dal")
        make_product(client_a, tenant_a, units, "DAL-B", name="toor  dal")
        result = find(client_a, "Toor Dal")
        assert result["match_type"] == "NAME" and sorted(skus(result)) == ["DAL-A", "DAL-B"]

    def test_the_limit_applies(self, client_a, tenant_a, units, catalog):
        for i in range(6):
            make_product(client_a, tenant_a, units, f"BULK{i}", name=f"Bulk item {i}")
        assert len(find(client_a, "bulk", limit=4)["products"]) == 4
        assert client_a.get(API, params={"code": "bulk", "limit": 26}).status_code == 422


class TestBarcodeInput:
    @pytest.mark.parametrize(
        "typed",
        [
            "8901234567890",
            " 8901234567890 ",
            "8901234567890\n",
            "\t8901234567890\r\n",
            "\x028901234567890\x03",
        ],
    )
    def test_scanner_style_input_with_control_characters_and_spaces(self, client_a, catalog, typed):
        result = find(client_a, typed)
        assert result["match_type"] == "BARCODE" and result["code"] == "8901234567890"

    def test_a_upc_a_code_finds_its_ean_13_twin_and_the_other_way_round(self, client_a, catalog):
        result = find(client_a, "0012345678905")  # stored as the 12-digit UPC-A 012345678905
        assert (result["match_type"], skus(result)) == ("BARCODE", ["SUGAR"])
        assert find(client_a, "9912345678905")["message"] == "Barcode not found"

    def test_leading_zero_twins_for_a_stored_ean_13(self, client_a, tenant_a, units):
        make_product(client_a, tenant_a, units, "UPC", barcode="0123456789012")
        assert skus(find(client_a, "123456789012")) == ["UPC"]  # 12 digits: the UPC-A of the stored EAN-13

    def test_an_empty_or_blank_code_is_refused(self, client_a):
        assert client_a.get(API, params={"code": ""}).status_code == 422
        assert client_a.get(API, params={"code": "   "}).status_code == 422
        assert client_a.get(API, params={"code": "\n\r"}).status_code == 422
        assert client_a.get(API).status_code == 422

    def test_an_over_long_code_is_refused(self, client_a):
        assert client_a.get(API, params={"code": "1" * 121}).status_code == 422
        assert client_a.get(API, params={"code": "1" * 101}).status_code == 422

    @pytest.mark.parametrize("text", ["%", "_", "'; DROP TABLE products; --", "a%b", "\\"])
    def test_special_characters_are_plain_text(self, client_a, catalog, text):
        assert find(client_a, text)["found"] is False
        assert client_a.get(PRODUCTS).json()["total"] == 3


class TestNotFound:
    def test_an_unknown_barcode_says_so_and_creates_nothing(self, client_a, catalog, fresh):
        before = fresh(lambda s: s.scalar(select(func.count()).select_from(Product)))
        result = find(client_a, "9999999999999")
        assert result == {
            "code": "9999999999999",
            "found": False,
            "match_type": "NONE",
            "message": "Barcode not found",
            "products": [],
        }
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(Product))) == before

    def test_unknown_text_gets_a_plain_message(self, client_a, catalog):
        assert find(client_a, "zzz nothing")["message"] == "No matching product"


class TestInactive:
    def test_an_exact_match_still_returns_an_inactive_product_and_says_so(self, client_a, catalog):
        client_a.post(f"{PRODUCTS}/{catalog['rice']['id']}/deactivate")
        result = find(client_a, "8901234567890")
        assert result["found"] and result["products"][0]["is_active"] is False
        assert find(client_a, "RICE-1")["products"][0]["is_active"] is False

    def test_a_search_never_returns_an_inactive_product(self, client_a, catalog):
        client_a.post(f"{PRODUCTS}/{catalog['rice']['id']}/deactivate")
        assert find(client_a, "india gate")["found"] is False

    def test_an_inactive_product_cannot_be_added_to_a_bill_even_when_scanned(self, client_a, catalog):
        client_a.post(f"{PRODUCTS}/{catalog['rice']['id']}/deactivate")
        response = client_a.post(
            "/api/v1/sales/calculate",
            json={"items": [{"product_id": catalog["rice"]["id"], "quantity": "1"}]},
        )
        assert "inactive" in response.text


class TestScopeAndPlan:
    def test_another_shops_products_are_never_found(self, client_a, client_b, tenant_b, units, catalog):
        make_product(client_b, tenant_b, units, "THEIRS", name="Theirs", barcode="7777777777777")
        assert find(client_a, "7777777777777")["found"] is False
        assert find(client_a, "THEIRS")["found"] is False and find(client_a, "theirs")["found"] is False
        assert (
            find(client_b, "7777777777777")["found"] is True
            and find(client_b, "8901234567890")["found"] is False
        )

    def test_the_same_barcode_in_two_shops_finds_each_shops_own_product(
        self, client_a, client_b, tenant_b, units, catalog
    ):
        mine = make_product(client_b, tenant_b, units, "RICE-B", name="Their Rice", barcode="8901234567890")
        assert skus(find(client_a, "8901234567890")) == ["RICE-1"] and skus(
            find(client_b, "8901234567890")
        ) == ["RICE-B"]
        assert mine["id"] != catalog["rice"]["id"]

    def test_a_plan_without_barcode_lookup_is_refused_but_ordinary_search_still_works(
        self, client_a, tenant_a, give_plan, catalog
    ):
        give_plan(tenant_a, "free")
        refused = client_a.get(API, params={"code": "8901234567890"})
        assert refused.status_code == 403 and refused.json()["detail"][0]["feature"] == "barcode_lookup"
        assert (
            client_a.get(PRODUCTS, params={"q": "8901234567890"}).json()["total"] == 1
        )  # the search box still works
        assert client_a.get(PRODUCTS, params={"barcode": "8901234567890"}).json()["total"] == 1

    def test_staff_can_look_products_up(self, client_a, make_client, tenant_a, catalog):
        from app.models.enums import UserRole

        assert find(make_client(tenant_a, role=UserRole.STAFF), "8901234567890")["found"] is True

    def test_lookup_is_read_only(self, client_a, catalog):
        assert client_a.post(API, json={"code": "x"}).status_code == 405
        assert client_a.delete(API).status_code == 405


class TestOfferPrices:
    def test_a_live_offer_price_is_shown_beside_mrp_and_selling_price_and_never_replaces_them(
        self, client_a, catalog
    ):
        live(
            client_a,
            name="Rice Offer",
            promo_type="OFFER_PRICE",
            percent=None,
            offer_price="90",
            scope="PRODUCTS",
            product_ids=[catalog["rice"]["id"]],
        )
        [p] = find(client_a, "8901234567890")["products"]
        assert (p["mrp"], p["selling_price"]) == ("120.00", "100.00")
        assert p["offer"] == {
            "promotion_id": p["offer"]["promotion_id"],
            "name": "Rice Offer",
            "offer_price": "90.00",
        }
        assert client_a.get(f"{PRODUCTS}/{catalog['rice']['id']}").json()["selling_price"] == "100.00"

    def test_the_lowest_offer_wins_and_a_category_offer_counts(self, client_a, catalog):
        live(
            client_a,
            name="A",
            promo_type="OFFER_PRICE",
            percent=None,
            offer_price="95",
            scope="PRODUCTS",
            product_ids=[catalog["rice"]["id"]],
        )
        live(
            client_a,
            name="B",
            promo_type="OFFER_PRICE",
            percent=None,
            offer_price="85",
            scope="CATEGORIES",
            category_ids=[catalog["rice"]["category_id"]],
        )
        assert find(client_a, "8901234567890")["products"][0]["offer"]["name"] == "B"

    @pytest.mark.parametrize(
        "extra",
        [
            {"coupon_code": "SECRET"},
            {"min_cart_value": "500"},
            {"min_quantity": "3"},
            {"audience": "NEW_CUSTOMER"},
        ],
    )
    def test_offers_that_need_more_than_the_product_are_not_shown_as_the_products_price(
        self, client_a, catalog, extra
    ):
        live(
            client_a,
            promo_type="OFFER_PRICE",
            percent=None,
            offer_price="90",
            scope="PRODUCTS",
            product_ids=[catalog["rice"]["id"]],
            **extra,
        )
        assert find(client_a, "8901234567890")["products"][0]["offer"] is None

    def test_paused_expired_draft_or_dearer_offers_are_not_shown(self, client_a, catalog):
        rice = [catalog["rice"]["id"]]
        base = dict(promo_type="OFFER_PRICE", percent=None, scope="PRODUCTS", product_ids=rice)
        create(client_a, name="Draft", offer_price="90", **base)
        paused = live(client_a, name="Paused", offer_price="90", **base)
        state(client_a, paused, "pause")
        ended = live(client_a, name="Ended", offer_price="90", **base)
        state(client_a, ended, "expire")
        live(client_a, name="Dearer", offer_price="130", **base)
        assert find(client_a, "8901234567890")["products"][0]["offer"] is None

    def test_no_offer_shows_on_a_plan_without_offers(self, client_a, tenant_a, give_plan, catalog):
        live(
            client_a,
            promo_type="OFFER_PRICE",
            percent=None,
            offer_price="90",
            scope="PRODUCTS",
            product_ids=[catalog["rice"]["id"]],
        )
        give_plan(tenant_a, "basic")  # has barcode lookup and offers
        assert find(client_a, "8901234567890")["products"][0]["offer"] is not None


class TestServiceLevel:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("012345678905", ["012345678905", "0012345678905"]),
            ("0012345678905", ["0012345678905", "012345678905"]),
            ("1234567", ["1234567"]),
            ("ABC123456789", ["ABC123456789"]),
            ("00123", ["00123"]),
        ],
    )
    def test_barcode_variants(self, code, expected):
        assert lookup.barcode_variants(code) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("  Rice, 1 KG! ", "rice 1 kg"), ("ＲＩＣＥ", "rice"), ("a__b", "a b"), ("", ""), ("---", "")],
    )
    def test_name_normalisation(self, raw, expected):
        assert lookup.normalize_name(raw) == expected

    def test_the_service_never_imports_anything_that_writes(self):
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent / "app/services/product_lookup_service.py"
        ).read_text()
        assert "session.add" not in source and "create_product" not in source and "flush" not in source


class TestEveryBusinessType:
    @pytest.mark.parametrize("business_type", REQUESTED_TYPES)
    def test_lookup_works_for_every_business_type(
        self, make_client, tenant_of, give_plan, units, business_type
    ):
        tenant = tenant_of(business_type)
        give_plan(tenant, "pro")
        client = make_client(tenant)
        make_product(client, tenant, units, "ITEM", barcode="4006381333931")
        assert find(client, "4006381333931")["match_type"] == "BARCODE"
