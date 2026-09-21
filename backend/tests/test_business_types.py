"""Business types: the platform is generic. Grocery/Kirana is one supported type among many.

Core rule under test: a business type provides DEFAULTS and SUGGESTIONS. It never restricts what a shop can
sell, which units it can use, or how stock is counted. There is one product model and one inventory ledger.
"""

import re
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.core.config import BACKEND_DIR
from app.models import AuditLog, Product, Shop
from app.models.enums import UserRole
from app.services import business_type_service as bts
from tests import factories
from tests.conftest import assert_rejected, assert_sql_rejected

# The identifiers requested for the platform, plus MEAT_FOOD (the "Meat/Food shop" in the same request).
REQUESTED_TYPES = [
    "GROCERY", "SWEET_SHOP", "BAKERY", "FRUIT", "VEGETABLE", "DAIRY", "GENERAL_STORE", "GARMENTS",
    "FOOTWEAR", "COSMETICS", "ELECTRONICS", "HARDWARE", "STATIONERY", "OTHER", "MEAT_FOOD",
]  # fmt: skip
UNIT_CODES = ["pcs", "kg", "g", "L", "ml", "pkt", "box", "doz", "m", "pair", "btl", "tray"]
PRODUCTS = "/api/v1/products"
INV = "/api/v1/inventory"


def product_body(tenant, units, unit: str, **overrides) -> dict:
    body = {
        "sku": "ITEM-1",
        "name": "Item",
        "category_id": tenant.category.id,
        "unit_id": units[unit],
        "selling_price": "100",
    }
    body.update(overrides)
    return body


class TestBusinessTypeReferenceData:
    def test_every_requested_type_exists(self, session):
        codes = set(session.scalars(text("SELECT code FROM business_types")))

        assert set(REQUESTED_TYPES) <= codes
        assert codes == set(REQUESTED_TYPES)  # nothing unexpected either

    def test_types_have_names_and_a_stable_order_with_other_last(self, client_a):
        types = client_a.get("/api/v1/business-types").json()

        assert [t["code"] for t in types][0] == "GROCERY" and [t["code"] for t in types][-1] == "OTHER"
        assert all(t["name"].strip() for t in types)
        assert next(t["name"] for t in types if t["code"] == "GROCERY") == "Grocery / Kirana"
        assert next(t["name"] for t in types if t["code"] == "SWEET_SHOP") == "Sweet Shop / Halwai"

    def test_a_shop_must_have_a_valid_business_type(self, session):
        assert_rejected(
            session,
            Shop(name="X", business_type="NOT_A_TYPE", phone="1", address="x", mrp_validation_mode="WARN"),
            match="FOREIGN KEY",
        )
        assert_sql_rejected(
            session,
            "INSERT INTO shops (name, phone, address, mrp_validation_mode, created_at, updated_at)"
            " VALUES ('X', '1', 'x', 'WARN', '2026-01-01', '2026-01-01')",
            match="NOT NULL",
        )  # no business type at all: also refused, there is no silent default

    def test_a_new_kind_of_business_needs_only_a_data_row_not_code(self, session, tenant_of):
        """Extensibility: insert a type and shops can use it immediately, with no template and no crash."""
        session.execute(
            text("INSERT INTO business_types (code, name, sort_order) VALUES ('PET_SHOP', 'Pet Shop', 500)")
        )
        session.commit()

        shop = factories.make_shop(session, "Paws", business_type="PET_SHOP")
        session.commit()

        assert shop.business_type == "PET_SHOP"
        assert bts.get_template("PET_SHOP") == bts.NO_TEMPLATE  # nothing suggested, nothing broken


