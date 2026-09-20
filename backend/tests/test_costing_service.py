"""costing_service: the moving weighted average, as pure arithmetic (no database)."""

import random
from decimal import Decimal

import pytest

from app.models.enums import InventoryTxnType as T
from app.services.costing_service import CostEvent, next_average_cost, replay_average_cost

D = Decimal


def receive(stock, avg, qty, value):
    return next_average_cost(
        stock_before=D(stock), avg_before=avg and D(avg), quantity=D(qty), line_value=D(value)
    )


class TestTheAverage:
    def test_the_example_from_the_brief_100_at_20_then_50_at_30_is_23_33(self):
        assert receive("100", "20", "50", "1500") == D("23.33")  # (100*20 + 50*30) / 150 = 23.333...

    def test_the_first_purchase_sets_the_average_to_its_own_cost(self):
        assert receive("0", None, "10", "425") == D("42.50")

    def test_after_the_shelf_emptied_the_old_average_is_forgotten(self):
        assert receive("0", "20", "5", "150") == D("30.00")

    def test_stock_below_zero_is_treated_like_an_empty_shelf(self):
        assert receive("-3", "20", "5", "150") == D("30.00")

    def test_the_same_price_leaves_the_average_unchanged(self):
        assert receive("40", "12.50", "10", "125") == D("12.50")

    def test_fractional_quantities(self):
        # 2.5 kg at 40 = 100 on hand; then 1.25 kg for 62.50 (50/kg): (100 + 62.50) / 3.75 = 43.333...
        assert receive("2.5", "40", "1.25", "62.50") == D("43.33")

    def test_a_discount_lowers_the_cost_because_the_net_line_total_is_used(self):
        # 10 at 100 = 1000, minus a 100 discount = 900 -> 90.00 each, not 100
        assert receive("0", None, "10", "900") == D("90.00")

    def test_large_quantities_stay_exact(self):
        # 999,999.999 units at 1.01 each, then 1 more at 2.00: no floating point drift
        line = (D("999999.999") * D("1.01")).quantize(D("0.01"))
        assert receive("0", None, "999999.999", line) == D("1.01")
        assert receive("999999.999", "1.01", "1", "2.00") == D("1.01")  # 1000000.999 * ~ still rounds to 1.01

    def test_paise_precision_rounds_half_up(self):
        # (1*0.10 + 1*0.11) / 2 = 0.105 -> 0.11 (half up), never banker's 0.10
        assert receive("1", "0.10", "1", "0.11") == D("0.11")

    def test_result_never_uses_floats(self):
        result = receive("3", "0.1", "3", "0.2")
        assert isinstance(result, Decimal)

    def test_zero_cost_goods_pull_the_average_down(self):
        assert receive("10", "10", "10", "0") == D("5.00")  # free goods are still known-cost goods


class TestUnknownCost:
    def test_a_receipt_without_a_cost_into_an_empty_shelf_keeps_the_cost_unknown(self):
        assert next_average_cost(stock_before=D("0"), avg_before=None, quantity=D("5")) is None

    def test_a_receipt_without_a_cost_keeps_the_existing_average(self):
        assert next_average_cost(stock_before=D("10"), avg_before=D("8"), quantity=D("5")) == D("8")

    def test_a_known_cost_on_top_of_unknown_cost_stock_does_not_invent_a_number(self):
        assert receive("10", None, "5", "50") is None

    def test_unknown_cost_stock_gets_a_cost_once_the_shelf_has_emptied(self):
        assert receive("0", None, "5", "50") == D("10.00")

    def test_opening_stock_cost_per_unit_is_used_when_no_line_value_exists(self):
        assert next_average_cost(
            stock_before=D("0"), avg_before=None, quantity=D("4"), unit_cost=D("7.25")
        ) == D("7.25")


class TestReplay:
    def test_replay_of_the_brief_example(self):
        stock, average = replay_average_cost(
            [
                CostEvent(T.PURCHASE, D("100"), line_value=D("2000")),
                CostEvent(T.PURCHASE, D("50"), line_value=D("1500")),
            ]
        )
        assert (stock, average) == (D("150"), D("23.33"))

    def test_adjustments_change_stock_but_never_the_average(self):
        stock, average = replay_average_cost(
            [
                CostEvent(T.OPENING, D("10"), unit_cost=D("20")),
                CostEvent(T.ADJUSTMENT, D("-4")),
                CostEvent(T.PURCHASE, D("6"), line_value=D("180")),  # 6 on hand at 20 + 6 at 30 -> 25
            ]
        )
        assert (stock, average) == (D("12"), D("25.00"))

    def test_opening_stock_without_a_cost_leaves_the_average_unknown(self):
        assert replay_average_cost([CostEvent(T.OPENING, D("10"))]) == (D("10"), None)

    def test_nothing_recorded_means_no_stock_and_no_average(self):
        assert replay_average_cost([]) == (D("0"), None)

    @pytest.mark.parametrize("seed", range(25))
    def test_replay_equals_applying_the_steps_one_by_one(self, seed):
        """The cached average is rebuildable: replaying a random history gives the live result."""
        rng = random.Random(seed)
        stock, average, events = D("0"), None, []
        for _ in range(rng.randint(1, 30)):
            kind = rng.choice(["buy", "buy", "buy", "adjust"])
            if kind == "buy":
                qty = D(rng.randint(1, 5000)) / D(1000)
                value = D(rng.randint(0, 200000)) / D(100)
                average = next_average_cost(
                    stock_before=stock, avg_before=average, quantity=qty, line_value=value
                )
                stock += qty
                events.append(CostEvent(T.PURCHASE, qty, line_value=value))
            else:
                delta = D(rng.randint(-3000, 3000)) / D(1000)
                stock += delta
                events.append(CostEvent(T.ADJUSTMENT, delta))
        assert replay_average_cost(events) == (stock, average)
