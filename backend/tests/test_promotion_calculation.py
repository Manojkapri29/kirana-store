"""Promotion arithmetic: pure, no database. Each promotion type, conditions, caps, stacking and exact sharing."""

import random
from decimal import Decimal

import pytest

from app.models.enums import PromotionScope as Scope
from app.models.enums import PromotionType as Kind
from app.services import promotion_calculation as pc
from app.services.promotion_calculation import CartLine, Rule, apply_promotions

D = Decimal


def line(index, product=1, category=1, qty="1", price="100", net=None):
    quantity, unit = D(qty), D(price)
    return CartLine(index, product, category, quantity, unit, D(net) if net is not None else quantity * unit)


def percent(rule_id=1, bp=1000, scope=Scope.CART, **extra):
    return Rule(rule_id, f"P{rule_id}", Kind.PERCENT, scope, percent_bp=bp, **extra)


def amount(rule_id=1, value="50", scope=Scope.CART, **extra):
    return Rule(rule_id, f"A{rule_id}", Kind.AMOUNT, scope, amount=D(value), **extra)


def run(rules, lines, **kw):
    return apply_promotions(rules, lines, **kw)


class TestPercentAndAmount:
    def test_percent_of_the_whole_bill(self):
        out = run([percent(bp=1000)], [line(0, price="100", qty="2"), line(1, product=2, price="50")])
        assert out.total == D("25.00") and out.applied[0].terms == "10% off"
        assert out.per_line == {0: D("20.00"), 1: D("5.00")}

    def test_a_percent_is_rounded_half_up_to_the_paisa(self):
        assert run([percent(bp=1250)], [line(0, price="0.10")]).total == D("0.01")  # 0.0125 -> 0.01
        assert run([percent(bp=500)], [line(0, price="0.10")]).total == D("0.01")  # 0.005 -> 0.01 (half up)

    def test_fractional_percent(self):
        out = run([percent(bp=1250)], [line(0, price="200")])
        assert out.total == D("25.00") and out.applied[0].terms == "12.5% off"

    def test_a_fixed_amount_off(self):
        out = run([amount(value="50")], [line(0, price="100"), line(1, product=2, price="100")])
        assert out.total == D("50.00") and out.per_line == {0: D("25.00"), 1: D("25.00")}

    def test_a_fixed_amount_never_exceeds_the_bill(self):
        out = run([amount(value="500")], [line(0, price="120")])
        assert out.total == D("120.00")

    def test_amount_shared_over_lines_adds_up_exactly(self):
        out = run([amount(value="50")], [line(i, product=i, price="33.33") for i in range(3)])
        assert out.total == D("50.00") and sum(out.per_line.values()) == D("50.00")
        assert sorted(out.per_line.values()) == [D("16.66"), D("16.67"), D("16.67")]
        assert all(share <= D("33.33") for share in out.per_line.values())

    def test_product_scope_only_touches_those_products(self):
        rule = percent(bp=2000, scope=Scope.PRODUCTS, product_ids=frozenset({2}))
        out = run([rule], [line(0, product=1, price="100"), line(1, product=2, price="100")])
        assert out.total == D("20.00") and out.per_line == {1: D("20.00")}

    def test_category_scope_only_touches_those_categories(self):
        rule = amount(value="30", scope=Scope.CATEGORIES, category_ids=frozenset({7}))
        out = run(
            [rule],
            [line(0, product=1, category=7, price="100"), line(1, product=2, category=8, price="100")],
        )
        assert out.total == D("30.00") and out.per_line == {0: D("30.00")}

    def test_no_eligible_item_is_a_reason_not_a_discount(self):
        rule = percent(scope=Scope.PRODUCTS, product_ids=frozenset({99}))
        out = run([rule], [line(0)])
        assert out.total == 0 and "eligible" in out.skipped[0].reason

    def test_a_line_that_was_already_discounted_by_the_cashier_uses_what_is_left(self):
        out = run([percent(bp=1000)], [line(0, price="100", net="80")])  # 20 off by hand
        assert out.total == D("8.00")

    def test_a_free_line_gets_nothing(self):
        assert run([percent(bp=1000)], [line(0, price="100", net="0")]).total == 0


