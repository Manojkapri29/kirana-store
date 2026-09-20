"""Supplier API: CRUD, validation, duplicate warnings, search, activation, isolation, and the product link."""

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Supplier
from app.models.enums import UserRole
from tests.test_business_types import REQUESTED_TYPES

API = "/api/v1/suppliers"
PRODUCTS = "/api/v1/products"


def create(client, **fields):
    response = client.post(API, json={"name": "Sharma Traders", **fields})
    assert response.status_code == 201, response.text
    return response.json()["supplier"]


def errors(response) -> dict[str, str]:
    return {item["loc"][-1]: item["msg"] for item in response.json()["detail"]}


def make_product(client, tenant, units, sku="P-1", **overrides):
    body = {
        "sku": sku,
        "name": f"Product {sku}",
        "category_id": tenant.category.id,
        "unit_id": units["pcs"],
        "selling_price": "50",
        **overrides,
    }
    response = client.post(PRODUCTS, json=body)
    assert response.status_code == 201, response.text
    return response.json()["product"]


class TestCreate:
    def test_only_a_name_is_needed(self, client_a):
        response = client_a.post(API, json={"name": "Sharma Traders"})

        assert response.status_code == 201
        supplier = response.json()["supplier"]
        assert supplier["name"] == "Sharma Traders" and supplier["is_active"] is True
        assert supplier["product_count"] == 0
        assert all(
            supplier[k] is None for k in ("phone", "alternate_phone", "email", "address", "gstin", "notes")
        )
        assert response.json()["warnings"] == []

    def test_all_fields_are_stored_in_a_consistent_form(self, client_a, tenant_a):
        supplier = create(
            client_a,
            name="  Sharma   & Sons  (Wholesale) ",
            phone="+91 98765-43210",
            alternate_phone="(0141) 234 5678",
            email="  Orders@Sharma-Sons.COM ",
            address="12, Main Bazaar, Agra",
            gstin="27aapfu0939f1zv",
            notes="Delivers on Tuesdays. Credit 15 days.",
        )

        assert supplier["name"] == "Sharma & Sons (Wholesale)"
        assert (supplier["phone"], supplier["alternate_phone"]) == ("+919876543210", "01412345678")
        assert supplier["email"] == "orders@sharma-sons.com"
        assert supplier["gstin"] == "27AAPFU0939F1ZV"
        assert supplier["address"] == "12, Main Bazaar, Agra" and supplier["notes"].startswith("Delivers")

    def test_blank_optional_fields_are_stored_as_not_given(self, client_a):
        supplier = create(client_a, phone="  ", email="", gstin="", address=" ", notes="")

        assert all(supplier[k] is None for k in ("phone", "email", "gstin", "address", "notes"))

    @pytest.mark.parametrize(
        "name",
        [
            "Sharma & Sons (Wholesale) — Agra",
            "शर्मा ट्रेडर्स",
            "O'Brien's 24x7",
            "M/S. Gupta-Brothers, Delhi",
            "A",
            "1st Choice #2",
            "x" * 200,
        ],
    )
    def test_legitimate_names_are_not_over_restricted(self, client_a, name):
        assert create(client_a, name=name)["name"] == name

    def test_name_is_required(self, client_a):
        assert errors(client_a.post(API, json={})) == {"name": "Field required"}
        for blank in ("", "   "):
            response = client_a.post(API, json={"name": blank})
            assert (response.status_code, errors(response)) == (422, {"name": "Enter the supplier's name."})

    def test_every_invalid_field_is_reported_at_once(self, client_a):
        response = client_a.post(API, json={"name": "  ", "phone": "12", "email": "nope", "gstin": "bad"})

        assert response.status_code == 422
        assert set(errors(response)) == {"name", "phone", "email", "gstin"}

    def test_too_long_values_are_refused(self, client_a):
        assert "name" in errors(client_a.post(API, json={"name": "x" * 201}))
        assert "notes" in errors(client_a.post(API, json={"name": "N", "notes": "x" * 2001}))

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("phone", "abc"), ("phone", "12345"), ("phone", "1" * 16),
            ("alternate_phone", "98-abc-3210"),
            ("email", "not-an-email"), ("email", "a@b"),
            ("gstin", "123"), ("gstin", "XXAAPFU0939F1ZV"),
        ],
    )  # fmt: skip
    def test_invalid_contact_details_are_refused_naming_the_field(self, client_a, field, value):
        response = client_a.post(API, json={"name": "X", field: value})

        assert response.status_code == 422 and field in errors(response)

    def test_a_short_local_phone_and_an_unverified_check_character_are_accepted(self, client_a):
        supplier = create(client_a, phone="123456", gstin="27AAPFU0939F1Z0")

        assert (supplier["phone"], supplier["gstin"]) == ("123456", "27AAPFU0939F1Z0")

    @pytest.mark.parametrize("field", ["is_active", "id", "shop_id", "product_count", "created_at"])
    def test_server_owned_fields_cannot_be_set(self, client_a, field):
        response = client_a.post(API, json={"name": "X", field: "1"})

        assert response.status_code == 422 and field in errors(response)


