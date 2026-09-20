"""Purchases and the stock ledger: atomicity, rollback, reversal guards, rebuildable average cost, races."""

import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import DocumentSequence, InventoryTransaction, Product, Purchase
from app.models.enums import AdjustmentReason
from app.services import inventory_service, numbering_service, purchase_service
from app.services.errors import ConflictError, NotFoundError
from tests import factories
from tests.conftest import context_for
from tests.test_purchases_api import (
    API,
    avg_cost,
    draft,
    line,
    make_product,
    make_supplier,
    post,
    posted,
    stock,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture
def setup(client_a, tenant_a, units):
    return (
        make_supplier(client_a),
        make_product(client_a, tenant_a, units, "RICE"),
        make_product(client_a, tenant_a, units, "DAL"),
    )


def ledger_count(fresh) -> int:
    return fresh(lambda s: s.scalar(select(func.count()).select_from(InventoryTransaction)))


class TestPostingIsAtomic:
    def test_a_failure_on_the_second_line_rolls_back_the_first_line_the_number_and_the_cost(
        self, client_a, setup, fresh, monkeypatch
    ):
        supplier, rice, dal = setup
        purchase = draft(client_a, supplier, [line(rice, "10", "20"), line(dal, "5", "30")])
        real = inventory_service.receive_purchase_line
        calls = []

        def fails_on_second(*args, **kwargs):
            calls.append(kwargs["product"].sku)
            if len(calls) == 2:
                raise RuntimeError("disk full")
            return real(*args, **kwargs)

        monkeypatch.setattr(inventory_service, "receive_purchase_line", fails_on_second)

        with pytest.raises(RuntimeError, match="disk full"):
            client_a.post(f"{API}/{purchase['id']}/post")

        assert calls == ["RICE", "DAL"]
        assert ledger_count(fresh) == 0  # the first line's stock row was rolled back too
        assert fresh(lambda s: s.scalar(select(func.count()).select_from(DocumentSequence))) == 0
        assert fresh(lambda s: s.scalars(select(Product.avg_cost)).all()) == [None, None]
        state = fresh(lambda s: s.execute(select(Purchase.status, Purchase.purchase_no)).one())
        assert (state.status.value, state.purchase_no) == ("DRAFT", None)
        item = client_a.get(f"{API}/{purchase['id']}").json()["items"][0]
        assert item["stock_before"] is None and item["avg_cost_after"] is None  # no snapshot either

        # nothing is stuck: after the fault is gone the same draft posts normally, taking number 0001
        monkeypatch.setattr(inventory_service, "receive_purchase_line", real)
        result = post(client_a, purchase)
        assert result.status_code == 200 and result.json()["purchase_no"].endswith("/0001")
        assert stock(client_a, rice) == 10 and stock(client_a, dal) == 5

    def test_a_failure_while_numbering_leaves_no_stock_behind(self, client_a, setup, fresh, monkeypatch):
        supplier, rice, _ = setup
        purchase = draft(client_a, supplier, [line(rice, "10", "20")])
        monkeypatch.setattr(
            numbering_service, "next_number", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError, match="boom"):
            client_a.post(f"{API}/{purchase['id']}/post")

        assert ledger_count(fresh) == 0 and stock(client_a, rice) == 0 and avg_cost(client_a, rice) is None

    def test_a_failure_while_voiding_leaves_the_purchase_posted_and_the_stock_in_place(
        self, client_a, setup, fresh, monkeypatch
    ):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice, "10", "20")])
        monkeypatch.setattr(
            inventory_service,
            "rebuild_average_cost",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
        )

        with pytest.raises(RuntimeError):
            client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "test"})

        assert client_a.get(f"{API}/{purchase['id']}").json()["status"] == "POSTED"
        assert (
            ledger_count(fresh) == 1
            and stock(client_a, rice) == 10
            and avg_cost(client_a, rice) == Decimal("20.00")
        )


