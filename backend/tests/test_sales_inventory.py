"""Sales, the stock ledger and khata: atomic posting, rollback, races, reversal, and the pure calculation."""

import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import CustomerLedgerEntry, DocumentSequence, InventoryTransaction, Product, Sale, SaleItem
from app.services import inventory_service, khata_service, numbering_service
from app.services import sale_calculation as calc
from app.services.errors import ConflictError
from tests.test_purchases_api import avg_cost, make_product, make_supplier, stock
from tests.test_sales_api import (
    API,
    draft,
    item,
    make_customer,
    owed,
    post,
    sold,
    stock_up,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"
D = Decimal


@pytest.fixture
def shelf(client_a, tenant_a, units):
    supplier = make_supplier(client_a)
    rice = make_product(client_a, tenant_a, units, "RICE", selling_price="50")
    sugar = make_product(client_a, tenant_a, units, "SUGAR", unit="kg", selling_price="48")
    oil = make_product(client_a, tenant_a, units, "OIL", unit="L", selling_price="140")
    for product, quantity, price in ((rice, 10, 20), (sugar, 10, 40), (oil, 10, 100)):
        stock_up(client_a, supplier, product, quantity, price)
    return {"supplier": supplier, "rice": rice, "sugar": sugar, "oil": oil}


def ledger_count(fresh) -> int:
    return fresh(lambda s: s.scalar(select(func.count()).select_from(InventoryTransaction)))


class TestPostingIsAtomic:
    def test_the_brief_example_rice_sugar_oil_all_or_nothing(self, client_a, shelf, fresh, monkeypatch):
        """Rice 5 kg, sugar 2 kg, oil 1 litre: if the third movement fails, none of the three is kept."""
        sale = draft(client_a, [item(shelf["rice"], "5"), item(shelf["sugar"], "2"), item(shelf["oil"], "1")])
        before = ledger_count(fresh)
        real = inventory_service.issue_sale_line
        calls = []

        def fails_on_third(*args, **kwargs):
            calls.append(kwargs["product"].sku)
            if len(calls) == 3:
                raise RuntimeError("disk full")
            return real(*args, **kwargs)

        monkeypatch.setattr(inventory_service, "issue_sale_line", fails_on_third)

        with pytest.raises(RuntimeError, match="disk full"):
            post(client_a, sale)

        assert calls == ["RICE", "SUGAR", "OIL"]
        assert ledger_count(fresh) == before  # the first two movements were rolled back too
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
        state = fresh(lambda s: s.execute(select(Sale.status, Sale.invoice_no, Sale.payment_type)).one())
        assert (state.status.value, state.invoice_no, state.payment_type) == ("DRAFT", None, None)
        assert fresh(lambda s: s.scalars(select(SaleItem.unit_cost)).all()) == [
            None,
            None,
            None,
        ]  # no snapshot kept
        assert (
            stock(client_a, shelf["rice"]),
            stock(client_a, shelf["sugar"]),
            stock(client_a, shelf["oil"]),
        ) == (10, 10, 10)

        # nothing is stuck: once the fault is gone the same draft posts normally and takes number 0001
        monkeypatch.setattr(inventory_service, "issue_sale_line", real)
        result = post(client_a, sale)
        assert result.status_code == 200 and result.json()["invoice_no"].endswith("/0001")
        assert (
            stock(client_a, shelf["rice"]),
            stock(client_a, shelf["sugar"]),
            stock(client_a, shelf["oil"]),
        ) == (5, 8, 9)

    def test_a_failure_charging_the_khata_rolls_back_the_stock_and_the_number(
        self, client_a, shelf, fresh, monkeypatch
    ):
        c = make_customer(client_a)
        sale = draft(client_a, [item(shelf["rice"], "4")], customer_id=c["id"])
        monkeypatch.setattr(
            khata_service, "record_credit_sale", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError, match="boom"):
            post(client_a, sale, amount_paid="0")

        assert stock(client_a, shelf["rice"]) == 10 and ledger_count(fresh) == 3  # only the three purchases
        assert client_a.get(f"{API}/{sale['id']}").json()["status"] == "DRAFT"
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

    def test_a_failure_while_numbering_leaves_no_stock_behind(self, client_a, shelf, fresh, monkeypatch):
        sale = draft(client_a, [item(shelf["rice"], "4")])
        monkeypatch.setattr(
            numbering_service, "next_number", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        )
        with pytest.raises(RuntimeError):
            post(client_a, sale)
        assert stock(client_a, shelf["rice"]) == 10 and ledger_count(fresh) == 3

    def test_a_failure_while_voiding_leaves_the_sale_posted_and_the_stock_out(
        self, client_a, shelf, fresh, monkeypatch
    ):
        c = make_customer(client_a)
        sale = sold(client_a, [item(shelf["rice"], "4")], header={"customer_id": c["id"]}, amount_paid="0")
        monkeypatch.setattr(
            khata_service, "reverse_credit_sale", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        )

        with pytest.raises(RuntimeError):
            client_a.post(f"{API}/{sale['id']}/void", json={"reason": "test"})

        assert client_a.get(f"{API}/{sale['id']}").json()["status"] == "POSTED"
        assert stock(client_a, shelf["rice"]) == 6 and owed(client_a, c) == D(
            "200.00"
        )  # the stock reversal rolled back too
        assert ledger_count(fresh) == 4


class TestVoidRestoresEverything:
    def test_void_gives_back_exactly_what_was_taken(self, client_a, shelf):
        sale = sold(client_a, [item(shelf["rice"], "3"), item(shelf["sugar"], "2.5")])
        client_a.post(f"{API}/{sale['id']}/void", json={"reason": "x"})
        assert (stock(client_a, shelf["rice"]), stock(client_a, shelf["sugar"])) == (10, 10)
        assert avg_cost(client_a, shelf["rice"]) == D("20.00") and avg_cost(client_a, shelf["sugar"]) == D(
            "40.00"
        )

    def test_a_sale_that_emptied_the_shelf_is_forgotten_by_the_average_when_voided(self, client_a, shelf):
        # buy 10 at 20, sell all 10, buy 10 at 30 (the empty shelf resets the average to 30) ...
        rice, supplier = shelf["rice"], shelf["supplier"]
        emptying = sold(client_a, [item(rice, "10")])
        stock_up(client_a, supplier, rice, 10, 30)
        assert avg_cost(client_a, rice) == D("30.00")

        # ... voiding the sale means the shelf never emptied: 10 at 20 and 10 at 30 -> 25
        client_a.post(f"{API}/{emptying['id']}/void", json={"reason": "Entered twice"})

        assert stock(client_a, rice) == 20 and avg_cost(client_a, rice) == D("25.00")

    def test_the_average_can_still_be_rebuilt_from_history_after_sales(
        self, client_a, tenant_a, shelf, session_factory, fresh
    ):
        sold(client_a, [item(shelf["sugar"], "4")])
        sold(client_a, [item(shelf["sugar"], "3")])
        stored = fresh(lambda s: s.get(Product, shelf["sugar"]["id"]).avg_cost)
        with session_factory() as s, s.begin():
            rebuilt = inventory_service.rebuild_average_cost(s, tenant_a.shop.id, shelf["sugar"]["id"])
        assert rebuilt == stored == D("40.00")


class TestRaces:
    def race(self, clients, request):
        barrier = threading.Barrier(len(clients))

        def run(client):
            barrier.wait()
            return request(client).status_code

        with ThreadPoolExecutor(len(clients)) as pool:
            return sorted(pool.map(run, clients))

    @pytest.mark.parametrize("attempt", range(4))
    def test_two_simultaneous_posts_of_one_draft_take_the_stock_once(
        self, client_a, make_client, tenant_a, shelf, fresh, attempt
    ):
        sale = draft(client_a, [item(shelf["rice"], "3")])
        clients = [make_client(tenant_a) for _ in range(3)]

        codes = self.race(clients, lambda c: post(c, sale))

        assert codes == [200, 409, 409]
        assert stock(client_a, shelf["rice"]) == 7
        assert (
            fresh(
                lambda s: s.scalar(
                    select(func.count())
                    .select_from(InventoryTransaction)
                    .where(InventoryTransaction.txn_type == "SALE")
                )
            )
            == 1
        )
        assert (
            fresh(
                lambda s: s.scalar(
                    select(DocumentSequence.last_number).where(DocumentSequence.doc_type == "SALE")
                )
            )
            == 1
        )

    @pytest.mark.parametrize("attempt", range(4))
    def test_two_sales_cannot_both_take_the_last_units(self, client_a, make_client, tenant_a, shelf, attempt):
        first = draft(client_a, [item(shelf["rice"], "6")])
        second = draft(client_a, [item(shelf["rice"], "6")])  # 10 on the shelf: only one of them fits
        clients = [make_client(tenant_a), make_client(tenant_a)]
        by_client = {id(clients[0]): first, id(clients[1]): second}

        codes = self.race(clients, lambda c: post(c, by_client[id(c)]))

        assert codes == [200, 409] and stock(client_a, shelf["rice"]) == 4  # never negative

    def test_many_simultaneous_sales_sell_exactly_what_there_is(self, client_a, make_client, tenant_a, shelf):
        drafts = [draft(client_a, [item(shelf["rice"], "3")]) for _ in range(5)]  # 15 wanted, 10 on the shelf
        clients = [make_client(tenant_a) for _ in drafts]
        by_client = {id(c): d for c, d in zip(clients, drafts, strict=True)}

        codes = self.race(clients, lambda c: post(c, by_client[id(c)]))

        assert codes == [200, 200, 200, 409, 409] and stock(client_a, shelf["rice"]) == 1

    def test_simultaneous_sales_get_distinct_gapless_numbers(
        self, client_a, make_client, tenant_a, shelf, fresh
    ):
        drafts = [draft(client_a, [item(shelf["rice"], "1")]) for _ in range(4)]
        clients = [make_client(tenant_a) for _ in drafts]
        by_client = {id(c): d for c, d in zip(clients, drafts, strict=True)}

        assert self.race(clients, lambda c: post(c, by_client[id(c)])) == [200] * 4

        numbers = fresh(lambda s: sorted(s.scalars(select(Sale.invoice_no)).all()))
        assert [n.rsplit("/", 1)[1] for n in numbers] == ["0001", "0002", "0003", "0004"]

    def test_two_simultaneous_voids_reverse_the_sale_once(
        self, client_a, make_client, tenant_a, shelf, fresh
    ):
        c = make_customer(client_a)
        sale = sold(client_a, [item(shelf["rice"], "2")], header={"customer_id": c["id"]}, amount_paid="0")
        clients = [make_client(tenant_a) for _ in range(2)]

        codes = self.race(clients, lambda cl: cl.post(f"{API}/{sale['id']}/void", json={"reason": "x"}))

        assert codes == [200, 409] and stock(client_a, shelf["rice"]) == 10 and owed(client_a, c) == 0
        assert (
            fresh(lambda s: s.scalar(select(func.count()).select_from(CustomerLedgerEntry))) == 2
        )  # credit + one reversal


class TestServiceLevel:
    def test_find_shortages_counts_the_total_per_product(self, session_factory, tenant_a, shelf):
        with session_factory() as s:
            found = inventory_service.find_shortages(
                s, tenant_a.shop.id, {shelf["rice"]["id"]: D("11"), shelf["sugar"]["id"]: D("10")}
            )
        assert [(x.product_id, x.available, x.requested) for x in found] == [
            (shelf["rice"]["id"], D("10.000"), D("11"))
        ]

    def test_find_shortages_is_empty_when_the_shop_allows_negative_stock(
        self, session_factory, tenant_a, shelf, set_shop
    ):
        set_shop(tenant_a, allow_negative_stock=True)
        with session_factory() as s:
            assert inventory_service.find_shortages(s, tenant_a.shop.id, {shelf["rice"]["id"]: D("99")}) == []

    def test_issue_sale_line_refuses_more_than_is_on_hand_under_the_lock(
        self, session_factory, tenant_a, shelf
    ):
        from tests.conftest import context_for

        with session_factory() as s, pytest.raises(ConflictError, match="Not enough stock"), s.begin():
            product = inventory_service.lock_products(s, tenant_a.shop.id, [shelf["rice"]["id"]])[
                shelf["rice"]["id"]
            ]
            inventory_service.issue_sale_line(
                s,
                context_for(tenant_a),
                product=product,
                quantity=D("11"),
                sale_item_id=1,
                txn_date=__import__("datetime").date.today(),
            )
        with session_factory() as s:
            assert (
                s.scalar(
                    select(func.count())
                    .select_from(InventoryTransaction)
                    .where(InventoryTransaction.txn_type == "SALE")
                )
                == 0
            )


class TestCalculation:
    def test_the_arithmetic(self):
        gross = calc.line_gross(D("5"), D("30"))
        assert gross == D("150.00") and calc.line_total(gross, D("10")) == D("140.00")
        assert calc.bill_totals([D("140.00"), D("60.00")], D("5")).total == D("195.00")

    def test_rounding_is_half_up_to_paise(self):
        assert calc.line_gross(D("0.5"), D("0.01")) == D("0.01")  # 0.005
        assert calc.line_gross(D("0.333"), D("10")) == D("3.33")
        assert calc.line_gross(D("2.5"), D("0.05")) == D("0.13")  # 0.125

    def test_a_discount_can_zero_a_line_but_never_make_it_negative(self):
        assert calc.line_total(D("10.00"), D("10.00")) == D("0.00")
        with pytest.raises(calc.DiscountTooLargeError):
            calc.line_total(D("10.00"), D("10.01"))
        with pytest.raises(calc.DiscountTooLargeError):
            calc.bill_totals([D("10")], D("10.01"))

    def test_profit_is_none_when_any_cost_is_unknown_never_zero(self):
        assert calc.gross_profit(D("100"), [D("40"), None]) is None
        assert calc.gross_profit(D("100"), [None]) is None
        assert calc.gross_profit(D("100"), []) is None
        assert calc.gross_profit(D("100"), [D("40"), D("10")]) == D("50")
        assert calc.total_cogs([D("1"), None]) is None and calc.line_profit(D("5"), None) is None

    def test_a_zero_cost_is_a_known_cost(self):
        assert calc.gross_profit(D("100"), [D("0")]) == D("100")  # free goods are known, unlike unknown

    def test_payment_split(self):
        assert calc.split_payment(D("1000"), D("700")).credit == D("300")
        assert calc.split_payment(D("1000"), None).payment_type.value == "PAID"
        assert calc.split_payment(D("1000"), D("0")).credit == D("1000")
        with pytest.raises(ValueError):
            calc.split_payment(D("1000"), D("1000.01"))

    @pytest.mark.parametrize("seed", range(25))
    def test_totals_always_add_up(self, seed):
        rng = random.Random(seed)
        lines = []
        for _ in range(rng.randint(1, 12)):
            quantity = D(rng.randint(1, 20000)) / 1000
            price = D(rng.randint(0, 99999)) / 100
            gross = calc.line_gross(quantity, price)
            discount = (gross * D(rng.randint(0, 100)) / 100).quantize(D("0.01"))
            lines.append(calc.line_total(gross, min(discount, gross)))
        bill_discount = min(sum(lines, D("0")), D(rng.randint(0, 5000)) / 100)
        bill = calc.bill_totals(lines, bill_discount)
        assert bill.subtotal == sum(lines, D("0")) and bill.total == bill.subtotal - bill_discount >= 0


class TestArchitectureRules:
    def source(self, name):
        return (APP_DIR / name).read_text()

    def test_sale_code_never_touches_a_ledger_table_directly(self):
        for name in (
            "services/sale_service.py",
            "services/sale_calculation.py",
            "api/v1/sales.py",
            "schemas/sale.py",
        ):
            assert not re.search(
                r"InventoryTransaction|inventory_transactions|CustomerLedgerEntry|customer_ledger",
                self.source(name),
            ), name

    def test_the_calculation_module_is_pure(self):
        """No database, no web framework, no other service: only arithmetic helpers and two small enums/types."""
        imported = set(
            re.findall(
                r"^(?:from|import)\s+([\w.]+)", self.source("services/sale_calculation.py"), re.MULTILINE
            )
        )
        assert imported == {"collections.abc", "dataclasses", "decimal", "app.db.types", "app.models.enums"}

    def test_there_is_no_stored_stock_on_products_or_sales(self):
        for model in (Product, Sale, SaleItem):
            assert "current_stock" not in model.__table__.columns and "stock" not in model.__table__.columns

    def test_the_route_does_no_arithmetic_or_ledger_writes(self):
        source = self.source("api/v1/sales.py")
        assert "Decimal(" not in source and "session.add" not in source