class TestTwoShopsDifferentTypes:
    def test_each_shop_reports_its_own_type(self, make_client, tenant_of):
        bakery, vegetables = tenant_of("BAKERY"), tenant_of("VEGETABLE")

        shop_a = make_client(bakery).get("/api/v1/shop").json()
        shop_b = make_client(vegetables).get("/api/v1/shop").json()

        assert (shop_a["business_type"], shop_a["business_type_name"]) == ("BAKERY", "Bakery")
        assert (shop_b["business_type"], shop_b["business_type_name"]) == ("VEGETABLE", "Vegetable Vendor")

    def test_same_sku_and_barcode_in_shops_of_different_types(self, make_client, tenant_of, units):
        shops = [tenant_of("GARMENTS"), tenant_of("ELECTRONICS")]

        for tenant in shops:
            response = make_client(tenant).post(
                PRODUCTS, json=product_body(tenant, units, "pcs", sku="X-1", barcode="8900000000001")
            )
            assert response.status_code == 201

    def test_changing_one_shops_type_leaves_the_other_alone(self, make_client, tenant_of):
        first, second = tenant_of("GROCERY"), tenant_of("GROCERY")

        assert (
            make_client(first).patch("/api/v1/shop", json={"business_type": "DAIRY"}).json()["business_type"]
            == "DAIRY"
        )

        assert make_client(second).get("/api/v1/shop").json()["business_type"] == "GROCERY"


class TestChangingTheBusinessType:
    def test_owner_can_change_it_and_it_is_audited(self, client_a, tenant_a, fresh):
        response = client_a.patch("/api/v1/shop", json={"business_type": "sweet_shop"})  # case is forgiven

        assert response.status_code == 200 and response.json()["business_type"] == "SWEET_SHOP"
        entry = fresh(lambda s: s.scalars(select(AuditLog).where(AuditLog.entity_type == "shop")).one())
        assert (entry.before_json, entry.after_json) == (
            {"business_type": "GROCERY"},
            {"business_type": "SWEET_SHOP"},
        )
        assert entry.shop_id == tenant_a.shop.id

    def test_unknown_type_is_refused_with_the_field_named(self, client_a):
        response = client_a.patch("/api/v1/shop", json={"business_type": "SPACESHIP"})

        assert response.status_code == 422 and response.json()["detail"][0]["loc"] == [
            "body",
            "business_type",
        ]
        assert client_a.get("/api/v1/shop").json()["business_type"] == "GROCERY"

    def test_an_inactive_type_cannot_be_chosen(self, client_a, session):
        session.execute(text("UPDATE business_types SET is_active = 0 WHERE code = 'HARDWARE'"))
        session.commit()

        assert client_a.patch("/api/v1/shop", json={"business_type": "HARDWARE"}).status_code == 422
        assert "HARDWARE" not in [t["code"] for t in client_a.get("/api/v1/business-types").json()]

    @pytest.mark.parametrize(
        "field", ["allow_negative_stock", "mrp_validation_mode", "name", "timezone", "id"]
    )
    def test_other_settings_cannot_be_changed_through_this_endpoint(self, client_a, field):
        assert client_a.patch("/api/v1/shop", json={field: "x"}).status_code == 422

    def test_an_empty_update_changes_nothing(self, client_a, fresh):
        assert client_a.patch("/api/v1/shop", json={}).json()["business_type"] == "GROCERY"
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(AuditLog))) == 0

    def test_only_the_owner_can_change_it(self, make_client, tenant_a):
        staff = make_client(tenant_a, role=UserRole.STAFF)

        assert staff.patch("/api/v1/shop", json={"business_type": "BAKERY"}).status_code == 403
        assert staff.get("/api/v1/shop").json()["business_type"] == "GROCERY"  # staff can still read it

    def test_changing_the_type_does_not_touch_products_units_or_stock(self, client_a, tenant_a, units):
        created = client_a.post(
            PRODUCTS, json=product_body(tenant_a, units, "kg", opening_stock="12.5")
        ).json()["product"]

        client_a.patch("/api/v1/shop", json={"business_type": "GARMENTS"})

        after = client_a.get(f"{PRODUCTS}/{created['id']}").json()
        assert (after["unit_code"], after["current_stock"], after["category_name"]) == (
            "kg",
            "12.500",
            "Grocery",
        )