class TestDuplicatesAreWarningsNotErrors:
    """Real suppliers can share a name or a phone, so a likely duplicate warns and still saves."""

    def test_same_name_ignoring_case(self, client_a):
        create(client_a, name="Sharma Traders")

        response = client_a.post(API, json={"name": "SHARMA  traders"})

        assert response.status_code == 201
        assert "also named 'Sharma Traders'" in response.json()["warnings"][0]
        assert client_a.get(API).json()["total"] == 2

    def test_same_phone_even_when_typed_differently_or_as_the_alternate_number(self, client_a):
        create(client_a, name="First", phone="9876543210")

        as_phone = client_a.post(API, json={"name": "Second", "phone": "98765 43210"})
        as_alternate = client_a.post(
            API, json={"name": "Third", "alternate_phone": "+919876543210", "phone": "1112223334"}
        )
        other_side = client_a.post(
            API, json={"name": "Fourth", "phone": "5556667778", "alternate_phone": "9876543210"}
        )

        assert (
            "phone number" in as_phone.json()["warnings"][0] and "'First'" in as_phone.json()["warnings"][0]
        )
        assert (
            as_alternate.json()["warnings"] == []
        )  # +91... is a different stored string: not claimed as a match
        assert "phone number" in other_side.json()["warnings"][0]

    def test_same_gstin(self, client_a):
        create(client_a, name="First", gstin="27AAPFU0939F1ZV")

        response = client_a.post(API, json={"name": "Branch office", "gstin": "27aapfu0939f1zv"})

        assert response.status_code == 201 and "GSTIN 27AAPFU0939F1ZV" in response.json()["warnings"][0]

    def test_no_warning_for_a_different_supplier(self, client_a):
        create(client_a, name="First", phone="9876543210", gstin="27AAPFU0939F1ZV")

        assert client_a.post(API, json={"name": "Second", "phone": "9000000000"}).json()["warnings"] == []

    def test_another_shops_suppliers_are_never_mentioned(self, client_a, client_b):
        create(client_b, name="Sharma Traders", phone="9876543210", gstin="27AAPFU0939F1ZV")

        response = client_a.post(
            API, json={"name": "Sharma Traders", "phone": "9876543210", "gstin": "27AAPFU0939F1ZV"}
        )

        assert response.status_code == 201 and response.json()["warnings"] == []

    def test_on_update_only_changed_details_are_rechecked(self, client_a):
        create(client_a, name="Twin", phone="9876543210")
        second = create(client_a, name="Twin", phone="9876543210")  # a knowing duplicate

        untouched = client_a.patch(f"{API}/{second['id']}", json={"notes": "Just a note"})
        renamed = client_a.patch(f"{API}/{second['id']}", json={"name": "Twin"})  # unchanged: not a change
        moved = client_a.patch(f"{API}/{second['id']}", json={"phone": "9000000001"})
        create(client_a, name="Other", phone="9111111111")
        collided = client_a.patch(f"{API}/{second['id']}", json={"phone": "9111111111"})

        assert (
            untouched.json()["warnings"] == []
            and renamed.json()["warnings"] == []
            and moved.json()["warnings"] == []
        )
        assert "phone number" in collided.json()["warnings"][0]

    def test_a_supplier_is_never_a_duplicate_of_itself(self, client_a):
        supplier = create(client_a, name="Solo", phone="9876543210", gstin="27AAPFU0939F1ZV")

        response = client_a.patch(f"{API}/{supplier['id']}", json={"name": "Solo", "phone": "98765 43210"})

        assert response.status_code == 200 and response.json()["warnings"] == []


