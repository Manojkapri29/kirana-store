"""Purchase exports: list, items and single-purchase details as CSV and XLSX, shop-scoped and formula-safe."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import UserRole
from tests.test_exports import INJECTIONS, all_text, read_csv, read_xlsx
from tests.test_purchases_api import (
    API,
    INV,
    draft,
    line,
    make_product,
    make_supplier,
    posted,
)

EXPORTS = "/api/v1/exports"


def rows_of(response) -> list[dict[str, str]]:
    table = read_csv(response.content)
    return [dict(zip(table[0], row, strict=True)) for row in table[1:]]


@pytest.fixture
def data(client_a, tenant_a, units):
    supplier = make_supplier(client_a, "Sharma Traders")
    other = make_supplier(client_a, "Gupta Stores")
    rice = make_product(client_a, tenant_a, units, "RICE")
    kg = make_product(client_a, tenant_a, units, "SUGAR", unit="kg")
    a = posted(
        client_a,
        supplier,
        [line(rice, "100", "20"), line(kg, "2.5", "40", discount="5")],
        supplier_invoice_no="A-1",
        purchase_date="2026-01-10",
        notes="First",
    )
    b = posted(
        client_a, other, [line(rice, "50", "30")], supplier_invoice_no="B-1", purchase_date="2026-02-10"
    )
    c = draft(client_a, supplier, [line(kg, "1", "10")], purchase_date="2026-03-10")
    return supplier, other, rice, kg, a, b, c


class TestPurchaseList:
    def test_csv_has_one_row_per_purchase_with_totals(self, client_a, data):
        _, _, _, _, a, b, c = data

        response = client_a.get(f"{EXPORTS}/purchases")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"].startswith('attachment; filename="purchases_')
        assert response.headers["cache-control"] == "no-store"
        rows = rows_of(response)
        assert [r["Status"] for r in rows] == ["Draft", "Posted", "Posted"]  # newest first
        by_supplier = {(r["Supplier"], r["Status"]): r for r in rows}
        first = by_supplier[("Sharma Traders", "Posted")]
        assert first["Purchase No"] == a["purchase_no"] and first["Items"] == "2"
        assert (
            first["Total"] == "2095.00"
            and first["Supplier Invoice No"] == "A-1"
            and first["Notes"] == "First"
        )
        assert first["Created By"] == "Test Owner" and first["Posted At"]
        assert by_supplier[("Sharma Traders", "Draft")]["Purchase No"] == ""  # drafts have no number yet

    def test_xlsx_has_real_dates_and_numbers(self, client_a, data):
        response = client_a.get(f"{EXPORTS}/purchases", params={"format": "xlsx", "status": "POSTED"})

        sheet = read_xlsx(response.content)
        header = [c.value for c in sheet[1]]
        first = {h: c for h, c in zip(header, sheet[2], strict=True)}
        assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert first["Date"].value.date() == date(2026, 2, 10) and first["Date"].number_format == "dd/mm/yyyy"
        assert first["Total"].value == Decimal("1500.00") or float(first["Total"].value) == 1500.0
        assert first["Total"].number_format == "#,##0.00" and first["Items"].value == 1

    def test_filters_apply_to_the_export(self, client_a, data):
        _, other, _, _, a, b, c = data

        assert [
            r["Purchase No"]
            for r in rows_of(client_a.get(f"{EXPORTS}/purchases", params={"supplier_id": other["id"]}))
        ] == [b["purchase_no"]]
        assert len(rows_of(client_a.get(f"{EXPORTS}/purchases", params={"status": "DRAFT"}))) == 1
        assert len(rows_of(client_a.get(f"{EXPORTS}/purchases", params={"date_from": "2026-02-01"}))) == 2
        assert len(rows_of(client_a.get(f"{EXPORTS}/purchases", params={"q": "A-1"}))) == 1

    def test_an_export_of_no_purchases_is_just_the_header(self, client_a):
        response = client_a.get(f"{EXPORTS}/purchases")
        assert response.status_code == 200 and len(read_csv(response.content)) == 1


class TestPurchaseItems:
    def test_one_row_per_line_with_cost_information(self, client_a, data):
        rows = rows_of(client_a.get(f"{EXPORTS}/purchase-items", params={"status": "POSTED"}))

        assert len(rows) == 3
        sugar = next(r for r in rows if r["SKU"] == "SUGAR")
        assert (sugar["Quantity"], sugar["Price"], sugar["Discount"], sugar["Line Total"]) == (
            "2.500",
            "40.00",
            "5.00",
            "95.00",
        )
        assert sugar["Unit"] == "kg" and sugar["Purchase Total"] == "2095.00"
        second_rice = next(r for r in rows if r["SKU"] == "RICE" and r["Quantity"] == "50.000")
        assert (
            second_rice["Stock Before"],
            second_rice["Average Cost Before"],
            second_rice["Average Cost After"],
        ) == (
            "100.000",
            "20.00",
            "23.33",
        )

    def test_a_draft_line_has_no_snapshot_yet(self, client_a, data):
        rows = rows_of(client_a.get(f"{EXPORTS}/purchase-items", params={"status": "DRAFT"}))
        assert len(rows) == 1 and rows[0]["Stock Before"] == "" and rows[0]["Average Cost After"] == ""

    def test_xlsx(self, client_a, data):
        response = client_a.get(f"{EXPORTS}/purchase-items", params={"format": "xlsx"})
        sheet = read_xlsx(response.content)
        assert sheet.max_row == 5  # header + 4 lines (3 posted, 1 draft)
        assert response.headers["content-disposition"].startswith('attachment; filename="purchase_items_')


class TestSinglePurchase:
    def test_details_repeat_the_header_on_every_line(self, client_a, data):
        _, _, _, _, a, _, _ = data

        response = client_a.get(f"{EXPORTS}/purchases/{a['id']}")

        assert response.status_code == 200
        assert response.headers["content-disposition"].startswith(
            f'attachment; filename="purchase_{a["id"]}_'
        )
        rows = rows_of(response)
        assert [r["SKU"] for r in rows] == ["RICE", "SUGAR"]
        assert {r["Purchase No"] for r in rows} == {a["purchase_no"]} and {r["Supplier"] for r in rows} == {
            "Sharma Traders"
        }

    def test_details_xlsx(self, client_a, data):
        _, _, _, _, a, _, _ = data
        assert (
            read_xlsx(
                client_a.get(f"{EXPORTS}/purchases/{a['id']}", params={"format": "xlsx"}).content
            ).max_row
            == 3
        )

    def test_an_unknown_purchase_is_404(self, client_a, data):
        assert client_a.get(f"{EXPORTS}/purchases/99999").status_code == 404


class TestSafetyAndScope:
    @pytest.mark.parametrize("fmt", ["csv", "xlsx"])
    @pytest.mark.parametrize("payload", INJECTIONS[:5])
    def test_text_that_looks_like_a_formula_is_neutralised(self, client_a, tenant_a, units, payload, fmt):
        supplier = make_supplier(client_a, payload)
        product = make_product(client_a, tenant_a, units, "X1", name=payload)
        p = posted(client_a, supplier, [line(product)], supplier_invoice_no=payload[:50], notes=payload)

        for path in ("purchases", "purchase-items", f"purchases/{p['id']}"):
            content = client_a.get(f"{EXPORTS}/{path}", params={"format": fmt}).content
            if fmt == "csv":
                cells = [c for row in read_csv(content) for c in row]
            else:
                cells = [
                    str(c.value) for row in read_xlsx(content).iter_rows() for c in row if c.value is not None
                ]
            dangerous = [c for c in cells if c.startswith(("=", "+", "-", "@", "\t", "\r"))]
            assert dangerous == [], (path, dangerous)
            assert "'" + payload in "\n".join(cells)

    def test_an_export_never_contains_another_shops_purchases(
        self, client_a, client_b, tenant_b, units, data
    ):
        theirs = make_supplier(client_b, "Their Supplier")
        product = make_product(client_b, tenant_b, units, "THEIRS")
        posted(client_b, theirs, [line(product)])

        text = all_text(client_a.get(f"{EXPORTS}/purchases").content, "csv") + all_text(
            client_a.get(f"{EXPORTS}/purchase-items").content, "csv"
        )
        assert "Their Supplier" not in text and "THEIRS" not in text
        assert len(rows_of(client_b.get(f"{EXPORTS}/purchases"))) == 1

    def test_another_shops_purchase_details_are_404(self, client_a, client_b, data):
        _, _, _, _, a, _, _ = data
        assert client_b.get(f"{EXPORTS}/purchases/{a['id']}").status_code == 404

    def test_only_the_owner_can_export(self, make_client, tenant_a, data):
        staff = make_client(tenant_a, role=UserRole.STAFF)
        for path in ("purchases", "purchase-items", "purchases/1"):
            assert staff.get(f"{EXPORTS}/{path}").status_code == 403

    def test_a_bad_format_is_refused(self, client_a, data):
        assert client_a.get(f"{EXPORTS}/purchases", params={"format": "pdf"}).status_code == 422


class TestInventoryHistoryShowsThePurchase:
    def test_the_history_export_names_the_purchase_behind_each_row(self, client_a, data):
        _, _, rice, _, a, b, _ = data

        rows = rows_of(client_a.get(f"{EXPORTS}/inventory-history", params={"product_id": rice["id"]}))

        assert [(r["Type"], r["Purchase No"]) for r in rows] == [
            ("PURCHASE", a["purchase_no"]),
            ("PURCHASE", b["purchase_no"]),
        ]
        assert [r["Balance After"] for r in rows] == ["100.000", "150.000"]

    def test_a_voided_purchase_shows_both_rows(self, client_a, data):
        _, _, rice, _, _, b, _ = data
        client_a.post(f"{API}/{b['id']}/void", json={"reason": "x"})

        rows = rows_of(client_a.get(f"{EXPORTS}/inventory-history", params={"product_id": rice["id"]}))

        assert [r["Type"] for r in rows][-2:] == ["PURCHASE", "REVERSAL"]
        assert rows[-1]["Purchase No"] == b["purchase_no"] and rows[-1]["Balance After"] == "100.000"
        assert client_a.get(f"{INV}/products/{rice['id']}").json()["current_stock"] == "100.000"