class TestOfferPrice:
    def rule(self, price="90", **extra):
        return Rule(
            1,
            "Offer",
            Kind.OFFER_PRICE,
            Scope.PRODUCTS,
            offer_price=D(price),
            product_ids=frozenset({1}),
            **extra,
        )

    def test_the_difference_per_unit_times_the_quantity(self):
        out = run([self.rule()], [line(0, qty="3", price="100")])
        assert out.total == D("30.00") and out.applied[0].terms == "Offer price 90.00"

    def test_an_offer_price_above_the_selling_price_does_nothing(self):
        out = run([self.rule(price="120")], [line(0, price="100")])
        assert out.total == 0 and "lower the price" in out.skipped[0].reason

    def test_an_offer_price_equal_to_the_selling_price_does_nothing(self):
        assert run([self.rule(price="100")], [line(0, price="100")]).total == 0

    def test_it_uses_the_price_the_cashier_charged_not_the_list_price(self):
        assert run([self.rule(price="90")], [line(0, price="95", qty="2")]).total == D("10.00")

    def test_a_free_offer_price(self):
        assert run([self.rule(price="0")], [line(0, price="40")]).total == D("40.00")

    def test_decimal_quantities(self):
        assert run([self.rule(price="80")], [line(0, qty="1.5", price="100")]).total == D("30.00")

    def test_other_products_are_untouched(self):
        out = run([self.rule()], [line(0, product=1), line(1, product=2)])
        assert set(out.per_line) == {0}


class TestBuyXGetY:
    def rule(self, buy=2, get=1, percent_bp=None, **extra):
        return Rule(
            1, "BXGY", Kind.BUY_X_GET_Y, Scope.PRODUCTS, buy_quantity=buy, get_quantity=get,
            get_percent_bp=percent_bp, product_ids=frozenset({1, 2}), **extra,
        )  # fmt: skip

    def test_buy_two_get_one_free(self):
        out = run([self.rule()], [line(0, qty="3", price="10")])
        assert out.total == D("10.00") and out.applied[0].terms == "Buy 2 get 1 free"

    def test_two_units_are_not_enough(self):
        assert run([self.rule()], [line(0, qty="2", price="10")]).total == 0

    def test_only_complete_groups_count(self):
        assert run([self.rule()], [line(0, qty="5", price="10")]).total == D("10.00")
        assert run([self.rule()], [line(0, qty="6", price="10")]).total == D("20.00")

    def test_the_cheapest_units_are_the_free_ones(self):
        out = run(
            [self.rule()], [line(0, product=1, qty="2", price="30"), line(1, product=2, qty="1", price="10")]
        )
        assert out.total == D("10.00") and out.per_line == {1: D("10.00")}

    def test_units_of_different_products_pool_together(self):
        out = run([self.rule(buy=1, get=1)], [line(0, product=1, price="50"), line(1, product=2, price="20")])
        assert out.total == D("20.00")

    def test_get_at_a_percentage_off(self):
        out = run([self.rule(percent_bp=5000)], [line(0, qty="3", price="10")])
        assert out.total == D("5.00") and out.applied[0].terms == "Buy 2 get 1 at 50% off"

    def test_a_part_unit_does_not_count(self):
        assert run([self.rule()], [line(0, qty="2.5", price="10")]).total == 0

    def test_a_discounted_line_caps_the_free_amount(self):
        out = run([self.rule()], [line(0, qty="3", price="10", net="5")])  # the cashier already took 25 off
        assert out.total == D("5.00")

    def test_max_discount_caps_it(self):
        assert run([self.rule(max_discount=D("4"))], [line(0, qty="3", price="10")]).total == D("4.00")