class TestReadListAndSearch:
    @pytest.fixture
    def suppliers(self, client_a):
        return {
            "sharma": create(
                client_a,
                name="Sharma Traders",
                phone="98765 43210",
                email="orders@sharma.in",
                gstin="27AAPFU0939F1ZV",
            ),
            "gupta": create(
                client_a, name="Gupta Brothers", phone="+91 91234 56789", alternate_phone="0141 234 5678"
            ),
            "verma": create(client_a, name="Verma (Wholesale)", email="verma@example.com"),
            "old": create(client_a, name="Old Supplier"),
        }

    def test_get_one(self, client_a, suppliers):
        response = client_a.get(f"{API}/{suppliers['sharma']['id']}")

        assert response.status_code == 200 and response.json()["email"] == "orders@sharma.in"

    def test_unknown_supplier_is_404(self, client_a):
        assert client_a.get(f"{API}/9999").status_code == 404

    def test_list_is_active_only_sorted_by_name_with_a_total(self, client_a, suppliers):
        client_a.post(f"{API}/{suppliers['old']['id']}/deactivate")

        data = client_a.get(API).json()

        assert data["total"] == 3 and [s["name"] for s in data["items"]] == [
            "Gupta Brothers",
            "Sharma Traders",
            "Verma (Wholesale)",
        ]

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("sharma", ["Sharma Traders"]),  # name, any case
            ("BROTHERS", ["Gupta Brothers"]),
            ("wholesale", ["Verma (Wholesale)"]),
            ("orders@", ["Sharma Traders"]),  # email
            ("example.com", ["Verma (Wholesale)"]),
            ("27aapfu", ["Sharma Traders"]),  # GSTIN, any case
            ("98765 43210", ["Sharma Traders"]),  # phone typed with a space
            ("9876543210", ["Sharma Traders"]),
            ("43210", ["Sharma Traders"]),  # part of a phone number
            ("+91 91234", ["Gupta Brothers"]),
            ("0141-234", ["Gupta Brothers"]),  # the alternate number
            ("%", []),  # wildcards are plain text
            ("no such supplier", []),
        ],
    )
    def test_search(self, client_a, suppliers, query, expected):
        names = [
            s["name"]
            for s in client_a.get(API, params={"q": query, "status": "all"}).json()["items"]
            if s["name"] != "Old Supplier"
        ]

        assert names == sorted(expected)

    def test_very_short_digit_strings_do_not_match_every_phone(self, client_a, suppliers):
        assert client_a.get(API, params={"q": "98"}).json()["total"] == 0

    def test_status_filter(self, client_a, suppliers):
        client_a.post(f"{API}/{suppliers['old']['id']}/deactivate")

        assert [s["name"] for s in client_a.get(API, params={"status": "inactive"}).json()["items"]] == [
            "Old Supplier"
        ]
        assert client_a.get(API, params={"status": "all"}).json()["total"] == 4

    def test_paging(self, client_a, suppliers):
        page = client_a.get(API, params={"limit": 2, "offset": 2, "status": "all"}).json()

        assert (page["total"], page["limit"], page["offset"], len(page["items"])) == (4, 2, 2, 2)
        assert client_a.get(API, params={"limit": 0}).status_code == 422
        assert client_a.get(API, params={"limit": 500}).status_code == 422
        assert client_a.get(API, params={"offset": -1}).status_code == 422

    def test_search_text_length_is_capped(self, client_a):
        assert client_a.get(API, params={"q": "x" * 100}).status_code == 200
        assert client_a.get(API, params={"q": "x" * 101}).status_code == 422

    def test_options_lists_active_suppliers_with_only_id_and_name(self, client_a, suppliers):
        client_a.post(f"{API}/{suppliers['old']['id']}/deactivate")

        options = client_a.get(f"{API}/options").json()

        assert [o["name"] for o in options] == ["Gupta Brothers", "Sharma Traders", "Verma (Wholesale)"]
        assert set(options[0]) == {"id", "name"}