class TestProductsAndInventoryIgnoreBusinessType:
    """The same product model and the same ledger serve every business, in every unit, with fractions."""

    # (business type, product, unit, opening quantity): the examples from the product brief.
    EXAMPLES = [
        ("GROCERY", "Rice", "kg", "10"),
        ("GROCERY", "Biscuit", "pkt", "5"),
        ("VEGETABLE", "Potato", "kg", "25.5"),
        ("VEGETABLE", "Tomato", "kg", "12"),
        ("FRUIT", "Apple", "kg", "8.5"),
        ("FRUIT", "Banana", "pcs", "40"),
        ("SWEET_SHOP", "Gulab Jamun", "kg", "5"),
        ("SWEET_SHOP", "Samosa", "pcs", "100"),
        ("GARMENTS", "T-shirt", "pcs", "20"),
        ("GARMENTS", "Cotton cloth", "m", "37.25"),
        ("ELECTRONICS", "Mobile Charger", "pcs", "15"),
        ("FOOTWEAR", "Sandals", "pair", "30"),
        ("DAIRY", "Milk", "L", "18.5"),
        ("DAIRY", "Water bottle", "btl", "60"),
        ("BAKERY", "Cupcake tray", "tray", "4"),
        ("HARDWARE", "Copper wire", "m", "120.5"),
        ("STATIONERY", "Pens", "doz", "3.5"),
        ("COSMETICS", "Perfume", "ml", "500"),
    ]  # fmt: skip

    @pytest.mark.parametrize(("business_type", "name", "unit", "quantity"), EXAMPLES)
    def test_product_and_stock_work_for_the_brief_examples(
        self, make_client, tenant_of, units, business_type, name, unit, quantity
    ):
        tenant = tenant_of(business_type)
        client = make_client(tenant)

        product = client.post(
            PRODUCTS, json=product_body(tenant, units, unit, name=name, opening_stock=quantity)
        ).json()["product"]

        expected = Decimal(quantity).quantize(Decimal("0.001"))
        assert (product["name"], product["unit_code"], Decimal(product["current_stock"])) == (
            name,
            unit,
            expected,
        )
        history = client.get(f"{INV}/products/{product['id']}/transactions").json()["items"]
        assert [(h["txn_type"], Decimal(h["balance_after"])) for h in history] == [("OPENING", expected)]

    @pytest.mark.parametrize("business_type", REQUESTED_TYPES)
    def test_every_business_type_can_use_every_unit_and_fractions_follow_the_unit_only(
        self, make_client, tenant_of, units, business_type
    ):
        """No lock-in: a shop of ANY type may sell in ANY unit. Whether 2.5 is allowed depends on the unit,
        never on the type of business."""
        tenant = tenant_of(business_type)
        client = make_client(tenant)
        allows = {"pcs": False, "kg": True, "g": False, "L": True, "ml": False, "pkt": False, "box": False,
                  "doz": True, "m": True, "pair": False, "btl": False, "tray": False}  # fmt: skip

        for index, unit in enumerate(UNIT_CODES):
            created = client.post(PRODUCTS, json=product_body(tenant, units, unit, sku=f"P-{index}"))
            assert created.status_code == 201, (business_type, unit, created.text)
            product_id = created.json()["product"]["id"]
            fractional = client.post(
                f"{INV}/opening-stock", json={"product_id": product_id, "quantity": "2.5"}
            )
            assert (fractional.status_code == 201) is allows[unit], (business_type, unit)

    def test_a_grocery_shop_can_sell_things_outside_its_type(self, client_a, tenant_a, units):
        """Grocery selling flowers by the dozen, a garment-style pair, and a bakery tray."""
        flowers = client_a.post("/api/v1/categories", json={"name": "Flowers"}).json()["id"]

        for sku, unit in (("ROSE", "doz"), ("SOCKS", "pair"), ("CAKE", "tray")):
            response = client_a.post(
                PRODUCTS, json=product_body(tenant_a, units, unit, sku=sku, category_id=flowers)
            )
            assert response.status_code == 201

    def test_a_fruit_shop_can_sell_packaged_juice_and_water(self, make_client, tenant_of, units):
        tenant = tenant_of("FRUIT")
        client = make_client(tenant)
        drinks = client.post("/api/v1/categories", json={"name": "Packaged drinks"}).json()["id"]

        for sku, unit in (("JUICE", "btl"), ("WATER", "L")):
            assert (
                client.post(
                    PRODUCTS, json=product_body(tenant, units, unit, sku=sku, category_id=drinks)
                ).status_code
                == 201
            )

    def test_stock_status_and_history_do_not_depend_on_the_type(self, make_client, tenant_of, units):
        for business_type in ("BAKERY", "HARDWARE", "OTHER"):
            tenant = tenant_of(business_type)
            client = make_client(tenant)
            product = client.post(
                PRODUCTS, json=product_body(tenant, units, "kg", reorder_level="5", opening_stock="5")
            ).json()["product"]

            assert product["stock_status"] == "LOW_STOCK"  # at the reorder level, whatever the business

    def test_exports_work_for_any_type(self, make_client, tenant_of, units):
        tenant = tenant_of("GARMENTS")
        client = make_client(tenant)
        client.post(PRODUCTS, json=product_body(tenant, units, "pair", opening_stock="3"))

        for path in ("products", "inventory", "inventory-history"):
            for fmt in ("csv", "xlsx"):
                assert client.get(f"/api/v1/exports/{path}", params={"format": fmt}).status_code == 200

    def test_isolation_between_shops_of_different_types(self, make_client, tenant_of, units):
        bakery, garments = tenant_of("BAKERY"), tenant_of("GARMENTS")
        client_bakery, client_garments = make_client(bakery), make_client(garments)
        cake = client_bakery.post(
            PRODUCTS, json=product_body(bakery, units, "pcs", opening_stock="9")
        ).json()["product"]

        assert client_garments.get(f"{PRODUCTS}/{cake['id']}").status_code == 404
        assert client_garments.get(f"{INV}/products/{cake['id']}").status_code == 404
        assert client_garments.get(INV).json()["total"] == 0