class TestConditions:
    def test_minimum_bill_value(self):
        rule = percent(min_cart_value=D("500"))
        assert run([rule], [line(0, price="499.99")]).total == 0
        assert "at least 500.00" in run([rule], [line(0, price="499.99")]).skipped[0].reason
        assert run([rule], [line(0, price="500")]).total == D("50.00")  # exactly the minimum qualifies

    def test_minimum_quantity_of_the_eligible_items(self):
        rule = percent(scope=Scope.PRODUCTS, product_ids=frozenset({1}), min_quantity=D("3"))
        assert run([rule], [line(0, product=1, qty="2"), line(1, product=2, qty="9")]).total == 0
        assert run([rule], [line(0, product=1, qty="3")]).total == D("30.00")

    def test_the_minimum_bill_looks_at_the_bill_before_any_offer(self):
        first = percent(
            1, bp=5000, scope=Scope.PRODUCTS, product_ids=frozenset({1}), stackable=True, priority=9
        )
        cart = percent(2, bp=1000, min_cart_value=D("150"), stackable=True)
        out = run([first, cart], [line(0, product=1, price="100"), line(1, product=2, price="100")])
        assert [a.rule.id for a in out.applied] == [1, 2]  # 200 before offers qualifies, though 150 remains

    def test_max_discount_caps_a_percentage_and_the_lines_shares_follow(self):
        out = run(
            [percent(bp=5000, max_discount=D("30"))], [line(0, price="100"), line(1, product=2, price="300")]
        )
        assert out.total == D("30.00") and sum(out.per_line.values()) == D("30.00")
        assert out.per_line == {0: D("7.50"), 1: D("22.50")}
        assert out.applied[0].terms == "50% off (up to 30.00)"

    def test_the_shares_of_a_capped_offer_never_exceed_a_line(self):
        out = run(
            [amount(value="100", max_discount=D("99.99"))],
            [line(i, product=i, price="33.33") for i in range(3)],
        )
        assert out.total == D("99.99") and all(s <= D("33.33") for s in out.per_line.values())


class TestStacking:
    def test_higher_priority_goes_first_and_an_exclusive_offer_blocks_the_rest(self):
        low, high = percent(1, bp=1000, priority=1), percent(2, bp=2000, priority=5)
        out = run([low, high], [line(0, price="100")])
        assert [a.rule.id for a in out.applied] == [2] and out.total == D("20.00")
        assert out.skipped[0].rule.id == 1 and "combined" in out.skipped[0].reason

    def test_a_tie_goes_to_the_older_offer(self):
        out = run([percent(7, bp=1000), percent(3, bp=2000)], [line(0, price="100")])
        assert [a.rule.id for a in out.applied] == [3]

    def test_the_input_order_does_not_matter(self):
        rules = [percent(i, bp=100 * i, priority=i % 3, stackable=i % 2 == 0) for i in range(1, 8)]
        cart = [line(0, price="90"), line(1, product=2, price="35.50")]
        baseline = run(rules, cart)
        for seed in range(20):
            shuffled = rules[:]
            random.Random(seed).shuffle(shuffled)
            again = run(shuffled, cart)
            assert [a.rule.id for a in again.applied] == [a.rule.id for a in baseline.applied]
            assert again.total == baseline.total and again.per_line == baseline.per_line

    def test_stackable_offers_combine_on_what_is_left(self):
        a, b = (
            percent(1, bp=1000, stackable=True, priority=2),
            percent(2, bp=1000, stackable=True, priority=1),
        )
        out = run([a, b], [line(0, price="100")])
        assert [x.amount for x in out.applied] == [D("10.00"), D("9.00")]  # the second is 10% of 90
        assert out.total == D("19.00")

    def test_an_exclusive_offer_that_comes_after_an_applied_one_is_skipped(self):
        a = percent(1, bp=1000, stackable=True, priority=5)
        b = percent(2, bp=2000, stackable=False, priority=1)
        out = run([a, b], [line(0, price="100")])
        assert [x.rule.id for x in out.applied] == [1]
        assert "cannot be combined" in out.skipped[0].reason

    def test_an_exclusive_offer_that_does_not_apply_does_not_block(self):
        blocked = percent(1, bp=5000, priority=9, min_cart_value=D("1000"))  # not met
        ok = percent(2, bp=1000, priority=1)
        out = run([blocked, ok], [line(0, price="100")])
        assert [x.rule.id for x in out.applied] == [2]

    def test_no_more_than_three_offers_apply(self):
        rules = [percent(i, bp=100, stackable=True, priority=10 - i) for i in range(1, 6)]
        out = run(rules, [line(0, price="1000")])
        assert len(out.applied) == 3 and "At most 3" in out.skipped[0].reason

    def test_item_offers_come_before_bill_offers_whatever_the_priority(self):
        bill = percent(1, bp=1000, stackable=True, priority=100)
        item = percent(
            2, bp=5000, scope=Scope.PRODUCTS, product_ids=frozenset({1}), stackable=True, priority=0
        )
        out = run([bill, item], [line(0, product=1, price="100")])
        assert [x.rule.id for x in out.applied] == [2, 1]
        assert [x.amount for x in out.applied] == [D("50.00"), D("5.00")]  # the bill offer sees the 50 left

    def test_the_same_money_is_never_discounted_twice(self):
        rules = [percent(i, bp=6000, stackable=True, priority=10 - i) for i in (1, 2, 3)]
        out = run(rules, [line(0, price="100")])
        assert out.total <= D("100.00") and sum(out.per_line.values()) == out.total

    def test_the_budget_stops_the_bill_going_below_zero(self):
        out = run([percent(bp=8000)], [line(0, price="100")], budget=D("50"))  # the cashier already took 50
        assert out.total == D("50.00") and out.per_line == {0: D("50.00")}

    def test_a_used_up_budget_skips_the_rest(self):
        a, b = amount(1, "100", stackable=True, priority=2), percent(2, bp=1000, stackable=True, priority=1)
        out = run([a, b], [line(0, price="100")])
        assert out.total == D("100.00") and "Nothing left" in out.skipped[0].reason

    def test_no_offers_no_discount(self):
        out = run([], [line(0)])
        assert out.total == 0 and out.applied == [] and out.skipped == []

    def test_an_empty_cart(self):
        assert run([percent()], []).total == 0