class TestUpdate:
    def test_partial_update_changes_only_what_is_sent(self, client_a):
        supplier = create(client_a, phone="9876543210", email="a@b.co", address="Old address")

        response = client_a.patch(f"{API}/{supplier['id']}", json={"address": "New address"})

        updated = response.json()["supplier"]
        assert response.status_code == 200 and updated["address"] == "New address"
        assert (updated["phone"], updated["email"], updated["name"]) == (
            "9876543210",
            "a@b.co",
            "Sharma Traders",
        )

    def test_optional_details_can_be_cleared_with_null_or_empty_text(self, client_a):
        supplier = create(
            client_a, phone="9876543210", email="a@b.co", gstin="27AAPFU0939F1ZV", notes="n", address="a"
        )

        cleared = client_a.patch(
            f"{API}/{supplier['id']}",
            json={"phone": None, "email": "", "gstin": None, "notes": "  ", "address": None},
        ).json()["supplier"]

        assert all(cleared[k] is None for k in ("phone", "email", "gstin", "notes", "address"))

    def test_new_values_are_validated_and_normalized_like_on_create(self, client_a):
        supplier = create(client_a)

        ok = client_a.patch(
            f"{API}/{supplier['id']}",
            json={"phone": "98765 43210", "email": "X@Y.IN", "gstin": "27aapfu0939f1zv"},
        ).json()["supplier"]
        bad = client_a.patch(f"{API}/{supplier['id']}", json={"phone": "12", "email": "nope"})

        assert (ok["phone"], ok["email"], ok["gstin"]) == ("9876543210", "x@y.in", "27AAPFU0939F1ZV")
        assert bad.status_code == 422 and {"phone", "email"} <= set(errors(bad))
        assert (
            client_a.get(f"{API}/{supplier['id']}").json()["phone"] == "9876543210"
        )  # the bad update changed nothing

    def test_name_cannot_be_cleared(self, client_a):
        supplier = create(client_a)

        for value in (None, "", "  "):
            response = client_a.patch(f"{API}/{supplier['id']}", json={"name": value})
            assert response.status_code == 422 and "name" in errors(response)

    @pytest.mark.parametrize("field", ["is_active", "id", "shop_id", "product_count"])
    def test_server_owned_fields_cannot_be_patched(self, client_a, field):
        assert client_a.patch(f"{API}/{create(client_a)['id']}", json={field: "x"}).status_code == 422

    def test_unknown_supplier_is_404(self, client_a):
        assert client_a.patch(f"{API}/9999", json={"name": "x"}).status_code == 404

    def test_a_no_op_update_leaves_no_audit_trail(self, client_a, fresh):
        supplier = create(client_a, phone="9876543210")
        before = fresh(lambda s: s.scalar(select(func.count()).select_from(AuditLog)))

        client_a.patch(f"{API}/{supplier['id']}", json={"name": "Sharma Traders", "phone": "98765 43210"})

        assert fresh(lambda s: s.scalar(select(func.count()).select_from(AuditLog))) == before


class TestActivateDeactivate:
    def test_deactivate_hides_from_default_lists_and_activate_restores(self, client_a):
        supplier = create(client_a)
        url = f"{API}/{supplier['id']}"

        assert client_a.post(f"{url}/deactivate").json()["is_active"] is False
        assert client_a.get(API).json()["total"] == 0 and client_a.get(f"{API}/options").json() == []
        assert client_a.get(API, params={"status": "inactive"}).json()["total"] == 1
        assert client_a.get(url).status_code == 200  # still reachable by id

        assert client_a.post(f"{url}/activate").json()["is_active"] is True
        assert client_a.get(API).json()["total"] == 1

    def test_repeating_a_call_is_harmless(self, client_a):
        url = f"{API}/{create(client_a)['id']}/deactivate"

        assert client_a.post(url).status_code == 200 and client_a.post(url).status_code == 200

    def test_an_inactive_supplier_can_still_be_edited(self, client_a):
        supplier = create(client_a)
        client_a.post(f"{API}/{supplier['id']}/deactivate")

        assert client_a.patch(f"{API}/{supplier['id']}", json={"notes": "Closed down"}).status_code == 200

    def test_suppliers_cannot_be_deleted(self, client_a):
        assert client_a.delete(f"{API}/{create(client_a)['id']}").status_code == 405

    def test_deactivating_does_not_touch_the_products_that_use_it(self, client_a, tenant_a, units):
        supplier = create(client_a)
        product = make_product(client_a, tenant_a, units, default_supplier_id=supplier["id"])

        client_a.post(f"{API}/{supplier['id']}/deactivate")

        still = client_a.get(f"{PRODUCTS}/{product['id']}").json()
        assert (still["default_supplier_id"], still["default_supplier_name"]) == (
            supplier["id"],
            "Sharma Traders",
        )


