"""khata_service: the customer ledger. Sign convention, balance, advance, reversal, isolation, rollback."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models import AuditLog, CustomerLedgerEntry
from app.models.enums import CustomerLedgerEntryType as E
from app.models.enums import KhataReferenceType as Ref
from app.models.enums import PaymentMethod
from app.services import khata_service as khata
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.khata_service import BalanceStatus
from app.services.shop_service import get_shop, shop_today
from tests import factories
from tests.conftest import context_for

D = Decimal


@pytest.fixture
def ctx(tenant_a):
    return context_for(tenant_a)


@pytest.fixture
def customer(session, tenant_a):
    customer = factories.make_customer(session, tenant_a.shop, "Ramesh", phone="9876543210")
    session.commit()
    return customer


def balance(session, tenant, customer) -> Decimal:
    return khata.get_customer_balance(session, tenant.shop.id, customer.id)


def rows(session, tenant, customer, **kwargs):
    return khata.get_customer_ledger(session, tenant.shop.id, customer.id, **kwargs)[0]


def sale(n: int) -> dict:
    return {"reference_type": Ref.SALE, "reference_id": n}


class TestTheBalanceIsTheSumOfTheLedger:
    def test_the_example_from_the_brief(self, session, ctx, tenant_a, customer):
        # opening 1000 + credit sale 500 - payment 300 = 1200 owed
        khata.create_opening_balance(session, ctx, customer.id, D("1000"))
        khata.record_credit_sale(session, ctx, customer.id, D("500"), **sale(1))
        result = khata.record_payment(session, ctx, customer.id, D("300"))

        assert result.account.balance == D("1200.00")
        assert result.account.status is BalanceStatus.OUTSTANDING

        # paying 1500 more leaves 300 in advance: the extra is kept, not lost
        result = khata.record_payment(session, ctx, customer.id, D("1500"))
        assert result.account.balance == D("-300.00")
        assert result.account.status is BalanceStatus.ADVANCE
        assert (result.account.outstanding, result.account.advance) == (D("0.00"), D("300.00"))

    def test_signs_follow_the_convention(self, session, ctx, tenant_a, customer):
        khata.create_opening_balance(session, ctx, customer.id, D("100"))
        khata.record_credit_sale(session, ctx, customer.id, D("50"), **sale(1))
        khata.record_payment(session, ctx, customer.id, D("20"))
        khata.record_return_credit(session, ctx, customer.id, D("10"), reference_id=1)
        khata.record_adjustment(session, ctx, customer.id, D("5"), reason="rounding")
        khata.record_adjustment(session, ctx, customer.id, D("-3"), reason="discount given")

        deltas = {r.entry_type: r.amount_delta for r in rows(session, tenant_a, customer, newest_first=False)}
        assert deltas[E.OPENING_BALANCE] == D("100.00") and deltas[E.CREDIT_SALE] == D("50.00")
        assert deltas[E.PAYMENT] == D("-20.00") and deltas[E.RETURN_CREDIT] == D("-10.00")
        assert balance(session, tenant_a, customer) == D("122.00")

    def test_a_customer_with_no_entries_is_settled_at_zero(self, session, ctx, tenant_a, customer):
        account = khata.get_account(session, tenant_a.shop.id, customer.id)
        assert account.balance == D("0.00") and account.status is BalanceStatus.SETTLED
        assert account.entry_count == 0

    def test_paying_exactly_what_is_owed_settles(self, session, ctx, tenant_a, customer):
        khata.create_opening_balance(session, ctx, customer.id, D("250.50"))
        result = khata.record_payment(session, ctx, customer.id, D("250.50"))
        assert result.account.balance == D("0.00") and result.account.status is BalanceStatus.SETTLED

    def test_an_advance_is_used_up_by_the_next_credit_sale(self, session, ctx, tenant_a, customer):
        khata.record_payment(session, ctx, customer.id, D("1000"))  # nothing owed: pure advance
        assert balance(session, tenant_a, customer) == D("-1000.00")

        result = khata.record_credit_sale(session, ctx, customer.id, D("400"), **sale(7))

        assert result.account.balance == D("-600.00") and result.account.status is BalanceStatus.ADVANCE

    def test_the_balance_is_never_stored_only_summed(self, session, tenant_a):
        from app.models import Customer

        assert not {"balance", "outstanding", "outstanding_balance"} & set(Customer.__table__.columns.keys())

    def test_the_stored_sum_matches_sql(self, session, ctx, tenant_a, customer):
        khata.create_opening_balance(session, ctx, customer.id, D("0.10"))
        khata.record_credit_sale(session, ctx, customer.id, D("0.20"), **sale(1))
        total = session.scalar(select(func.sum(CustomerLedgerEntry.amount_delta)))
        assert total == balance(session, tenant_a, customer) == D("0.30")  # exact: no float drift

    def test_balances_of_many_customers_in_one_call(self, session, ctx, tenant_a, customer):
        other = factories.make_customer(session, tenant_a.shop, "Sita")
        session.commit()
        khata.record_payment(session, ctx, other.id, D("40"))
        khata.create_opening_balance(session, ctx, customer.id, D("15"))

        found = khata.get_balance_map(session, tenant_a.shop.id, [customer.id, other.id, 99999])

        assert found == {customer.id: D("15.00"), other.id: D("-40.00"), 99999: D("0.00")}


class TestAmountValidation:
    @pytest.mark.parametrize("amount", [D("0"), D("-5"), D("0.001"), D("1.005"), D("99999999999")])
    def test_bad_amounts_are_refused(self, session, ctx, customer, amount):
        with pytest.raises(InvalidInputError) as info:
            khata.record_payment(session, ctx, customer.id, amount)
        assert info.value.field == "amount"

    @pytest.mark.parametrize("amount", [0.1, "5", True, None])
    def test_floats_and_text_are_refused(self, session, ctx, customer, amount):
        with pytest.raises(InvalidInputError):
            khata.record_payment(session, ctx, customer.id, amount)

    def test_nothing_is_written_when_the_amount_is_bad(self, session, ctx, tenant_a, customer):
        with pytest.raises(InvalidInputError):
            khata.record_payment(session, ctx, customer.id, D("0"))
        assert rows(session, tenant_a, customer) == []

    def test_a_future_date_is_refused_and_a_past_date_accepted(self, session, ctx, tenant_a, customer):
        today = shop_today(get_shop(session, tenant_a.shop.id))
        with pytest.raises(InvalidInputError) as info:
            khata.record_payment(session, ctx, customer.id, D("1"), entry_date=today + timedelta(days=1))
        assert info.value.field == "entry_date"
        result = khata.record_payment(
            session, ctx, customer.id, D("1"), entry_date=today - timedelta(days=30)
        )
        assert result.entry.entry_date == today - timedelta(days=30)

    def test_the_date_defaults_to_today_in_the_shop_timezone(self, session, ctx, tenant_a, customer):
        result = khata.record_payment(session, ctx, customer.id, D("1"))
        assert result.entry.entry_date == shop_today(get_shop(session, tenant_a.shop.id))


class TestOpeningBalance:
    def test_it_is_a_ledger_entry_not_a_stored_field(self, session, ctx, tenant_a, customer):
        result = khata.create_opening_balance(
            session, ctx, customer.id, D("1000"), note="From the old notebook"
        )

        assert result.entry.entry_type is E.OPENING_BALANCE and result.entry.amount_delta == D("1000.00")
        assert result.entry.note == "From the old notebook" and result.account.entry_count == 1

    def test_it_cannot_be_entered_twice(self, session, ctx, tenant_a, customer):
        khata.create_opening_balance(session, ctx, customer.id, D("1000"))

        with pytest.raises(ConflictError, match="already has an opening balance"):
            khata.create_opening_balance(session, ctx, customer.id, D("1000"))

        assert balance(session, tenant_a, customer) == D("1000.00")  # not 2000

    def test_after_a_reversal_the_right_one_can_be_entered(self, session, ctx, tenant_a, customer):
        first = khata.create_opening_balance(session, ctx, customer.id, D("1000"))
        khata.reverse_entry(session, ctx, first.entry.id, reason="Typed the wrong amount")

        result = khata.create_opening_balance(session, ctx, customer.id, D("100"))

        assert result.account.balance == D("100.00")

    def test_each_customer_has_their_own(self, session, ctx, tenant_a, customer):
        other = factories.make_customer(session, tenant_a.shop, "Sita")
        session.commit()
        khata.create_opening_balance(session, ctx, customer.id, D("5"))
        assert khata.create_opening_balance(session, ctx, other.id, D("7")).account.balance == D("7.00")

    def test_it_must_be_positive(self, session, ctx, customer):
        with pytest.raises(InvalidInputError):
            khata.create_opening_balance(session, ctx, customer.id, D("-1"))


class TestPayments:
    def test_a_partial_payment_with_details(self, session, ctx, tenant_a, customer):
        khata.create_opening_balance(session, ctx, customer.id, D("5000"))

        result = khata.record_payment(
            session,
            ctx,
            customer.id,
            D("2000"),
            payment_method=PaymentMethod.UPI,
            payment_reference="  UPI-123456  ",
            note="  Paid at the counter ",
        )

        assert result.account.balance == D("3000.00")
        assert result.entry.amount_delta == D("-2000.00") and result.entry.entry_type is E.PAYMENT
        assert (result.entry.payment_method, result.entry.payment_reference) == (
            PaymentMethod.UPI,
            "UPI-123456",
        )
        assert result.entry.note == "Paid at the counter"

    def test_method_and_reference_are_optional(self, session, ctx, customer):
        entry = khata.record_payment(session, ctx, customer.id, D("1")).entry
        assert entry.payment_method is None and entry.payment_reference is None

    def test_an_inactive_customer_can_still_pay(self, session, ctx, tenant_a, customer):
        khata.create_opening_balance(session, ctx, customer.id, D("50"))
        customer.is_active = False
        session.flush()

        assert khata.record_payment(session, ctx, customer.id, D("50")).account.balance == D("0.00")

    def test_an_over_long_reference_is_refused(self, session, ctx, customer):
        with pytest.raises(InvalidInputError):
            khata.record_payment(session, ctx, customer.id, D("1"), payment_reference="x" * 101)


class TestCreditSaleFoundation:
    def test_it_increases_the_outstanding_and_keeps_the_reference(self, session, ctx, tenant_a, customer):
        result = khata.record_credit_sale(session, ctx, customer.id, D("500"), **sale(42), note="Rice 25kg")

        assert result.account.balance == D("500.00") and result.account.status is BalanceStatus.OUTSTANDING
        assert (result.entry.reference_type, result.entry.reference_id) == (Ref.SALE, 42)

    def test_the_same_sale_cannot_be_recorded_twice(self, session, ctx, tenant_a, customer):
        khata.record_credit_sale(session, ctx, customer.id, D("500"), **sale(42))

        with pytest.raises(ConflictError, match="already been added"):
            khata.record_credit_sale(session, ctx, customer.id, D("500"), **sale(42))

        assert balance(session, tenant_a, customer) == D("500.00")

    def test_a_reference_is_required_and_must_be_a_sale_type(self, session, ctx, customer):
        for bad in (
            {"reference_type": Ref.SALES_RETURN, "reference_id": 1},
            {"reference_type": Ref.SALE, "reference_id": 0},
        ):
            with pytest.raises(InvalidInputError):
                khata.record_credit_sale(session, ctx, customer.id, D("1"), **bad)

    def test_a_quick_sale_reference_is_also_accepted(self, session, ctx, customer):
        khata.record_credit_sale(
            session, ctx, customer.id, D("1"), reference_type=Ref.QUICK_SALE, reference_id=1
        )

    def test_an_inactive_customer_gets_no_new_credit(self, session, ctx, tenant_a, customer):
        customer.is_active = False
        session.flush()
        with pytest.raises(ConflictError, match="inactive"):
            khata.record_credit_sale(session, ctx, customer.id, D("1"), **sale(1))

    def test_no_sale_document_is_needed_or_created(self, session, ctx, customer):
        khata.record_credit_sale(session, ctx, customer.id, D("1"), **sale(999999))  # no such sale exists yet
        assert session.scalar(text("SELECT count(*) FROM sales")) == 0

    @pytest.mark.parametrize("business_type", ["GROCERY", "SWEET_SHOP", "GARMENTS", "ELECTRONICS", "OTHER"])
    def test_the_same_engine_serves_every_kind_of_shop(self, session, tenant_of, business_type):
        tenant = tenant_of(business_type)
        customer = factories.make_customer(session, tenant.shop, "Buyer")
        session.commit()
        ctx = context_for(tenant)

        khata.record_credit_sale(session, ctx, customer.id, D("120"), **sale(1))
        result = khata.record_payment(session, ctx, customer.id, D("20"))

        assert result.account.balance == D("100.00")


class TestReturnCreditFoundation:
    def test_it_decreases_the_outstanding(self, session, ctx, tenant_a, customer):
        khata.record_credit_sale(session, ctx, customer.id, D("500"), **sale(1))

        result = khata.record_return_credit(session, ctx, customer.id, D("120"), reference_id=9)

        assert result.account.balance == D("380.00") and result.entry.amount_delta == D("-120.00")
        assert (result.entry.reference_type, result.entry.reference_id) == (Ref.SALES_RETURN, 9)

    def test_it_may_take_the_balance_below_zero(self, session, ctx, customer):
        result = khata.record_return_credit(session, ctx, customer.id, D("75"), reference_id=1)
        assert result.account.status is BalanceStatus.ADVANCE and result.account.advance == D("75.00")

    def test_the_same_return_cannot_be_credited_twice(self, session, ctx, customer):
        khata.record_return_credit(session, ctx, customer.id, D("10"), reference_id=1)
        with pytest.raises(ConflictError, match="already been credited"):
            khata.record_return_credit(session, ctx, customer.id, D("10"), reference_id=1)

    def test_a_return_and_a_sale_may_share_a_number(self, session, ctx, tenant_a, customer):
        khata.record_credit_sale(session, ctx, customer.id, D("10"), **sale(1))
        khata.record_return_credit(session, ctx, customer.id, D("4"), reference_id=1)
        assert balance(session, tenant_a, customer) == D("6.00")


class TestAdjustments:
    def test_a_positive_adjustment_increases_what_is_owed(self, session, ctx, customer):
        result = khata.record_adjustment(
            session, ctx, customer.id, D("25.50"), reason="Delivery charge missed"
        )
        assert result.account.balance == D("25.50") and result.entry.note == "Delivery charge missed"

    def test_a_negative_adjustment_decreases_it(self, session, ctx, customer):
        khata.create_opening_balance(session, ctx, customer.id, D("100"))
        result = khata.record_adjustment(session, ctx, customer.id, D("-30"), reason="Settlement discount")
        assert result.account.balance == D("70.00") and result.entry.amount_delta == D("-30.00")

    @pytest.mark.parametrize("reason", [None, "", "   "])
    def test_a_reason_is_required(self, session, ctx, tenant_a, customer, reason):
        with pytest.raises(InvalidInputError) as info:
            khata.record_adjustment(session, ctx, customer.id, D("5"), reason=reason)
        assert info.value.field == "reason"
        assert rows(session, tenant_a, customer) == []

    def test_zero_is_refused(self, session, ctx, customer):
        with pytest.raises(InvalidInputError):
            khata.record_adjustment(session, ctx, customer.id, D("0"), reason="nothing")

    def test_it_is_permanent_like_every_entry(self, session, ctx, tenant_a, customer):
        entry = khata.record_adjustment(session, ctx, customer.id, D("5"), reason="x").entry
        with pytest.raises(Exception, match="insert-only"):
            session.execute(
                text("UPDATE customer_ledger SET amount_delta = 0 WHERE id = :i"), {"i": entry.id}
            )
        session.rollback()


class TestReversal:
    @pytest.fixture
    def credit(self, session, ctx, customer):
        return khata.record_credit_sale(session, ctx, customer.id, D("500"), **sale(1)).entry

    def test_the_original_stays_and_the_reversal_offsets_it(self, session, ctx, tenant_a, customer, credit):
        result = khata.reverse_entry(
            session, ctx, credit.id, reason="Sale cancelled", allow_document_entries=True
        )

        history = rows(session, tenant_a, customer, newest_first=False)
        assert [(r.entry_type, r.amount_delta) for r in history] == [
            (E.CREDIT_SALE, D("500.00")),
            (E.REVERSAL, D("-500.00")),
        ]
        assert result.account.balance == D("0.00") and result.entry.reverses_entry_id == credit.id
        assert history[0].reversed_by_entry_id == result.entry.id  # the original knows it was undone
        assert history[0].amount_delta == D("500.00")  # and is unchanged

    def test_a_payment_reversal_puts_the_debt_back(self, session, ctx, customer):
        khata.create_opening_balance(session, ctx, customer.id, D("300"))
        payment = khata.record_payment(session, ctx, customer.id, D("300")).entry

        result = khata.reverse_entry(session, ctx, payment.id, reason="Cheque bounced")

        assert result.entry.amount_delta == D("300.00") and result.account.balance == D("300.00")

    def test_it_cannot_be_reversed_twice(self, session, ctx, tenant_a, customer):
        payment = khata.record_payment(session, ctx, customer.id, D("50")).entry
        khata.reverse_entry(session, ctx, payment.id, reason="Mistake")

        with pytest.raises(ConflictError, match="already been reversed"):
            khata.reverse_entry(session, ctx, payment.id, reason="Again")

        assert balance(session, tenant_a, customer) == D("0.00")  # one reversal only

    def test_a_reversal_cannot_be_reversed(self, session, ctx, customer):
        payment = khata.record_payment(session, ctx, customer.id, D("50")).entry
        reversal = khata.reverse_entry(session, ctx, payment.id, reason="Mistake").entry

        with pytest.raises(ConflictError, match="cannot be reversed"):
            khata.reverse_entry(session, ctx, reversal.id, reason="Undo the undo")

    def test_document_entries_need_the_document_flag(self, session, ctx, tenant_a, customer, credit):
        with pytest.raises(ConflictError, match="came from a sale"):
            khata.reverse_entry(session, ctx, credit.id, reason="By hand")
        assert balance(session, tenant_a, customer) == D("500.00")

        return_credit = khata.record_return_credit(session, ctx, customer.id, D("50"), reference_id=3).entry
        with pytest.raises(ConflictError, match="came from a sale"):
            khata.reverse_entry(session, ctx, return_credit.id, reason="By hand")

    @pytest.mark.parametrize("reason", [None, "", "  "])
    def test_a_reason_is_required(self, session, ctx, credit, reason):
        with pytest.raises(InvalidInputError) as info:
            khata.reverse_entry(session, ctx, credit.id, reason=reason, allow_document_entries=True)
        assert info.value.field == "reason"

    def test_the_customer_guard_rejects_someone_elses_entry(self, session, ctx, tenant_a, customer, credit):
        other = factories.make_customer(session, tenant_a.shop, "Sita")
        session.commit()
        with pytest.raises(NotFoundError):
            khata.reverse_entry(
                session, ctx, credit.id, reason="x", customer_id=other.id, allow_document_entries=True
            )

    def test_an_unknown_entry_is_not_found(self, session, ctx):
        with pytest.raises(NotFoundError):
            khata.reverse_entry(session, ctx, 99999, reason="x")

    def test_the_database_also_refuses_a_second_reversal(self, session, ctx, tenant_a, customer):
        from tests.conftest import assert_rejected

        payment = khata.record_payment(session, ctx, customer.id, D("50")).entry
        khata.reverse_entry(session, ctx, payment.id, reason="Mistake")
        session.commit()
        duplicate = CustomerLedgerEntry(
            shop_id=tenant_a.shop.id, customer_id=customer.id, entry_date=payment.entry_date,
            entry_type=E.REVERSAL, amount_delta=D("50"), reverses_entry_id=payment.id, created_by=tenant_a.user.id,
        )  # fmt: skip
        assert_rejected(session, duplicate, match="uq_customer_ledger_reverses|UNIQUE")


class TestLedgerHistory:
    def test_running_balance_follows_date_order(self, session, ctx, tenant_a, customer):
        today = shop_today(get_shop(session, tenant_a.shop.id))
        khata.create_opening_balance(
            session, ctx, customer.id, D("1000"), entry_date=today - timedelta(days=10)
        )
        khata.record_credit_sale(
            session, ctx, customer.id, D("500"), **sale(1), entry_date=today - timedelta(days=5)
        )
        khata.record_payment(session, ctx, customer.id, D("300"), entry_date=today - timedelta(days=2))

        history = rows(session, tenant_a, customer, newest_first=False)

        assert [r.balance_after for r in history] == [D("1000.00"), D("1500.00"), D("1200.00")]
        newest = rows(session, tenant_a, customer)
        assert [r.entry_type for r in newest] == [E.PAYMENT, E.CREDIT_SALE, E.OPENING_BALANCE]
        assert newest[0].balance_after == balance(session, tenant_a, customer)

    def test_a_backdated_entry_slots_into_history_and_changes_later_balances(
        self, session, ctx, tenant_a, customer
    ):
        today = shop_today(get_shop(session, tenant_a.shop.id))
        khata.record_credit_sale(session, ctx, customer.id, D("100"), **sale(1), entry_date=today)
        khata.record_payment(session, ctx, customer.id, D("40"), entry_date=today - timedelta(days=3))

        history = rows(session, tenant_a, customer, newest_first=False)

        assert [(r.entry_type, r.balance_after) for r in history] == [
            (E.PAYMENT, D("-40.00")),
            (E.CREDIT_SALE, D("60.00")),
        ]

    def test_filters_narrow_the_rows_but_not_the_running_balance(self, session, ctx, tenant_a, customer):
        khata.create_opening_balance(session, ctx, customer.id, D("100"))
        khata.record_payment(session, ctx, customer.id, D("30"))
        khata.record_payment(session, ctx, customer.id, D("20"))

        only_payments, total = khata.get_customer_ledger(
            session, tenant_a.shop.id, customer.id, entry_type=E.PAYMENT, newest_first=False
        )

        assert total == 2 and [r.balance_after for r in only_payments] == [D("70.00"), D("50.00")]

    def test_date_range_and_pagination(self, session, ctx, tenant_a, customer):
        today = shop_today(get_shop(session, tenant_a.shop.id))
        for days in (9, 6, 3, 0):
            khata.record_payment(session, ctx, customer.id, D("1"), entry_date=today - timedelta(days=days))

        in_range, total = khata.get_customer_ledger(
            session,
            tenant_a.shop.id,
            customer.id,
            date_from=today - timedelta(days=6),
            date_to=today - timedelta(days=3),
        )
        page, all_total = khata.get_customer_ledger(session, tenant_a.shop.id, customer.id, limit=2, offset=1)

        assert total == 2 and len(in_range) == 2
        assert all_total == 4 and len(page) == 2

    def test_rows_say_who_recorded_them(self, session, ctx, tenant_a, customer):
        khata.record_payment(session, ctx, customer.id, D("1"))
        assert rows(session, tenant_a, customer)[0].created_by_name == "Test Owner"

    def test_entries_are_audited(self, session, ctx, tenant_a, customer):
        entry = khata.record_payment(session, ctx, customer.id, D("50")).entry

        log = session.scalars(select(AuditLog).where(AuditLog.action == "khata_payment")).one()

        assert log.entity_type == "customer" and log.entity_id == customer.id
        assert log.after_json["entry_id"] == entry.id and log.after_json["amount_delta"] == "-50.00"


class TestShopIsolation:
    @pytest.fixture
    def theirs(self, tenant_b):
        return context_for(tenant_b)

    def test_every_operation_treats_another_shops_customer_as_not_found(
        self, session, ctx, tenant_b, customer
    ):
        theirs = context_for(tenant_b)
        operations = [
            lambda: khata.create_opening_balance(session, theirs, customer.id, D("1")),
            lambda: khata.record_payment(session, theirs, customer.id, D("1")),
            lambda: khata.record_adjustment(session, theirs, customer.id, D("1"), reason="x"),
            lambda: khata.record_credit_sale(session, theirs, customer.id, D("1"), **sale(1)),
            lambda: khata.record_return_credit(session, theirs, customer.id, D("1"), reference_id=1),
            lambda: khata.get_customer_balance(session, tenant_b.shop.id, customer.id),
            lambda: khata.get_customer_ledger(session, tenant_b.shop.id, customer.id),
            lambda: khata.get_account(session, tenant_b.shop.id, customer.id),
        ]
        for operation in operations:
            with pytest.raises(NotFoundError):
                operation()
        assert session.scalar(select(func.count()).select_from(CustomerLedgerEntry)) == 0

    def test_another_shops_entry_cannot_be_reversed(self, session, ctx, tenant_b, customer):
        payment = khata.record_payment(session, ctx, customer.id, D("5")).entry
        with pytest.raises(NotFoundError):
            khata.reverse_entry(session, context_for(tenant_b), payment.id, reason="x")

    def test_lists_and_balances_never_mix_shops(self, session, ctx, tenant_a, tenant_b, customer):
        theirs = factories.make_customer(session, tenant_b.shop, "Ramesh", phone="9876543210")  # same phone
        session.commit()
        khata.record_payment(session, ctx, customer.id, D("10"))
        khata.create_opening_balance(session, context_for(tenant_b), theirs.id, D("999"))

        mine, total = khata.list_accounts(session, tenant_a.shop.id)

        assert total == 1 and mine[0].balance == D("-10.00")
        assert khata.get_customer_balance(session, tenant_b.shop.id, theirs.id) == D("999.00")


class TestListAccounts:
    @pytest.fixture
    def three(self, session, ctx, tenant_a):
        owes = factories.make_customer(session, tenant_a.shop, "Asha", phone="9000000001")
        settled = factories.make_customer(session, tenant_a.shop, "Bina", phone="9000000002")
        ahead = factories.make_customer(session, tenant_a.shop, "Chitra", phone="9000000003")
        gone = factories.make_customer(session, tenant_a.shop, "Dev")
        gone.is_active = False
        session.commit()
        khata.create_opening_balance(session, ctx, owes.id, D("500"))
        khata.create_opening_balance(session, ctx, settled.id, D("50"))
        khata.record_payment(session, ctx, settled.id, D("50"))
        khata.record_payment(session, ctx, ahead.id, D("80"))
        return owes, settled, ahead, gone

    def names(self, session, tenant, **kwargs):
        return [a.customer.name for a in khata.list_accounts(session, tenant.shop.id, **kwargs)[0]]

    def test_balance_filters(self, session, tenant_a, three):
        assert self.names(session, tenant_a, balance=BalanceStatus.OUTSTANDING) == ["Asha"]
        assert self.names(session, tenant_a, balance=BalanceStatus.SETTLED) == ["Bina"]
        assert self.names(session, tenant_a, balance=BalanceStatus.ADVANCE) == ["Chitra"]

    def test_inactive_customers_are_hidden_by_default(self, session, tenant_a, three):
        assert self.names(session, tenant_a) == ["Asha", "Bina", "Chitra"]
        assert self.names(session, tenant_a, active=False) == ["Dev"]
        assert self.names(session, tenant_a, active=None) == ["Asha", "Bina", "Chitra", "Dev"]

    def test_biggest_dues_first(self, session, tenant_a, three):
        assert self.names(session, tenant_a, biggest_first=True) == ["Asha", "Bina", "Chitra"]  # 500, 0, -80

    def test_search_and_balance_together_and_the_total_counts_the_filter(self, session, tenant_a, three):
        found, total = khata.list_accounts(
            session, tenant_a.shop.id, q="9000000001", balance=BalanceStatus.OUTSTANDING
        )
        assert total == 1 and found[0].balance == D("500.00")

    def test_pagination(self, session, tenant_a, three):
        page, total = khata.list_accounts(session, tenant_a.shop.id, limit=2, offset=2)
        assert total == 3 and [a.customer.name for a in page] == ["Chitra"]


class TestAtomicity:
    def test_a_failure_after_the_entry_is_written_rolls_the_entry_back(
        self, session_factory, tenant_a, customer, monkeypatch
    ):
        ctx = context_for(tenant_a)

        def fail(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(khata, "record_audit", fail)  # the entry is flushed, then this blows up

        with pytest.raises(RuntimeError, match="disk full"), session_factory() as s, s.begin():
            khata.record_payment(s, ctx, customer.id, D("100"))

        with session_factory() as s:
            assert s.scalar(select(func.count()).select_from(CustomerLedgerEntry)) == 0
            assert khata.get_customer_balance(s, tenant_a.shop.id, customer.id) == D("0.00")

    def test_a_reversal_that_fails_leaves_the_original_effect_alone(
        self, session_factory, tenant_a, customer, monkeypatch
    ):
        ctx = context_for(tenant_a)
        with session_factory() as s, s.begin():
            payment = khata.record_payment(s, ctx, customer.id, D("100")).entry
            payment_id = payment.id
        monkeypatch.setattr(khata, "record_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))

        with pytest.raises(RuntimeError), session_factory() as s, s.begin():
            khata.reverse_entry(s, ctx, payment_id, reason="test")

        with session_factory() as s:
            assert khata.get_customer_balance(s, tenant_a.shop.id, customer.id) == D("-100.00")
            assert s.scalar(select(func.count()).select_from(CustomerLedgerEntry)) == 1

    def test_a_customer_and_their_opening_balance_are_created_together_or_not_at_all(
        self, session_factory, tenant_a
    ):
        ctx = context_for(tenant_a)

        with pytest.raises(InvalidInputError), session_factory() as s, s.begin():
            khata.create_customer_with_opening_balance(s, ctx, {"name": "New"}, opening_balance=D("0"))

        with session_factory() as s:
            from app.models import Customer

            assert s.scalar(select(func.count()).select_from(Customer)) == 0