class TestReversalGuards:
    def test_a_purchase_cannot_be_voided_once_its_stock_has_been_used(
        self, client_a, tenant_a, session_factory, setup
    ):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice, "10", "20")])
        with session_factory() as s, s.begin():  # 4 units are removed some other way (a sale, later)
            inventory_service.record_adjustment(
                s,
                context_for(tenant_a),
                product_id=rice["id"],
                quantity_delta=Decimal("-4"),
                reason_code=AdjustmentReason.DAMAGED,
            )

        response = client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "Wrong supplier"})

        assert response.status_code == 409
        assert "has only 6" in response.json()["detail"][0]["msg"]
        assert client_a.get(f"{API}/{purchase['id']}").json()["status"] == "POSTED"
        assert stock(client_a, rice) == 6

    def test_the_guard_looks_at_the_stock_of_every_line_before_changing_anything(
        self, client_a, tenant_a, session_factory, setup, fresh
    ):
        supplier, rice, dal = setup
        purchase = posted(client_a, supplier, [line(rice, "10", "20"), line(dal, "10", "20")])
        with session_factory() as s, s.begin():
            inventory_service.record_adjustment(
                s,
                context_for(tenant_a),
                product_id=dal["id"],
                quantity_delta=Decimal("-4"),
                reason_code=AdjustmentReason.DAMAGED,
            )

        assert client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "x"}).status_code == 409

        assert stock(client_a, rice) == 10  # rice was fine, but nothing was reversed
        assert ledger_count(fresh) == 3  # two purchase rows and one adjustment

    def test_voiding_is_allowed_when_the_shop_permits_negative_stock(
        self, client_a, tenant_a, session_factory, setup, set_shop
    ):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice, "10", "20")])
        with session_factory() as s, s.begin():
            inventory_service.record_adjustment(
                s,
                context_for(tenant_a),
                product_id=rice["id"],
                quantity_delta=Decimal("-4"),
                reason_code=AdjustmentReason.DAMAGED,
            )
        set_shop(tenant_a, allow_negative_stock=True)

        assert client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "x"}).status_code == 200
        assert stock(client_a, rice) == -4

    def test_a_purchase_line_cannot_be_reversed_twice_at_the_service_level(
        self, client_a, tenant_a, session_factory, setup
    ):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice, "10", "20")])
        ctx = context_for(tenant_a)
        ids = [purchase["items"][0]["id"]]
        from app.models.enums import StockReferenceType

        with session_factory() as s, s.begin():
            inventory_service.reverse_lines(
                s, ctx, reference_type=StockReferenceType.PURCHASE_ITEM, reference_ids=ids, note="once"
            )
        with session_factory() as s, pytest.raises(ConflictError), s.begin():
            inventory_service.reverse_lines(
                s, ctx, reference_type=StockReferenceType.PURCHASE_ITEM, reference_ids=ids, note="twice"
            )


class TestAverageCostIsRebuildable:
    def stored(self, fresh, product):
        return fresh(lambda s: s.get(Product, product["id"]).avg_cost)

    def test_rebuilding_from_history_reproduces_the_stored_average(
        self, client_a, tenant_a, session_factory, setup, fresh
    ):
        supplier, rice, _ = setup
        client_a.post(
            "/api/v1/inventory/opening-stock",
            json={"product_id": rice["id"], "quantity": "7", "unit_cost": "11.50"},
        )
        posted(client_a, supplier, [line(rice, "100", "20", discount="50")])
        posted(client_a, supplier, [line(rice, "33", "31.37")])
        stored = self.stored(fresh, rice)

        with session_factory() as s, s.begin():
            rebuilt = inventory_service.rebuild_average_cost(s, tenant_a.shop.id, rice["id"])

        assert rebuilt == stored and stored is not None

    def test_a_damaged_cache_is_repaired_by_a_rebuild(
        self, client_a, tenant_a, session_factory, setup, fresh
    ):
        supplier, rice, _ = setup
        posted(client_a, supplier, [line(rice, "100", "20")])
        posted(client_a, supplier, [line(rice, "50", "30")])
        with session_factory() as s, s.begin():
            s.get(Product, rice["id"]).avg_cost = Decimal("1.00")  # simulate a wrong cached value

        with session_factory() as s, s.begin():
            inventory_service.rebuild_average_cost(s, tenant_a.shop.id, rice["id"])

        assert self.stored(fresh, rice) == Decimal("23.33")

    def test_adjustments_do_not_change_the_average(self, client_a, tenant_a, session_factory, setup, fresh):
        supplier, rice, _ = setup
        posted(client_a, supplier, [line(rice, "100", "20")])
        with session_factory() as s, s.begin():
            inventory_service.record_adjustment(
                s,
                context_for(tenant_a),
                product_id=rice["id"],
                quantity_delta=Decimal("-30"),
                reason_code=AdjustmentReason.DAMAGED,
            )
        posted(client_a, supplier, [line(rice, "30", "30")])  # 70 at 20 + 30 at 30 -> 23.00

        assert avg_cost(client_a, rice) == Decimal("23.00")
        with session_factory() as s, s.begin():
            assert inventory_service.rebuild_average_cost(s, tenant_a.shop.id, rice["id"]) == Decimal("23.00")

    @pytest.mark.parametrize("seed", range(12))
    def test_after_any_mix_of_purchases_and_voids_the_stored_average_equals_a_rebuild(
        self, client_a, tenant_a, session_factory, setup, fresh, seed
    ):
        supplier, rice, _ = setup
        rng = random.Random(seed)
        live = []
        for _ in range(rng.randint(3, 9)):
            if live and rng.random() < 0.35:
                purchase = live.pop(rng.randrange(len(live)))
                client_a.post(f"{API}/{purchase['id']}/void", json={"reason": "random"})
            else:
                qty = f"{rng.randint(1, 500)}"
                price = f"{rng.randint(1, 9999) / 100:.2f}"
                discount = "0" if rng.random() < 0.6 else "0.01"
                live.append(posted(client_a, supplier, [line(rice, qty, price, discount=discount)]))
        stored = self.stored(fresh, rice)

        with session_factory() as s, s.begin():
            rebuilt = inventory_service.rebuild_average_cost(s, tenant_a.shop.id, rice["id"])

        assert rebuilt == stored
        assert stock(client_a, rice) == sum(Decimal(p["items"][0]["quantity"]) for p in live)

    def test_a_replay_that_disagrees_with_the_ledger_stock_is_reported_not_guessed(
        self, client_a, tenant_a, session_factory, setup, monkeypatch
    ):
        supplier, rice, _ = setup
        posted(client_a, supplier, [line(rice, "10", "20")])
        monkeypatch.setattr(
            inventory_service.costing_service, "replay_average_cost", lambda events: (Decimal("999"), None)
        )

        with session_factory() as s, pytest.raises(RuntimeError, match="replay mismatch"), s.begin():
            inventory_service.rebuild_average_cost(s, tenant_a.shop.id, rice["id"])