class TestProductDefaultSupplier:
    def test_a_product_can_have_a_default_supplier(self, client_a, tenant_a, units):
        supplier = create(client_a, name="Verma Wholesale")

        product = make_product(client_a, tenant_a, units, default_supplier_id=supplier["id"])

        assert (product["default_supplier_id"], product["default_supplier_name"]) == (
            supplier["id"],
            "Verma Wholesale",
        )

    def test_a_supplier_is_optional(self, client_a, tenant_a, units):
        product = make_product(client_a, tenant_a, units)

        assert product["default_supplier_id"] is None and product["default_supplier_name"] is None

    def test_one_supplier_can_supply_many_products(self, client_a, tenant_a, units):
        supplier = create(client_a)
        for sku in ("A", "B", "C"):
            make_product(client_a, tenant_a, units, sku=sku, default_supplier_id=supplier["id"])
        make_product(client_a, tenant_a, units, sku="NONE")  # a product without a supplier

        listing = client_a.get(PRODUCTS, params={"supplier_id": supplier["id"]}).json()

        assert sorted(p["sku"] for p in listing["items"]) == ["A", "B", "C"] and listing["total"] == 3
        assert client_a.get(f"{API}/{supplier['id']}").json()["product_count"] == 3
        assert client_a.get(API).json()["items"][0]["product_count"] == 3

    def test_the_product_count_includes_inactive_products(self, client_a, tenant_a, units):
        supplier = create(client_a)
        product = make_product(client_a, tenant_a, units, default_supplier_id=supplier["id"])
        client_a.post(f"{PRODUCTS}/{product['id']}/deactivate")

        assert client_a.get(f"{API}/{supplier['id']}").json()["product_count"] == 1
        assert (
            client_a.get(PRODUCTS, params={"supplier_id": supplier["id"]}).json()["total"] == 0
        )  # default list: active
        assert (
            client_a.get(PRODUCTS, params={"supplier_id": supplier["id"], "status": "all"}).json()["total"]
            == 1
        )

    def test_the_supplier_can_be_changed_and_cleared(self, client_a, tenant_a, units):
        first, second = create(client_a, name="First"), create(client_a, name="Second")
        product = make_product(client_a, tenant_a, units, default_supplier_id=first["id"])
        url = f"{PRODUCTS}/{product['id']}"

        switched = client_a.patch(url, json={"default_supplier_id": second["id"]}).json()["product"]
        cleared = client_a.patch(url, json={"default_supplier_id": None}).json()["product"]

        assert switched["default_supplier_name"] == "Second"
        assert (cleared["default_supplier_id"], cleared["default_supplier_name"]) == (None, None)
        assert client_a.get(f"{API}/{first['id']}").json()["product_count"] == 0

    def test_an_unknown_supplier_is_refused(self, client_a, tenant_a, units):
        response = client_a.post(
            PRODUCTS,
            json={
                "sku": "X",
                "name": "X",
                "category_id": tenant_a.category.id,
                "unit_id": units["pcs"],
                "selling_price": "1",
                "default_supplier_id": 9999,
            },
        )

        assert response.status_code == 422 and errors(response) == {
            "default_supplier_id": "Choose an existing supplier."
        }

    def test_an_inactive_supplier_cannot_be_newly_chosen(self, client_a, tenant_a, units):
        supplier = create(client_a)
        client_a.post(f"{API}/{supplier['id']}/deactivate")
        product = make_product(client_a, tenant_a, units, sku="P")

        on_create = client_a.post(
            PRODUCTS,
            json={
                "sku": "N",
                "name": "N",
                "category_id": tenant_a.category.id,
                "unit_id": units["pcs"],
                "selling_price": "1",
                "default_supplier_id": supplier["id"],
            },
        )
        on_update = client_a.patch(
            f"{PRODUCTS}/{product['id']}", json={"default_supplier_id": supplier["id"]}
        )

        assert on_create.status_code == on_update.status_code == 422
        assert errors(on_update) == {"default_supplier_id": "This supplier is inactive."}

    def test_a_product_keeps_working_when_its_supplier_becomes_inactive(self, client_a, tenant_a, units):
        supplier = create(client_a)
        product = make_product(client_a, tenant_a, units, default_supplier_id=supplier["id"])
        client_a.post(f"{API}/{supplier['id']}/deactivate")
        url = f"{PRODUCTS}/{product['id']}"

        renamed = client_a.patch(url, json={"name": "Renamed"})
        resent = client_a.patch(
            url, json={"default_supplier_id": supplier["id"], "selling_price": "60"}
        )  # same link, kept
        cleared = client_a.patch(url, json={"default_supplier_id": None})

        assert renamed.status_code == resent.status_code == cleared.status_code == 200

    def test_the_products_export_shows_the_default_supplier(self, client_a, tenant_a, units):
        supplier = create(client_a, name="Verma Wholesale")
        make_product(client_a, tenant_a, units, sku="WITH", default_supplier_id=supplier["id"])
        make_product(client_a, tenant_a, units, sku="WITHOUT")

        import csv
        import io

        rows = list(
            csv.reader(io.StringIO(client_a.get("/api/v1/exports/products").content.decode("utf-8-sig")))
        )
        column = rows[0].index("Default Supplier")

        assert {r[0]: r[column] for r in rows[1:]} == {"WITH": "Verma Wholesale", "WITHOUT": ""}