class TestSuggestionTemplates:
    def test_every_business_type_has_a_template_entry_and_every_entry_is_a_real_type(self, session):
        codes = set(session.scalars(text("SELECT code FROM business_types")))

        assert set(bts.TEMPLATES) == codes

    def test_suggested_units_exist_and_are_not_repeated(self, session):
        real_units = set(session.scalars(text("SELECT code FROM units")))

        for code, template in bts.TEMPLATES.items():
            assert set(template.unit_codes) <= real_units, code
            assert len(set(template.unit_codes)) == len(template.unit_codes), code
            assert len({c.lower() for c in template.categories}) == len(template.categories), code

    def test_the_brief_examples_are_suggested(self):
        assert {"Rice & Grains", "Snacks", "Beverages", "Personal Care"} <= set(
            bts.TEMPLATES["GROCERY"].categories
        )
        assert {"Fruits", "Juices"} <= set(bts.TEMPLATES["FRUIT"].categories)
        assert {"Vegetables", "Leafy Vegetables"} <= set(bts.TEMPLATES["VEGETABLE"].categories)
        assert {"Sweets", "Namkeen", "Snacks", "Beverages"} == set(bts.TEMPLATES["SWEET_SHOP"].categories)

    def test_other_suggests_nothing_and_unknown_types_do_not_crash(self):
        assert bts.get_template("OTHER") == bts.NO_TEMPLATE == bts.BusinessTemplate()
        assert bts.get_template("NEVER_HEARD_OF_IT") == bts.NO_TEMPLATE

    def test_the_shop_template_endpoint(self, client_a):
        template = client_a.get("/api/v1/shop/template").json()

        assert (template["business_type"], template["business_type_name"]) == ("GROCERY", "Grocery / Kirana")
        assert template["unit_codes"][0] == "kg"
        by_name = {c["name"]: c["exists"] for c in template["categories"]}
        assert by_name["Snacks"] is False and by_name["Rice & Grains"] is False

    def test_existing_categories_are_marked_ignoring_case_and_the_template_follows_the_type(self, client_a):
        client_a.post("/api/v1/categories", json={"name": "snacks"})
        first = {c["name"]: c["exists"] for c in client_a.get("/api/v1/shop/template").json()["categories"]}
        client_a.patch("/api/v1/shop", json={"business_type": "FRUIT"})
        second = client_a.get("/api/v1/shop/template").json()

        assert first["Snacks"] is True
        assert second["business_type"] == "FRUIT" and [c["name"] for c in second["categories"]][0] == "Fruits"

    def test_suggestions_are_never_enforced(self, make_client, tenant_of, units):
        """A vegetable vendor may use a category and unit the template never mentions."""
        tenant = tenant_of("VEGETABLE")
        client = make_client(tenant)
        category = client.post("/api/v1/categories", json={"name": "Mobile accessories"}).json()["id"]

        response = client.post(PRODUCTS, json=product_body(tenant, units, "pair", category_id=category))

        assert response.status_code == 201