class TestRaces:
    def race(self, clients, request):
        barrier = threading.Barrier(len(clients))

        def run(client):
            barrier.wait()
            return request(client).status_code

        with ThreadPoolExecutor(len(clients)) as pool:
            return sorted(pool.map(run, clients))

    @pytest.mark.parametrize("attempt", range(4))
    def test_two_simultaneous_posts_of_one_draft_add_the_stock_once(
        self, client_a, make_client, tenant_a, setup, fresh, attempt
    ):
        supplier, rice, _ = setup
        purchase = draft(client_a, supplier, [line(rice, "10", "20")])
        clients = [make_client(tenant_a) for _ in range(3)]

        codes = self.race(clients, lambda c: c.post(f"{API}/{purchase['id']}/post"))

        assert codes == [200, 409, 409]
        assert stock(client_a, rice) == 10 and ledger_count(fresh) == 1
        assert fresh(lambda s: s.scalar(select(DocumentSequence.last_number))) == 1

    def test_two_simultaneous_voids_reverse_the_stock_once(
        self, client_a, make_client, tenant_a, setup, fresh
    ):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice, "10", "20")])
        clients = [make_client(tenant_a) for _ in range(2)]

        codes = self.race(clients, lambda c: c.post(f"{API}/{purchase['id']}/void", json={"reason": "x"}))

        assert codes == [200, 409] and stock(client_a, rice) == 0 and ledger_count(fresh) == 2

    def test_simultaneous_purchases_get_distinct_gapless_numbers_and_the_right_average(
        self, client_a, make_client, tenant_a, setup, fresh
    ):
        supplier, rice, _ = setup
        drafts = [draft(client_a, supplier, [line(rice, "10", "20")]) for _ in range(4)]
        clients = [make_client(tenant_a) for _ in drafts]
        by_client = dict(zip(map(id, clients), drafts, strict=True))

        codes = self.race(clients, lambda c: c.post(f"{API}/{by_client[id(c)]['id']}/post"))

        assert codes == [200] * 4
        numbers = fresh(lambda s: sorted(s.scalars(select(Purchase.purchase_no)).all()))
        assert [n.rsplit("/", 1)[1] for n in numbers] == ["0001", "0002", "0003", "0004"]
        assert stock(client_a, rice) == 40 and avg_cost(client_a, rice) == Decimal("20.00")