class TestShopIsolation:
    def test_a_shop_cannot_read_change_or_deactivate_another_shops_supplier(self, client_a, client_b):
        supplier = create(client_a, name="Private Supplier")
        url = f"{API}/{supplier['id']}"

        assert client_b.get(url).status_code == 404
        assert client_b.patch(url, json={"name": "hacked"}).status_code == 404
        assert client_b.post(f"{url}/deactivate").status_code == 404
        assert client_b.post(f"{url}/activate").status_code == 404
        assert client_a.get(url).json() == {**supplier, "product_count": 0}  # untouched

    def test_lists_search_and_options_show_only_own_suppliers(self, client_a, client_b):
        create(client_a, name="Alpha Traders", phone="9876543210", email="a@alpha.in")
        create(client_b, name="Beta Traders", phone="9123456789", email="b@beta.in")

        assert [s["name"] for s in client_a.get(API).json()["items"]] == ["Alpha Traders"]
        assert [s["name"] for s in client_b.get(API).json()["items"]] == ["Beta Traders"]
        for query in ("beta", "9123456789", "b@beta.in"):
            assert client_a.get(API, params={"q": query, "status": "all"}).json()["total"] == 0
        assert [o["name"] for o in client_a.get(f"{API}/options").json()] == ["Alpha Traders"]

    def test_a_product_cannot_use_another_shops_supplier(self, client_a, client_b, tenant_a, units):
        theirs = create(client_b, name="Theirs")
        product = make_product(client_a, tenant_a, units)

        created = client_a.post(
            PRODUCTS,
            json={
                "sku": "N",
                "name": "N",
                "category_id": tenant_a.category.id,
                "unit_id": units["pcs"],
                "selling_price": "1",
                "default_supplier_id": theirs["id"],
            },
        )
        updated = client_a.patch(f"{PRODUCTS}/{product['id']}", json={"default_supplier_id": theirs["id"]})

        assert created.status_code == updated.status_code == 422
        assert errors(updated) == {
            "default_supplier_id": "Choose an existing supplier."
        }  # same answer as "unknown"

    def test_a_shops_product_filter_cannot_reach_another_shops_supplier_products(
        self, client_a, client_b, tenant_b, units
    ):
        theirs = create(client_b, name="Theirs")
        make_product(client_b, tenant_b, units, default_supplier_id=theirs["id"])

        assert (
            client_a.get(PRODUCTS, params={"supplier_id": theirs["id"], "status": "all"}).json()["total"] == 0
        )

    def test_each_shop_can_have_a_supplier_with_the_same_name_and_details(self, client_a, client_b):
        create(client_a, name="Sharma Traders", phone="9876543210", gstin="27AAPFU0939F1ZV")
        create(client_b, name="Sharma Traders", phone="9876543210", gstin="27AAPFU0939F1ZV")

    def test_audit_rows_belong_to_the_acting_shop(self, client_a, client_b, tenant_a, tenant_b, fresh):
        create(client_a)
        create(client_b)

        rows = fresh(
            lambda s: s.execute(
                select(AuditLog.shop_id, AuditLog.user_id).where(AuditLog.entity_type == "supplier")
            ).all()
        )

        assert sorted(rows) == sorted(
            [(tenant_a.shop.id, tenant_a.user.id), (tenant_b.shop.id, tenant_b.user.id)]
        )


