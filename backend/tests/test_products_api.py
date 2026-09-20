"""Product API: create, read, list/search/filter, update, activate/deactivate, validation, isolation, audit."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, InventoryTransaction, Product
from app.models.enums import MrpValidationMode

API = "/api/v1/products"


def body(tenant, units, **overrides) -> dict:
    fields = {
        "sku": "RICE-5KG",
        "name": "Rice 5kg",
        "category_id": tenant.category.id,
        "unit_id": units["pcs"],
        "selling_price": "250.00",
    }
    fields.update(overrides)
    return fields


def create(client, tenant, units, **overrides):
    response = client.post(API, json=body(tenant, units, **overrides))
    assert response.status_code == 201, response.text
    return response.json()["product"]


def errors(response) -> dict[str, str]:
    """Field name -> message from a 409/422 response."""
    return {item["loc"][-1]: item["msg"] for item in response.json()["detail"]}


class TestCreate:
    def test_minimal_product(self, client_a, tenant_a, units):
        response = client_a.post(API, json=body(tenant_a, units))

        assert response.status_code == 201
        product = response.json()["product"]
        assert product["sku"] == "RICE-5KG"
        assert product["selling_price"] == "250.00"  # money is text, exact
        assert product["category_name"] == "Grocery"
        assert product["unit_code"] == "pcs"
        assert product["is_active"] is True
        assert response.json()["warnings"] == []

    def test_missing_prices_stay_null_and_are_not_silently_zero(self, client_a, tenant_a, units):
        product = create(client_a, tenant_a, units)

        assert product["mrp"] is None
        assert product["purchase_price"] is None
        assert product["avg_cost"] is None

    def test_an_explicit_zero_price_is_kept_as_zero(self, client_a, tenant_a, units):
        product = create(client_a, tenant_a, units, purchase_price="0", mrp="0")

        assert product["purchase_price"] == "0.00"
        assert product["mrp"] == "0.00"

    def test_all_fields(self, client_a, tenant_a, units):
        product = create(
            client_a, tenant_a, units,
            brand="India Gate", barcode="8901234567890", mrp="300.00", purchase_price="210.50",
            reorder_level="5", unit_id=units["kg"],
        )  # fmt: skip

        assert (product["brand"], product["barcode"], product["mrp"]) == (
            "India Gate",
            "8901234567890",
            "300.00",
        )
        assert product["purchase_price"] == "210.50"
        assert product["reorder_level"] == "5.000"
        assert product["unit_allows_decimal"] is True

    def test_mrp_is_stored_separately_from_selling_price(self, client_a, tenant_a, units, fresh):
        product = create(client_a, tenant_a, units, mrp="300", selling_price="270")

        row = fresh(lambda s: s.get(Product, product["id"]))
        assert (row.mrp, row.selling_price) == (Decimal("300.00"), Decimal("270.00"))

    def test_sku_is_trimmed_and_upper_cased(self, client_a, tenant_a, units):
        assert create(client_a, tenant_a, units, sku="  rice-5kg ")["sku"] == "RICE-5KG"

    def test_a_product_has_no_stock_column_and_starts_at_zero(self, client_a, tenant_a, units):
        product = create(client_a, tenant_a, units)

        assert "current_stock" not in Product.__table__.columns
        assert product["current_stock"] == "0.000"
        assert product["stock_status"] == "OUT_OF_STOCK"

    def test_opening_stock_can_be_given_at_creation(self, client_a, tenant_a, units, fresh):
        product = create(client_a, tenant_a, units, opening_stock="20", opening_stock_cost="200")

        assert product["current_stock"] == "20.000"
        assert product["avg_cost"] == "200.00"
        rows = fresh(lambda s: s.scalars(select(InventoryTransaction)).all())
        assert [(r.txn_type.value, r.qty_delta) for r in rows] == [("OPENING", Decimal("20.000"))]

    def test_zero_opening_stock_means_none(self, client_a, tenant_a, units, fresh):
        create(client_a, tenant_a, units, opening_stock="0")

        assert fresh(lambda s: s.scalar(select(func.count()).select_from(InventoryTransaction))) == 0

    def test_a_failing_opening_stock_creates_no_product_at_all(self, client_a, tenant_a, units, fresh):
        response = client_a.post(
            API, json=body(tenant_a, units, opening_stock="2.5")
        )  # pieces cannot be split

        assert response.status_code == 422
        assert "opening_stock" in errors(response) or "quantity" in errors(response)
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(Product))) == 0

    def test_opening_cost_without_quantity_is_an_error(self, client_a, tenant_a, units):
        response = client_a.post(API, json=body(tenant_a, units, opening_stock_cost="100"))

        assert response.status_code == 422
        assert "opening_stock" in errors(response)


class TestUniqueness:
    def test_duplicate_sku_in_the_same_shop_is_refused(self, client_a, tenant_a, units):
        create(client_a, tenant_a, units)

        response = client_a.post(API, json=body(tenant_a, units, name="Other"))

        assert response.status_code == 409
        assert "already used" in errors(response)["sku"]

    def test_sku_uniqueness_ignores_case(self, client_a, tenant_a, units):
        create(client_a, tenant_a, units, sku="RICE-5KG")

        assert client_a.post(API, json=body(tenant_a, units, sku="rice-5kg")).status_code == 409

    def test_the_same_sku_can_exist_in_different_shops(self, client_a, client_b, tenant_a, tenant_b, units):
        create(client_a, tenant_a, units)
        create(client_b, tenant_b, units)  # no error

    def test_duplicate_barcode_in_the_same_shop_is_refused(self, client_a, tenant_a, units):
        create(client_a, tenant_a, units, barcode="8901234567890")

        response = client_a.post(API, json=body(tenant_a, units, sku="OTHER", barcode="8901234567890"))

        assert response.status_code == 409
        assert "barcode" in errors(response)

    def test_the_same_barcode_can_exist_in_different_shops(
        self, client_a, client_b, tenant_a, tenant_b, units
    ):
        create(client_a, tenant_a, units, barcode="8901234567890")
        create(client_b, tenant_b, units, barcode="8901234567890")

    def test_barcode_is_optional_and_many_products_may_have_none(self, client_a, tenant_a, units):
        for sku in ("A", "B", "C"):
            assert create(client_a, tenant_a, units, sku=sku)["barcode"] is None

    def test_a_blank_barcode_counts_as_no_barcode(self, client_a, tenant_a, units):
        assert create(client_a, tenant_a, units, sku="A", barcode="")["barcode"] is None
        assert create(client_a, tenant_a, units, sku="B", barcode="   ")["barcode"] is None


class TestValidation:
    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("selling_price", "-1", "cannot be negative"),
            ("selling_price", "10.999", "at most 2 decimal"),
            ("selling_price", "abc", "valid number"),
            ("selling_price", "", "valid number"),
            ("purchase_price", "-5", "cannot be negative"),
            ("mrp", "1.005", "at most 2 decimal"),
            ("reorder_level", "-1", "cannot be negative"),
            ("reorder_level", "1.0005", "at most 3 decimal"),
            ("selling_price", "99999999999", "too large"),
        ],
    )
    def test_invalid_numbers_are_rejected_with_a_clear_message(
        self, client_a, tenant_a, units, field, value, message
    ):
        response = client_a.post(API, json=body(tenant_a, units, **{field: value}))

        assert response.status_code == 422
        assert message in errors(response)[field].lower()

    def test_a_json_number_with_a_fraction_is_refused_so_no_float_sneaks_in(self, client_a, tenant_a, units):
        response = client_a.post(API, json=body(tenant_a, units, selling_price=25.5))

        assert response.status_code == 422
        assert "as text" in errors(response)["selling_price"]

    def test_whole_json_numbers_and_text_are_both_accepted(self, client_a, tenant_a, units):
        assert create(client_a, tenant_a, units, sku="A", selling_price=25)["selling_price"] == "25.00"
        assert create(client_a, tenant_a, units, sku="B", selling_price="25.5")["selling_price"] == "25.50"

    def test_required_fields(self, client_a, tenant_a, units):
        for missing in ("sku", "name", "category_id", "unit_id", "selling_price"):
            data = body(tenant_a, units)
            del data[missing]
            response = client_a.post(API, json=data)
            assert response.status_code == 422, missing
            assert missing in errors(response)

    def test_blank_sku_and_blank_name_get_friendly_messages(self, client_a, tenant_a, units):
        blank_sku = client_a.post(API, json=body(tenant_a, units, sku="   "))
        blank_name = client_a.post(API, json=body(tenant_a, units, name="  "))

        assert (blank_sku.status_code, errors(blank_sku)) == (422, {"sku": "Enter a SKU."})
        assert (blank_name.status_code, errors(blank_name)) == (422, {"name": "Enter a product name."})

    @pytest.mark.parametrize("field", ["current_stock", "avg_cost", "id", "is_active", "shop_id"])
    def test_stock_average_cost_and_other_server_owned_fields_cannot_be_set(
        self, client_a, tenant_a, units, field
    ):
        response = client_a.post(API, json=body(tenant_a, units, **{field: "5"}))

        assert response.status_code == 422
        assert field in errors(response)

    def test_unknown_category_and_unit(self, client_a, tenant_a, units):
        assert "category_id" in errors(client_a.post(API, json=body(tenant_a, units, category_id=9999)))
        assert "unit_id" in errors(client_a.post(API, json=body(tenant_a, units, unit_id=9999)))

    def test_another_shops_category_cannot_be_used(self, client_a, tenant_a, tenant_b, units):
        response = client_a.post(API, json=body(tenant_a, units, category_id=tenant_b.category.id))

        assert response.status_code == 422
        assert "category_id" in errors(response)

    def test_fractional_reorder_level_needs_a_unit_that_allows_decimals(self, client_a, tenant_a, units):
        assert "reorder_level" in errors(client_a.post(API, json=body(tenant_a, units, reorder_level="2.5")))
        assert (
            create(client_a, tenant_a, units, reorder_level="2.5", unit_id=units["kg"])["reorder_level"]
            == "2.500"
        )

    def test_unknown_default_supplier_is_refused(self, client_a, tenant_a, units):
        response = client_a.post(API, json=body(tenant_a, units, default_supplier_id=123))

        assert "default_supplier_id" in errors(response)


class TestMrpValidation:
    def test_price_above_mrp_warns_in_warn_mode(self, client_a, tenant_a, units):
        response = client_a.post(API, json=body(tenant_a, units, mrp="100", selling_price="120"))

        assert response.status_code == 201
        # Both amounts are shown with two decimals, whatever was typed ("120" -> "120.00").
        assert response.json()["warnings"] == ["Selling price ₹120.00 is above the MRP ₹100.00."]

    def test_price_above_mrp_is_refused_in_block_mode(self, client_a, tenant_a, units, set_shop):
        set_shop(tenant_a, mrp_validation_mode=MrpValidationMode.BLOCK)

        response = client_a.post(API, json=body(tenant_a, units, mrp="100", selling_price="120"))

        assert response.status_code == 422
        assert "above the MRP" in errors(response)["selling_price"]

    @pytest.mark.parametrize("mrp", ["100", None])
    def test_price_at_or_below_mrp_or_without_mrp_is_fine_in_block_mode(
        self, client_a, tenant_a, units, set_shop, mrp
    ):
        set_shop(tenant_a, mrp_validation_mode=MrpValidationMode.BLOCK)

        response = client_a.post(API, json=body(tenant_a, units, mrp=mrp, selling_price="100"))

        assert response.status_code == 201 and response.json()["warnings"] == []

    def test_the_mode_of_one_shop_does_not_affect_another(
        self, client_a, client_b, tenant_a, tenant_b, units, set_shop
    ):
        set_shop(tenant_a, mrp_validation_mode=MrpValidationMode.BLOCK)

        assert client_a.post(API, json=body(tenant_a, units, mrp="1", selling_price="2")).status_code == 422
        assert client_b.post(API, json=body(tenant_b, units, mrp="1", selling_price="2")).status_code == 201


class TestReadAndSearch:
    @pytest.fixture
    def catalogue(self, client_a, tenant_a, units):
        client_a.post("/api/v1/categories", json={"name": "Dairy"})
        dairy = client_a.get("/api/v1/categories").json()
        dairy_id = next(c["id"] for c in dairy if c["name"] == "Dairy")
        create(
            client_a,
            tenant_a,
            units,
            sku="RICE-5KG",
            name="Basmati Rice 5kg",
            brand="India Gate",
            barcode="8901111111111",
        )
        create(
            client_a,
            tenant_a,
            units,
            sku="SUGAR-1KG",
            name="Sugar 1kg",
            brand="Madhur",
            barcode="8902222222222",
        )
        create(
            client_a,
            tenant_a,
            units,
            sku="MILK-1L",
            name="Milk 1L",
            brand="Amul",
            category_id=dairy_id,
            unit_id=units["L"],
        )
        create(client_a, tenant_a, units, sku="OLD-1", name="Old item")
        client_a.post(f"{API}/{client_a.get(API, params={'q': 'OLD'}).json()['items'][0]['id']}/deactivate")
        return {"dairy_id": dairy_id}

    def test_get_one(self, client_a, tenant_a, units):
        created = create(client_a, tenant_a, units)

        response = client_a.get(f"{API}/{created['id']}")

        assert response.status_code == 200 and response.json()["sku"] == "RICE-5KG"

    def test_unknown_product_is_404(self, client_a):
        assert client_a.get(f"{API}/9999").status_code == 404

    def test_default_list_shows_active_products_sorted_by_name(self, client_a, catalogue):
        data = client_a.get(API).json()

        assert data["total"] == 3
        assert [p["name"] for p in data["items"]] == ["Basmati Rice 5kg", "Milk 1L", "Sugar 1kg"]

    @pytest.mark.parametrize(
        ("query", "expected_skus"),
        [
            ({"q": "rice"}, ["RICE-5KG"]),  # name, any case
            ({"q": "SUGAR"}, ["SUGAR-1KG"]),  # sku
            ({"q": "amul"}, ["MILK-1L"]),  # brand
            ({"q": "8902222"}, ["SUGAR-1KG"]),  # part of a barcode
            ({"q": "1kg"}, ["SUGAR-1KG"]),  # substring of SKU and name
            ({"barcode": "8901111111111"}, ["RICE-5KG"]),  # exact barcode, as a scanner sends it
            ({"barcode": "890111"}, []),  # exact means exact
            ({"q": "nothing-matches"}, []),
            ({"q": "%"}, []),  # wildcards are literal text
        ],
    )
    def test_search(self, client_a, catalogue, query, expected_skus):
        skus = sorted(p["sku"] for p in client_a.get(API, params=query).json()["items"])

        assert skus == sorted(expected_skus)

    def test_filter_by_category_and_unit(self, client_a, catalogue, units):
        assert [
            p["sku"] for p in client_a.get(API, params={"category_id": catalogue["dairy_id"]}).json()["items"]
        ] == ["MILK-1L"]
        assert [p["sku"] for p in client_a.get(API, params={"unit_id": units["L"]}).json()["items"]] == [
            "MILK-1L"
        ]

    def test_status_filter(self, client_a, catalogue):
        assert [p["sku"] for p in client_a.get(API, params={"status": "inactive"}).json()["items"]] == [
            "OLD-1"
        ]
        assert client_a.get(API, params={"status": "all"}).json()["total"] == 4

    def test_pagination(self, client_a, catalogue):
        page = client_a.get(API, params={"limit": 2, "offset": 2}).json()

        assert (page["total"], page["limit"], page["offset"], len(page["items"])) == (3, 2, 2, 1)

    def test_bad_paging_parameters_are_refused(self, client_a):
        assert client_a.get(API, params={"limit": 0}).status_code == 422
        assert client_a.get(API, params={"limit": 500}).status_code == 422
        assert client_a.get(API, params={"offset": -1}).status_code == 422

    def test_list_shows_stock_from_the_ledger(self, client_a, tenant_a, units):
        create(client_a, tenant_a, units, sku="A", opening_stock="3", reorder_level="5")
        create(client_a, tenant_a, units, sku="B", opening_stock="50", reorder_level="5")
        create(client_a, tenant_a, units, sku="C")

        items = {p["sku"]: (p["current_stock"], p["stock_status"]) for p in client_a.get(API).json()["items"]}

        assert items == {
            "A": ("3.000", "LOW_STOCK"),
            "B": ("50.000", "IN_STOCK"),
            "C": ("0.000", "OUT_OF_STOCK"),
        }


class TestUpdate:
    def test_partial_update_changes_only_what_is_sent(self, client_a, tenant_a, units):
        created = create(client_a, tenant_a, units, brand="India Gate", mrp="300")

        response = client_a.patch(f"{API}/{created['id']}", json={"selling_price": "260.50"})

        product = response.json()["product"]
        assert response.status_code == 200
        assert product["selling_price"] == "260.50"
        assert (product["name"], product["brand"], product["mrp"]) == ("Rice 5kg", "India Gate", "300.00")

    def test_optional_fields_can_be_cleared_with_null(self, client_a, tenant_a, units):
        created = create(
            client_a, tenant_a, units, brand="X", mrp="300", purchase_price="200", barcode="123456"
        )

        product = client_a.patch(
            f"{API}/{created['id']}",
            json={"brand": None, "mrp": None, "purchase_price": None, "barcode": None},
        ).json()["product"]

        assert (product["brand"], product["mrp"], product["purchase_price"], product["barcode"]) == (
            None,
        ) * 4

    @pytest.mark.parametrize(
        "field", ["sku", "name", "category_id", "unit_id", "selling_price", "reorder_level"]
    )
    def test_required_fields_cannot_be_cleared(self, client_a, tenant_a, units, field):
        created = create(client_a, tenant_a, units)

        response = client_a.patch(f"{API}/{created['id']}", json={field: None})

        assert response.status_code == 422 and field in errors(response)

    def test_sku_and_barcode_uniqueness_on_update(self, client_a, tenant_a, units):
        first = create(client_a, tenant_a, units, sku="A", barcode="111111")
        second = create(client_a, tenant_a, units, sku="B", barcode="222222")

        assert client_a.patch(f"{API}/{second['id']}", json={"sku": "a"}).status_code == 409
        assert client_a.patch(f"{API}/{second['id']}", json={"barcode": "111111"}).status_code == 409
        # Keeping your own SKU/barcode is not a clash.
        assert (
            client_a.patch(
                f"{API}/{first['id']}", json={"sku": "A", "barcode": "111111", "name": "New"}
            ).status_code
            == 200
        )

    def test_mrp_rules_apply_to_updates_but_only_when_prices_change(
        self, client_a, tenant_a, units, set_shop
    ):
        created = create(client_a, tenant_a, units, mrp="100", selling_price="120")  # accepted in WARN mode
        set_shop(tenant_a, mrp_validation_mode=MrpValidationMode.BLOCK)

        assert (
            client_a.patch(f"{API}/{created['id']}", json={"name": "Renamed"}).status_code == 200
        )  # unrelated edit
        assert client_a.patch(f"{API}/{created['id']}", json={"selling_price": "130"}).status_code == 422
        assert client_a.patch(f"{API}/{created['id']}", json={"selling_price": "100"}).status_code == 200

    def test_warning_is_returned_on_update_in_warn_mode(self, client_a, tenant_a, units):
        created = create(client_a, tenant_a, units, mrp="100", selling_price="90")

        response = client_a.patch(f"{API}/{created['id']}", json={"selling_price": "150"})

        assert response.status_code == 200 and "above the MRP" in response.json()["warnings"][0]

    def test_unit_cannot_change_once_stock_exists_but_can_before(self, client_a, tenant_a, units):
        without = create(client_a, tenant_a, units, sku="A")
        with_stock = create(client_a, tenant_a, units, sku="B", opening_stock="5")

        assert client_a.patch(f"{API}/{without['id']}", json={"unit_id": units["kg"]}).status_code == 200
        response = client_a.patch(f"{API}/{with_stock['id']}", json={"unit_id": units["kg"]})
        assert response.status_code == 409 and "unit_id" in errors(response)

    def test_server_owned_fields_cannot_be_patched(self, client_a, tenant_a, units):
        created = create(client_a, tenant_a, units)

        for field in ("current_stock", "avg_cost", "is_active"):
            assert client_a.patch(f"{API}/{created['id']}", json={field: "5"}).status_code == 422

    def test_unknown_product_is_404(self, client_a):
        assert client_a.patch(f"{API}/9999", json={"name": "x"}).status_code == 404

    def test_a_no_op_update_changes_nothing_and_leaves_no_audit_trail(self, client_a, tenant_a, units, fresh):
        created = create(client_a, tenant_a, units)
        before = fresh(lambda s: s.scalar(select(func.count()).select_from(AuditLog)))

        client_a.patch(f"{API}/{created['id']}", json={"name": "Rice 5kg", "selling_price": "250"})

        assert fresh(lambda s: s.scalar(select(func.count()).select_from(AuditLog))) == before


class TestActivateDeactivate:
    def test_deactivate_hides_from_default_list_and_activate_restores(self, client_a, tenant_a, units):
        created = create(client_a, tenant_a, units)
        url = f"{API}/{created['id']}"

        assert client_a.post(f"{url}/deactivate").json()["is_active"] is False
        assert client_a.get(API).json()["total"] == 0
        assert client_a.get(API, params={"status": "inactive"}).json()["total"] == 1
        assert client_a.get(url).status_code == 200  # still reachable by id

        assert client_a.post(f"{url}/activate").json()["is_active"] is True
        assert client_a.get(API).json()["total"] == 1

    def test_repeating_the_call_is_harmless(self, client_a, tenant_a, units):
        created = create(client_a, tenant_a, units)
        url = f"{API}/{created['id']}/deactivate"

        assert client_a.post(url).status_code == 200
        assert client_a.post(url).status_code == 200

    def test_an_inactive_product_can_still_be_edited(self, client_a, tenant_a, units):
        created = create(client_a, tenant_a, units)
        client_a.post(f"{API}/{created['id']}/deactivate")

        assert client_a.patch(f"{API}/{created['id']}", json={"name": "Renamed"}).status_code == 200

    def test_inactive_products_cannot_receive_stock(self, client_a, tenant_a, units):
        created = create(client_a, tenant_a, units)
        client_a.post(f"{API}/{created['id']}/deactivate")

        response = client_a.post(
            "/api/v1/inventory/opening-stock", json={"product_id": created["id"], "quantity": "5"}
        )

        assert response.status_code == 409 and "inactive" in response.json()["detail"][0]["msg"]

    def test_deactivating_keeps_stock_and_history(self, client_a, tenant_a, units, fresh):
        created = create(client_a, tenant_a, units, opening_stock="20")

        product = client_a.post(f"{API}/{created['id']}/deactivate").json()

        assert product["current_stock"] == "20.000"
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(InventoryTransaction))) == 1

    def test_the_sku_of_an_inactive_product_stays_reserved(self, client_a, tenant_a, units):
        created = create(client_a, tenant_a, units)
        client_a.post(f"{API}/{created['id']}/deactivate")

        assert client_a.post(API, json=body(tenant_a, units)).status_code == 409

    def test_products_cannot_be_deleted(self, client_a, tenant_a, units):
        created = create(client_a, tenant_a, units)

        assert client_a.delete(f"{API}/{created['id']}").status_code == 405


class TestShopIsolation:
    def test_a_shop_cannot_read_change_or_deactivate_another_shops_product(
        self, client_a, client_b, tenant_a, units
    ):
        product = create(client_a, tenant_a, units)
        url = f"{API}/{product['id']}"

        assert client_b.get(url).status_code == 404
        assert client_b.patch(url, json={"name": "hacked"}).status_code == 404
        assert client_b.post(f"{url}/deactivate").status_code == 404
        assert client_b.post(f"{url}/activate").status_code == 404
        assert client_b.get(f"/api/v1/inventory/products/{product['id']}").status_code == 404
        assert client_a.get(url).json()["name"] == "Rice 5kg"  # untouched

    def test_lists_and_searches_only_show_own_products(self, client_a, client_b, tenant_a, tenant_b, units):
        create(client_a, tenant_a, units, sku="A-ONLY", name="Alpha", barcode="1111")
        create(client_b, tenant_b, units, sku="B-ONLY", name="Beta", barcode="2222")

        assert [p["sku"] for p in client_a.get(API).json()["items"]] == ["A-ONLY"]
        assert [p["sku"] for p in client_b.get(API).json()["items"]] == ["B-ONLY"]
        assert client_a.get(API, params={"q": "beta"}).json()["total"] == 0
        assert client_a.get(API, params={"barcode": "2222"}).json()["total"] == 0
        assert client_a.get(API, params={"status": "all"}).json()["total"] == 1

    def test_categories_are_per_shop_too(self, client_a, client_b, tenant_a):
        names_b = [c["name"] for c in client_b.get("/api/v1/categories").json()]

        assert names_b == ["Grocery"]  # Shop B's own category, not Shop A's row
        assert (
            client_b.patch(f"/api/v1/categories/{tenant_a.category.id}", json={"name": "x"}).status_code
            == 404
        )


class TestAudit:
    def test_create_update_and_deactivate_are_traceable(self, client_a, tenant_a, units, fresh):
        created = create(client_a, tenant_a, units, selling_price="250")
        client_a.patch(f"{API}/{created['id']}", json={"selling_price": "275", "name": "Rice 5kg Premium"})
        client_a.post(f"{API}/{created['id']}/deactivate")

        rows = fresh(
            lambda s: s.scalars(
                select(AuditLog).where(AuditLog.entity_type == "product").order_by(AuditLog.id)
            ).all()
        )

        assert [r.action for r in rows] == ["create", "update", "deactivate"]
        assert rows[1].before_json == {"selling_price": "250.00", "name": "Rice 5kg"}
        assert rows[1].after_json == {"selling_price": "275.00", "name": "Rice 5kg Premium"}
        assert rows[2].before_json == {"is_active": True} and rows[2].after_json == {"is_active": False}
        assert all(r.user_id == tenant_a.user.id and r.shop_id == tenant_a.shop.id for r in rows)

    def test_audit_rows_are_scoped_to_the_acting_shop(
        self, client_a, client_b, tenant_a, tenant_b, units, fresh
    ):
        create(client_a, tenant_a, units)
        create(client_b, tenant_b, units)

        shops = fresh(lambda s: sorted(s.scalars(select(AuditLog.shop_id)).all()))

        assert shops == sorted([tenant_a.shop.id, tenant_b.shop.id])


class TestCategoriesAndReferenceData:
    def test_units_are_listed(self, client_a):
        codes = [u["code"] for u in client_a.get("/api/v1/units").json()]

        assert codes == ["pcs", "kg", "g", "L", "ml", "pkt", "box", "doz"]

    def test_create_rename_and_deactivate_a_category(self, client_a):
        created = client_a.post("/api/v1/categories", json={"name": "  Dairy   Items "})
        assert created.status_code == 201 and created.json()["name"] == "Dairy Items"
        category_id = created.json()["id"]

        assert (
            client_a.patch(f"/api/v1/categories/{category_id}", json={"name": "Milk"}).json()["name"]
            == "Milk"
        )
        assert (
            client_a.patch(f"/api/v1/categories/{category_id}", json={"is_active": False}).json()["is_active"]
            is False
        )
        assert "Milk" not in [
            c["name"] for c in client_a.get("/api/v1/categories", params={"active": True}).json()
        ]

    def test_duplicate_category_names_are_refused_ignoring_case(self, client_a):
        response = client_a.post("/api/v1/categories", json={"name": "grocery"})

        assert response.status_code == 409 and "name" in errors(response)

    def test_a_blank_category_name_is_refused(self, client_a):
        assert client_a.post("/api/v1/categories", json={"name": "   "}).status_code == 422

    def test_inactive_categories_cannot_be_chosen_for_new_products(self, client_a, tenant_a, units):
        client_a.patch(f"/api/v1/categories/{tenant_a.category.id}", json={"is_active": False})

        response = client_a.post(API, json=body(tenant_a, units))

        assert response.status_code == 422 and "category_id" in errors(response)

    def test_shop_settings_are_readable(self, client_a):
        shop = client_a.get("/api/v1/shop").json()

        assert shop["allow_negative_stock"] is False
        assert shop["mrp_validation_mode"] == "WARN"
