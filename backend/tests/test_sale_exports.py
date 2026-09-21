"""Sale exports: list, items and one sale as CSV and XLSX. Shop-scoped, owner-only and formula-safe."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import UserRole
from tests.test_exports import INJECTIONS, read_csv, read_xlsx
from tests.test_purchases_api import make_product, make_supplier
from tests.test_sales_api import draft, item, make_customer, sold, stock_up

EXPORTS = "/api/v1/exports"


def rows_of(response) -> list[dict[str, str]]:
    table = read_csv(response.content)
    return [dict(zip(table[0], row, strict=True)) for row in table[1:]]


@pytest.fixture
def data(client_a, tenant_a, units):
    supplier = make_supplier(client_a)
    sugar = make_product(client_a, tenant_a, units, "SUGAR", unit="kg", selling_price="48")
    oil = make_product(client_a, tenant_a, units, "OIL", unit="L", selling_price="140")
    stock_up(client_a, supplier, sugar, 100, 20)
    stock_up(client_a, supplier, sugar, 50, 30)  # 23.33 average
    client_a.post(
        "/api/v1/inventory/opening-stock", json={"product_id": oil["id"], "quantity": "20"}
    )  # no cost
    asha = make_customer(client_a, "Asha Devi", phone="9000000001")
    a = sold(client_a, [item(sugar, "5")], header={"customer_id": asha["id"], "sale_date": "2026-01-10"})
    b = sold(
        client_a, [item(sugar, "2"), item(oil, "1")],
        header={"customer_id": asha["id"], "sale_date": "2026-02-10", "notes": "festival", "discount": "6"},
        amount_paid="100", payment_method="UPI", payment_reference="UPI-1",
    )  # fmt: skip
    c = draft(client_a, [item(sugar, "1")], sale_date="2026-03-10")
    return sugar, oil, asha, a, b, c


class TestSaleList:
    def test_csv_has_one_row_per_sale_with_payment_cost_and_profit(self, client_a, data):
        _, _, _, a, b, c = data

        response = client_a.get(f"{EXPORTS}/sales")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"].startswith('attachment; filename="sales_')
        assert response.headers["cache-control"] == "no-store"
        assert response.content.startswith("﻿".encode())
        rows = {r["Invoice No"]: r for r in rows_of(response)}
        first, second, draft_row = rows[a["invoice_no"]], rows[b["invoice_no"]], rows[""]

        assert (first["Total"], first["Payment"], first["Payment Method"], first["On Khata (Credit)"]) == (
            "240.00",
            "Paid",
            "CASH",
            "0.00",
        )
        assert (first["Cost of Goods"], first["Gross Profit"], first["Cost Known"]) == (
            "116.65",
            "123.35",
            "Yes",
        )
        # the second sale has a line with no known cost: revenue is known, profit is EMPTY, never 0
        assert (second["Subtotal"], second["Bill Discount"], second["Total"]) == ("236.00", "6.00", "230.00")
        assert (second["Amount Paid"], second["On Khata (Credit)"], second["Payment"]) == (
            "100.00",
            "130.00",
            "Credit",
        )
        assert (second["Cost of Goods"], second["Gross Profit"], second["Cost Known"]) == ("", "", "No")
        assert (second["Payment Method"], second["Payment Reference"], second["Notes"]) == (
            "UPI",
            "UPI-1",
            "festival",
        )
        assert first["Customer"] == "Asha Devi" and first["Created By"] == "Test Owner" and first["Posted At"]
        assert draft_row["Status"] == "Draft" and draft_row["Payment"] == "" and draft_row["Cost Known"] == ""

    def test_xlsx_has_real_dates_and_numbers_and_blank_unknowns(self, client_a, data):
        response = client_a.get(f"{EXPORTS}/sales", params={"format": "xlsx", "status": "POSTED"})

        sheet = read_xlsx(response.content)
        header = [c.value for c in sheet[1]]
        newest = dict(zip(header, sheet[2], strict=True))
        assert (
            newest["Date"].value.date() == date(2026, 2, 10) and newest["Date"].number_format == "dd/mm/yyyy"
        )
        assert float(newest["Total"].value) == 230.0 and newest["Total"].number_format == "#,##0.00"
        assert newest["Gross Profit"].value is None  # unknown stays an empty cell, not 0
        older = dict(zip(header, sheet[3], strict=True))
        assert float(older["Gross Profit"].value) == 123.35

    def test_filters_apply(self, client_a, data):
        _, _, asha, a, b, c = data
        assert len(rows_of(client_a.get(f"{EXPORTS}/sales", params={"status": "DRAFT"}))) == 1
        assert len(rows_of(client_a.get(f"{EXPORTS}/sales", params={"payment_type": "CREDIT"}))) == 1
        assert len(rows_of(client_a.get(f"{EXPORTS}/sales", params={"date_from": "2026-02-01"}))) == 2
        assert len(rows_of(client_a.get(f"{EXPORTS}/sales", params={"customer_id": asha["id"]}))) == 2
        assert [
            r["Invoice No"] for r in rows_of(client_a.get(f"{EXPORTS}/sales", params={"q": "festival"}))
        ] == [b["invoice_no"]]

    def test_an_export_of_no_sales_is_just_the_header(self, client_a):
        assert len(read_csv(client_a.get(f"{EXPORTS}/sales").content)) == 1


class TestSaleItems:
    def test_one_row_per_line_with_cost_snapshot_and_profit(self, client_a, data):
        rows = rows_of(client_a.get(f"{EXPORTS}/sale-items", params={"status": "POSTED"}))

        assert len(rows) == 3
        sugar = next(r for r in rows if r["Quantity"] == "5.000")
        assert (sugar["Price"], sugar["Line Total"], sugar["Unit Cost"], sugar["Cost of Goods"], sugar["Line Profit"]) == (
            "48.00", "240.00", "23.33", "116.65", "123.35",
        )  # fmt: skip
        oil = next(r for r in rows if r["SKU"] == "OIL")
        assert (oil["Unit Cost"], oil["Cost of Goods"], oil["Line Profit"], oil["Line Total"]) == (
            "",
            "",
            "",
            "140.00",
        )

    def test_a_draft_line_has_no_cost_yet(self, client_a, data):
        rows = rows_of(client_a.get(f"{EXPORTS}/sale-items", params={"status": "DRAFT"}))
        assert len(rows) == 1 and rows[0]["Unit Cost"] == "" and rows[0]["Invoice No"] == ""

    def test_xlsx(self, client_a, data):
        sheet = read_xlsx(client_a.get(f"{EXPORTS}/sale-items", params={"format": "xlsx"}).content).max_row
        assert sheet == 5  # header + 4 lines


class TestSingleSale:
    def test_details_repeat_the_header_on_every_line(self, client_a, data):
        _, _, _, _, b, _ = data
        response = client_a.get(f"{EXPORTS}/sales/{b['id']}")
        assert response.status_code == 200
        assert response.headers["content-disposition"].startswith(f'attachment; filename="sale_{b["id"]}_')
        rows = rows_of(response)
        assert [r["SKU"] for r in rows] == ["SUGAR", "OIL"]
        assert {r["Invoice No"] for r in rows} == {b["invoice_no"]} and {r["Sale Total"] for r in rows} == {
            "230.00"
        }

    def test_details_xlsx_and_unknown_sale(self, client_a, data):
        _, _, _, _, b, _ = data
        assert (
            read_xlsx(client_a.get(f"{EXPORTS}/sales/{b['id']}", params={"format": "xlsx"}).content).max_row
            == 3
        )
        assert client_a.get(f"{EXPORTS}/sales/99999").status_code == 404


class TestSafetyAndScope:
    @pytest.mark.parametrize("fmt", ["csv", "xlsx"])
    @pytest.mark.parametrize("payload", INJECTIONS[:5])
    def test_text_that_looks_like_a_formula_is_neutralised(self, client_a, tenant_a, units, payload, fmt):
        supplier = make_supplier(client_a)
        product = make_product(client_a, tenant_a, units, "X1", name=payload, selling_price="10")
        stock_up(client_a, supplier, product, 5, 5)
        buyer = make_customer(client_a, payload)
        sale = sold(
            client_a, [item(product)], header={"customer_id": buyer["id"], "notes": payload},
            amount_paid="5", payment_method="UPI", payment_reference=payload[:100],
        )  # fmt: skip

        for path in ("sales", "sale-items", f"sales/{sale['id']}"):
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

    def test_an_export_never_contains_another_shops_sales(self, client_a, client_b, tenant_b, units, data):
        supplier = make_supplier(client_b, "Theirs")
        theirs = make_product(client_b, tenant_b, units, "THEIRS-SKU", selling_price="10")
        stock_up(client_b, supplier, theirs, 5, 5)
        buyer = make_customer(client_b, "Their Secret Buyer")
        sold(client_b, [item(theirs)], header={"customer_id": buyer["id"]})

        text = "".join(
            client_a.get(f"{EXPORTS}/{p}", params={"status": ["POSTED", "DRAFT", "VOID"]}).content.decode(
                "utf-8-sig"
            )
            for p in ("sales", "sale-items")
        )

        assert "THEIRS-SKU" not in text and "Their Secret Buyer" not in text
        assert len(rows_of(client_b.get(f"{EXPORTS}/sales"))) == 1

    def test_another_shops_sale_details_are_404(self, client_a, client_b, data):
        _, _, _, _, b, _ = data
        assert client_b.get(f"{EXPORTS}/sales/{b['id']}").status_code == 404

    def test_only_the_owner_can_export(self, make_client, tenant_a, data):
        staff = make_client(tenant_a, role=UserRole.STAFF)
        for path in ("sales", "sale-items", "sales/1"):
            assert staff.get(f"{EXPORTS}/{path}").status_code == 403

    def test_a_bad_format_or_filter_is_refused(self, client_a, data):
        assert client_a.get(f"{EXPORTS}/sales", params={"format": "pdf"}).status_code == 422
        assert client_a.get(f"{EXPORTS}/sales", params={"payment_type": "BARTER"}).status_code == 422


class TestOtherExportsShowSales:
    def test_the_inventory_history_export_names_the_invoice(self, client_a, data):
        sugar, _, _, a, b, _ = data
        rows = rows_of(client_a.get(f"{EXPORTS}/inventory-history", params={"product_id": sugar["id"]}))
        sale_rows = [r for r in rows if r["Type"] == "SALE"]
        assert [(r["Invoice No"], r["Quantity Change"]) for r in sale_rows] == [
            (a["invoice_no"], "-5.000"),
            (b["invoice_no"], "-2.000"),
        ]

    def test_the_customer_ledger_export_names_the_invoice_for_a_sale_on_credit(self, client_a, data):
        _, _, asha, a, b, _ = data
        rows = rows_of(client_a.get(f"{EXPORTS}/customers/{asha['id']}/ledger"))
        credit = next(r for r in rows if r["Type"] == "CREDIT_SALE")
        assert (credit["Source"], credit["Source Invoice No"], credit["Debit (Owed)"]) == (
            "SALE",
            b["invoice_no"],
            "130.00",
        )
        assert Decimal(rows[-1]["Balance After"]) == Decimal("130.00")