class TestAudit:
    def test_create_update_and_deactivate_are_traceable(self, client_a, fresh):
        supplier = create(client_a, phone="9876543210")
        client_a.patch(f"{API}/{supplier['id']}", json={"phone": "9000000000", "notes": "Prefers cash"})
        client_a.post(f"{API}/{supplier['id']}/deactivate")

        rows = fresh(
            lambda s: s.scalars(
                select(AuditLog).where(AuditLog.entity_type == "supplier").order_by(AuditLog.id)
            ).all()
        )

        assert [r.action for r in rows] == ["create", "update", "deactivate"]
        assert rows[0].after_json["name"] == "Sharma Traders"
        assert rows[1].before_json == {"phone": "9876543210", "notes": None}
        assert rows[1].after_json == {"phone": "9000000000", "notes": "Prefers cash"}
        assert (rows[2].before_json, rows[2].after_json) == ({"is_active": True}, {"is_active": False})


class TestEveryBusinessType:
    """Suppliers are generic: identical behaviour whatever kind of business the shop is."""

    @pytest.mark.parametrize("business_type", REQUESTED_TYPES)
    def test_full_supplier_flow(self, make_client, tenant_of, units, business_type):
        tenant = tenant_of(business_type)
        client = make_client(tenant)

        supplier = create(
            client, name="Any Supplier", phone="9876543210", email="s@example.com", gstin="27AAPFU0939F1ZV"
        )
        product = make_product(client, tenant, units, default_supplier_id=supplier["id"])
        client.patch(f"{API}/{supplier['id']}", json={"notes": "ok"})

        assert client.get(f"{API}/{supplier['id']}").json()["product_count"] == 1
        assert client.get(API, params={"q": "any"}).json()["total"] == 1
        assert client.get(f"{PRODUCTS}/{product['id']}").json()["default_supplier_name"] == "Any Supplier"
        assert client.post(f"{API}/{supplier['id']}/deactivate").status_code == 200

    def test_suppliers_of_shops_with_different_types_are_isolated(self, make_client, tenant_of):
        bakery, garments = tenant_of("BAKERY"), tenant_of("GARMENTS")
        theirs = create(make_client(bakery), name="Flour Mill")

        assert make_client(garments).get(f"{API}/{theirs['id']}").status_code == 404


class TestAccess:
    def test_staff_can_manage_suppliers_like_products(self, make_client, tenant_a):
        staff = make_client(tenant_a, role=UserRole.STAFF)

        assert staff.post(API, json={"name": "By Staff"}).status_code == 201
        assert staff.get(API).status_code == 200


def test_the_database_holds_exactly_what_the_api_returned(client_a, tenant_a, fresh):
    created = create(
        client_a, name="Stored Supplier", phone="98765 43210", email="S@X.IN", gstin="27aapfu0939f1zv"
    )

    row = fresh(lambda s: s.get(Supplier, created["id"]))

    assert (row.shop_id, row.name, row.phone, row.email, row.gstin) == (
        tenant_a.shop.id, "Stored Supplier", "9876543210", "s@x.in", "27AAPFU0939F1ZV",
    )  # fmt: skip