class TestNumbering:
    @pytest.mark.parametrize(
        ("day", "label"),
        [
            (date(2026, 3, 31), "2025-26"),
            (date(2026, 4, 1), "2026-27"),
            (date(2026, 12, 31), "2026-27"),
            (date(2027, 1, 1), "2026-27"),
            (date(2027, 3, 31), "2026-27"),
            (date(2027, 4, 1), "2027-28"),
            (date(2099, 5, 5), "2099-00"),
            (date(2000, 1, 1), "1999-00"),
        ],
    )
    def test_the_indian_financial_year_runs_april_to_march(self, day, label):
        assert numbering_service.fiscal_year_label(day) == label

    def test_the_document_number_format(self):
        assert numbering_service.format_document_number("PUR", "2026-27", 7) == "PUR/2026-27/0007"
        assert numbering_service.format_document_number("PUR", "2026-27", 12345) == "PUR/2026-27/12345"

    def test_sequences_are_separate_per_shop_document_type_and_year(self, session, tenant_a, tenant_b):
        next_ = numbering_service.next_number
        assert [next_(session, tenant_a.shop.id, "PURCHASE", "2026-27") for _ in range(3)] == [1, 2, 3]
        assert next_(session, tenant_b.shop.id, "PURCHASE", "2026-27") == 1  # other shop
        assert next_(session, tenant_a.shop.id, "SALE", "2026-27") == 1  # other document type
        assert next_(session, tenant_a.shop.id, "PURCHASE", "2027-28") == 1  # new financial year
        assert next_(session, tenant_a.shop.id, "PURCHASE", "2026-27") == 4

    def test_a_rolled_back_transaction_gives_its_number_back(self, session_factory, tenant_a):
        with session_factory() as s:
            with s.begin():
                assert numbering_service.next_number(s, tenant_a.shop.id, "PURCHASE", "2026-27") == 1
            with pytest.raises(RuntimeError), s.begin():
                numbering_service.next_number(s, tenant_a.shop.id, "PURCHASE", "2026-27")
                raise RuntimeError("posting failed")
            with s.begin():
                assert numbering_service.next_number(s, tenant_a.shop.id, "PURCHASE", "2026-27") == 2

    def test_the_number_year_follows_the_posting_date_in_the_shop_timezone(self, client_a, setup):
        supplier, rice, _ = setup
        purchase = posted(client_a, supplier, [line(rice)], purchase_date="2020-06-01")  # an old date...
        today_label = numbering_service.fiscal_year_label(factories.today_in_shop_timezone())

        assert f"/{today_label}/" in purchase["purchase_no"]  # ...still numbered in the current year


class TestServiceLevel:
    def test_the_service_refuses_another_shops_purchase(
        self, session_factory, client_a, tenant_a, tenant_b, setup
    ):
        supplier, rice, _ = setup
        purchase = draft(client_a, supplier, [line(rice)])

        with session_factory() as s, pytest.raises(NotFoundError), s.begin():
            purchase_service.post_purchase(s, context_for(tenant_b), purchase["id"])

    def test_compute_line_total(self):
        c = purchase_service.compute_line_total
        assert c(Decimal("3"), Decimal("33.33"), Decimal("0.99")) == Decimal("99.00")
        assert c(Decimal("0.001"), Decimal("1.00"), Decimal("0")) == Decimal("0.00")
        assert c(Decimal("0.5"), Decimal("0.01"), Decimal("0")) == Decimal("0.01")  # 0.005 rounds up
        with pytest.raises(ValueError):
            c(Decimal("1"), Decimal("1"), Decimal("1.01"))


class TestArchitectureRules:
    def source(self, name):
        return (APP_DIR / name).read_text()

    def test_purchase_code_never_touches_the_ledger_table_directly(self):
        for name in ["services/purchase_service.py", "api/v1/purchases.py", "schemas/purchase.py"]:
            assert not re.search(r"InventoryTransaction|inventory_transactions", self.source(name)), name

    def test_the_average_cost_is_assigned_only_by_the_inventory_service(self):
        writers = sorted(
            path.relative_to(APP_DIR).as_posix()
            for path in APP_DIR.rglob("*.py")
            if re.search(r"\.avg_cost\s*=[^=]", path.read_text())
        )
        assert writers == ["services/inventory_service.py"]

    def test_there_is_no_stored_current_stock_on_products_or_purchases(self):
        from app.models import Product as P
        from app.models import Purchase as Pu
        from app.models import PurchaseItem as PI

        for model in (P, Pu, PI):
            assert "current_stock" not in model.__table__.columns
            assert "stock" not in model.__table__.columns