class TestSharing:
    @pytest.mark.parametrize("seed", range(40))
    def test_shares_always_add_up_and_never_exceed_a_line(self, seed):
        rnd = random.Random(seed)
        lines = [
            line(
                i,
                product=i,
                category=i % 3,
                qty=str(rnd.randint(1, 9)),
                price=f"{rnd.randint(1, 50000) / 100:.2f}",
            )
            for i in range(rnd.randint(1, 7))
        ]
        rules = [
            percent(1, bp=rnd.randint(1, 10000), stackable=True, priority=3),
            amount(2, f"{rnd.randint(1, 20000) / 100:.2f}", stackable=True, priority=2),
            Rule(3, "cat", Kind.PERCENT, Scope.CATEGORIES, percent_bp=rnd.randint(1, 10000), stackable=True,
                 category_ids=frozenset({0, 1}), max_discount=D(rnd.randint(1, 500))),
        ]  # fmt: skip
        out = run(rules, lines, budget=sum((x.net for x in lines), D(0)) - D(rnd.randint(0, 5)))
        by_line = out.per_line
        for applied in out.applied:
            assert sum(applied.per_line.values()) == applied.amount
        for item in lines:
            assert by_line.get(item.index, D(0)) <= item.net
        assert sum(by_line.values()) == out.total
        assert out.total <= sum((x.net for x in lines), D(0))

    def test_spread_uses_the_largest_remainder_and_is_exact(self):
        assert pc.spread(100, {0: 1, 1: 1, 2: 1}) == {0: 34, 1: 33, 2: 33}
        assert sum(pc.spread(7, {0: 3, 1: 3, 2: 3, 3: 3}).values()) == 7
        assert pc.spread(0, {0: 5}) == {} and pc.spread(5, {}) == {}

    def test_paise_conversion_round_trips(self):
        for text in ("0.00", "0.01", "12.34", "99999999.99"):
            assert pc.from_paise(pc.to_paise(D(text))) == D(text)


class TestDescribe:
    def test_terms_are_plain_text(self):
        assert pc.describe_terms(percent(bp=1000)) == "10% off"
        assert pc.describe_terms(percent(bp=333)) == "3.33% off"
        assert pc.describe_terms(amount(value="100")) == "100.00 off"

    def test_the_basis_says_why_it_applied(self):
        out = run([percent(scope=Scope.PRODUCTS, product_ids=frozenset({1}))], [line(0, qty="3", price="10")])
        assert "1 eligible item" in out.applied[0].basis and "3 units" in out.applied[0].basis
        assert "eligible amount 30.00" in out.applied[0].basis
        assert run([percent()], [line(0)]).applied[0].basis.startswith("the whole bill")