class TestNoBusinessSpecificCodeInTheCore:
    """The product, inventory, export and audit logic must not know what kind of business it serves."""

    CORE_FILES = [
        "services/product_service.py", "services/inventory_service.py", "services/catalog_service.py",
        "services/export_service.py", "services/export_datasets.py", "services/audit_service.py",
        "services/_filters.py", "services/shop_service.py", "services/errors.py",
        "models/catalog.py", "models/inventory.py", "models/khata.py", "models/purchasing.py",
        "models/sales.py", "models/expenses.py", "models/parties.py", "models/system.py", "models/base.py",
        "api/v1/products.py", "api/v1/inventory.py", "api/v1/exports.py", "api/v1/suppliers.py",
        "services/supplier_service.py", "services/contact_validation.py", "schemas/supplier.py",
        "schemas/product.py", "schemas/inventory.py", "schemas/common.py",
        "services/purchase_service.py", "services/costing_service.py", "services/numbering_service.py",
        "api/v1/purchases.py", "schemas/purchase.py",
        "services/customer_service.py", "services/khata_service.py", "api/v1/customers.py", "schemas/customer.py",
        "services/sale_service.py", "services/sale_calculation.py", "api/v1/sales.py", "schemas/sale.py",
        "services/quick_sale_service.py", "services/payment_service.py", "services/entitlement_service.py",
        "api/v1/quick_sales.py", "api/v1/subscription.py", "schemas/quick_sale.py", "schemas/subscription.py",
        "models/subscription.py", "models/promotion.py", "services/promotion_service.py",
        "services/promotion_calculation.py", "api/v1/promotions.py", "schemas/promotion.py",
        "services/price_comparison_service.py", "services/price_providers.py", "services/product_lookup_service.py",
        "api/v1/price_intelligence.py", "schemas/price.py", "models/pricing.py",
        "services/sales_report_service.py", "api/v1/reports.py", "schemas/report.py",
        "services/sales_return_service.py", "services/purchase_return_service.py", "services/return_calculation.py",
        "api/v1/returns.py", "schemas/returns.py", "services/image_intelligence_service.py",
        "services/image_validation.py", "services/image_providers.py", "services/image_store.py",
        "services/product_match_service.py", "services/idempotency_service.py", "api/idempotency.py",
        "api/v1/image_intelligence.py", "schemas/image_intelligence.py", "api/errors.py", "core/diagnostics.py",
    ]  # fmt: skip
    BUSINESS_WORDS = re.compile(
        r"grocery|kirana|halwai|sweet[ _]?shop|bakery|fruit|vegetable|dairy|garment|footwear|cosmetic|"
        r"electronics|hardware|stationery|meat|business_type",
        re.IGNORECASE,
    )

    def test_core_files_exist(self):
        assert all((BACKEND_DIR / "app" / name).is_file() for name in self.CORE_FILES)

    @pytest.mark.parametrize("name", CORE_FILES)
    def test_no_business_type_or_business_specific_wording(self, name):
        source = (BACKEND_DIR / "app" / name).read_text()

        assert self.BUSINESS_WORDS.findall(source) == [], name

    def test_only_the_business_type_module_and_the_shop_screens_read_the_type(self):
        readers = sorted(
            path.relative_to(BACKEND_DIR / "app").as_posix()
            for path in (BACKEND_DIR / "app").rglob("*.py")
            if "business_type" in path.read_text()
        )

        assert readers == [
            "api/v1/reference.py",  # the shop endpoints and the business-type list
            "models/shop.py",  # the column and the reference table
            "schemas/catalog.py",  # their JSON shapes
            "seed.py",  # the development shop
            "services/business_type_service.py",  # the only place that knows what a type suggests
        ]

    def test_a_product_has_no_business_specific_columns(self):
        columns = {c.name for c in Product.__table__.columns}

        assert not {c for c in columns if re.search(r"grocery|weight|imei|size|color|serial|expiry", c)}
