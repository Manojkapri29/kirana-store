"""Customer exports: the customer list and one customer's khata, as CSV and XLSX. Shop-scoped, formula-safe."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import UserRole
from tests.test_customers_api import API, customer, pay
from tests.test_exports import INJECTIONS, read_csv, read_xlsx

EXPORTS = "/api/v1/exports"


def rows_of(response) -> list[dict[str, str]]:
    table = read_csv(response.content)
    return [dict(zip(table[0], row, strict=True)) for row in table[1:]]


@pytest.fixture
def khata(client_a):
    asha = customer(
        client_a, name="Asha Devi", phone="9000000001", email="asha@shop.in", opening_balance="800"
    )
    bina = customer(client_a, name="Bina", opening_balance="100")
    pay(client_a, bina["id"], "250")  # paid 150 ahead
    chitra = customer(client_a, name="Chitra")
    gone = customer(client_a, name="Dev", notes="moved away")
    client_a.post(f"{API}/{gone['id']}/deactivate")
    return asha, bina, chitra, gone


class TestCustomerList:
    def test_csv_has_one_row_per_customer_with_balances(self, client_a, khata):
        response = client_a.get(f"{EXPORTS}/customers")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"].startswith('attachment; filename="customers_')
        assert response.headers["cache-control"] == "no-store"
        assert response.content.startswith("﻿".encode())  # UTF-8 BOM so Excel shows Hindi and ₹ correctly
        by_name = {r["Customer"]: r for r in rows_of(response)}
        assert list(by_name) == ["Asha Devi", "Bina", "Chitra"]  # active only, by name
        asha, bina, chitra = by_name["Asha Devi"], by_name["Bina"], by_name["Chitra"]
        assert (asha["Balance"], asha["Outstanding (Owes)"], asha["Balance Status"]) == (
            "800.00",
            "800.00",
            "Owes",
        )
        assert (bina["Balance"], bina["Advance (Paid Ahead)"], bina["Balance Status"]) == (
            "-150.00",
            "150.00",
            "Advance",
        )
        assert (chitra["Balance"], chitra["Balance Status"], chitra["Ledger Entries"]) == (
            "0.00",
            "Settled",
            "0",
        )
        assert (
            asha["Phone"] == "9000000001" and asha["Email"] == "asha@shop.in" and asha["Status"] == "Active"
        )

    def test_filters_apply(self, client_a, khata):
        assert [
            r["Customer"]
            for r in rows_of(client_a.get(f"{EXPORTS}/customers", params={"status": "inactive"}))
        ] == ["Dev"]
        assert len(rows_of(client_a.get(f"{EXPORTS}/customers", params={"status": "all"}))) == 4
        assert [
            r["Customer"]
            for r in rows_of(client_a.get(f"{EXPORTS}/customers", params={"balance": "outstanding"}))
        ] == ["Asha Devi"]
        assert [
            r["Customer"] for r in rows_of(client_a.get(f"{EXPORTS}/customers", params={"q": "bina"}))
        ] == ["Bina"]

    def test_xlsx_has_real_numbers_and_dates(self, client_a, khata):
        response = client_a.get(f"{EXPORTS}/customers", params={"format": "xlsx"})

        sheet = read_xlsx(response.content)
        header = [c.value for c in sheet[1]]
        first = dict(zip(header, sheet[2], strict=True))
        assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert float(first["Balance"].value) == 800.0 and first["Balance"].number_format == "#,##0.00"
        assert first["Ledger Entries"].value == 1
        assert first["Created"].number_format == "dd/mm/yyyy hh:mm"

    def test_an_empty_shop_exports_only_the_header(self, client_a):
        assert len(read_csv(client_a.get(f"{EXPORTS}/customers").content)) == 1

    def test_only_the_owner_can_export(self, make_client, tenant_a, khata):
        staff = make_client(tenant_a, role=UserRole.STAFF)
        assert staff.get(f"{EXPORTS}/customers").status_code == 403
        assert staff.get(f"{EXPORTS}/customers/1/ledger").status_code == 403

    def test_a_bad_format_or_filter_is_refused(self, client_a, khata):
        assert client_a.get(f"{EXPORTS}/customers", params={"format": "pdf"}).status_code == 422
        assert client_a.get(f"{EXPORTS}/customers", params={"balance": "bogus"}).status_code == 422


class TestCustomerLedger:
    @pytest.fixture
    def history(self, client_a):
        c = customer(
            client_a,
            name="Asha Devi",
            phone="9000000001",
            opening_balance="1000",
            opening_balance_date="2026-01-01",
        )
        pay(
            client_a,
            c["id"],
            "300",
            payment_method="UPI",
            payment_reference="UPI-77",
            note="Counter",
            entry_date="2026-02-01",
        )
        payment = pay(client_a, c["id"], "50", entry_date="2026-03-01").json()["entry"]["id"]
        client_a.post(f"{API}/{c['id']}/ledger/{payment}/reverse", json={"reason": "Wrong customer"})
        return c, payment

    def test_debit_credit_and_running_balance(self, client_a, history):
        c, _ = history

        response = client_a.get(f"{EXPORTS}/customers/{c['id']}/ledger")

        assert response.status_code == 200
        assert response.headers["content-disposition"].startswith(
            f'attachment; filename="customer_{c["id"]}_ledger_'
        )
        rows = rows_of(response)
        assert [r["Type"] for r in rows] == [
            "OPENING_BALANCE",
            "PAYMENT",
            "PAYMENT",
            "REVERSAL",
        ]  # oldest first
        assert [(r["Debit (Owed)"], r["Credit (Received)"]) for r in rows] == [
            ("1000.00", ""),
            ("", "300.00"),
            ("", "50.00"),
            ("50.00", ""),
        ]
        assert [r["Balance After"] for r in rows] == ["1000.00", "700.00", "650.00", "700.00"]
        payment = rows[1]
        assert (payment["Payment Method"], payment["Payment Reference"], payment["Note"]) == (
            "UPI",
            "UPI-77",
            "Counter",
        )
        assert (
            payment["Customer"] == "Asha Devi"
            and payment["Phone"] == "9000000001"
            and payment["Recorded By"] == "Test Owner"
        )

    def test_reversals_link_to_their_originals(self, client_a, history):
        c, payment_id = history
        rows = rows_of(client_a.get(f"{EXPORTS}/customers/{c['id']}/ledger"))
        original = next(r for r in rows if r["Reversed By Entry"])
        reversal = next(r for r in rows if r["Type"] == "REVERSAL")
        assert reversal["Reverses Entry"] == str(payment_id) and original["Reversed By Entry"]

    def test_xlsx_dates_are_real_date_cells(self, client_a, history):
        c, _ = history
        sheet = read_xlsx(
            client_a.get(f"{EXPORTS}/customers/{c['id']}/ledger", params={"format": "xlsx"}).content
        )
        header = [cell.value for cell in sheet[1]]
        first = dict(zip(header, sheet[2], strict=True))
        assert first["Date"].value.date() == date(2026, 1, 1) and first["Date"].number_format == "dd/mm/yyyy"
        assert float(first["Debit (Owed)"].value) == 1000.0 and first["Credit (Received)"].value is None

    def test_filters(self, client_a, history):
        c, _ = history
        only_payments = rows_of(
            client_a.get(f"{EXPORTS}/customers/{c['id']}/ledger", params={"entry_type": "PAYMENT"})
        )
        recent = rows_of(
            client_a.get(f"{EXPORTS}/customers/{c['id']}/ledger", params={"date_from": "2026-02-15"})
        )
        assert len(only_payments) == 2 and all(r["Type"] == "PAYMENT" for r in only_payments)
        assert all(r["Type"] != "OPENING_BALANCE" for r in recent)

    def test_the_final_running_balance_equals_the_customers_balance(self, client_a, history):
        c, _ = history
        rows = rows_of(client_a.get(f"{EXPORTS}/customers/{c['id']}/ledger"))
        assert Decimal(rows[-1]["Balance After"]) == Decimal(
            client_a.get(f"{API}/{c['id']}/balance").json()["balance"]
        )


class TestSafetyAndScope:
    @pytest.mark.parametrize("fmt", ["csv", "xlsx"])
    @pytest.mark.parametrize("payload", INJECTIONS[:5])
    def test_text_that_looks_like_a_formula_is_neutralised(self, client_a, payload, fmt):
        c = customer(client_a, name=payload, address=payload, notes=payload, opening_balance="10")
        pay(client_a, c["id"], "5", payment_reference=payload[:100], note=payload)

        for path in ("customers", f"customers/{c['id']}/ledger"):
            content = client_a.get(f"{EXPORTS}/{path}", params={"format": fmt, "status": "all"}).content
            if fmt == "csv":
                cells = [cell for row in read_csv(content) for cell in row]
            else:
                cells = [
                    str(cell.value)
                    for row in read_xlsx(content).iter_rows()
                    for cell in row
                    if cell.value is not None
                ]
            dangerous = [cell for cell in cells if cell.startswith(("=", "+", "-", "@", "\t", "\r"))]
            assert dangerous == [], (path, dangerous)
            assert "'" + payload in "\n".join(cells)

    def test_an_export_never_contains_another_shops_customers(self, client_a, client_b):
        customer(client_a, name="Mine", opening_balance="5")
        theirs = customer(client_b, name="Theirs Secret", phone="9111111111", opening_balance="777")

        text = client_a.get(f"{EXPORTS}/customers", params={"status": "all"}).content.decode("utf-8-sig")

        assert "Theirs" not in text and "9111111111" not in text and "777" not in text
        assert len(rows_of(client_b.get(f"{EXPORTS}/customers"))) == 1
        assert theirs["id"]

    def test_another_shops_ledger_export_is_404(self, client_a, client_b):
        c = customer(client_a, opening_balance="10")
        assert client_b.get(f"{EXPORTS}/customers/{c['id']}/ledger").status_code == 404
        assert client_a.get(f"{EXPORTS}/customers/99999/ledger").status_code == 404
