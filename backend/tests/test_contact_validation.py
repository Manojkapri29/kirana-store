"""Phone, email and GSTIN handling: lenient on layout, strict enough to catch typos, and consistent on output."""

import pytest

from app.services.contact_validation import (
    blank_to_none,
    normalize_email,
    normalize_gstin,
    normalize_phone,
)
from app.services.errors import InvalidInputError


class TestBlankToNone:
    @pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
    def test_nothing_means_not_given(self, value):
        assert blank_to_none(value) is None

    def test_text_is_trimmed(self):
        assert blank_to_none("  hello ") == "hello"


class TestPhone:
    @pytest.mark.parametrize(
        ("typed", "stored"),
        [
            ("9876543210", "9876543210"),
            ("98765 43210", "9876543210"),
            ("98765-43210", "9876543210"),
            ("+91 98765 43210", "+919876543210"),
            ("+91-98765-43210", "+919876543210"),
            ("(0141) 234 5678", "01412345678"),
            ("0141.234.5678", "01412345678"),
            ("  9876543210  ", "9876543210"),
            ("123456", "123456"),  # short local numbers exist
            ("+" + "1" * 15, "+" + "1" * 15),  # the maximum length
            ("९८७६५४३२१०", "9876543210"),  # Devanagari digits become ASCII
            ("٩٨٧٦٥٤٣٢١٠", "9876543210"),  # Arabic-Indic digits too
        ],
    )
    def test_any_reasonable_layout_is_accepted_and_stored_compactly(self, typed, stored):
        assert normalize_phone(typed, field="phone") == stored

    @pytest.mark.parametrize("nothing", [None, "", "   "])
    def test_optional(self, nothing):
        assert normalize_phone(nothing, field="phone") is None

    @pytest.mark.parametrize(
        "bad",
        [
            "abc",
            "12345",
            "1" * 16,
            "98-abc-3210",
            "9876 5432 1O",
            "++919876543210",
            "98765+43210",
            "12 34",
            "#123456",
        ],
    )
    def test_things_that_are_not_phone_numbers_are_refused_naming_the_field(self, bad):
        with pytest.raises(InvalidInputError) as caught:
            normalize_phone(bad, field="alternate_phone")

        assert caught.value.field == "alternate_phone" and "valid phone number" in caught.value.message


class TestEmail:
    @pytest.mark.parametrize(
        ("typed", "stored"),
        [
            ("orders@example.com", "orders@example.com"),
            ("  Orders@Example.COM ", "orders@example.com"),
            ("first.last+tag@sub.example.co.in", "first.last+tag@sub.example.co.in"),
        ],
    )
    def test_valid_addresses_are_lower_cased(self, typed, stored):
        assert normalize_email(typed) == stored

    @pytest.mark.parametrize(
        "bad",
        [
            "plain",
            "a@b",
            "a b@example.com",
            "a@b .com",
            "a@@b.com",
            "a@b.com, c@d.com",
            "@x.com",
            "<a@b.com>",
        ],
    )
    def test_invalid_addresses_are_refused(self, bad):
        with pytest.raises(InvalidInputError) as caught:
            normalize_email(bad)

        assert caught.value.field == "email"

    def test_optional_and_length_limited(self):
        assert normalize_email("") is None and normalize_email(None) is None
        with pytest.raises(InvalidInputError):
            normalize_email("a" * 250 + "@example.com")


class TestGstin:
    @pytest.mark.parametrize(
        ("typed", "stored"),
        [
            ("27AAPFU0939F1ZV", "27AAPFU0939F1ZV"),
            ("27aapfu0939f1zv", "27AAPFU0939F1ZV"),
            (" 27AAPFU 0939F1ZV ", "27AAPFU0939F1ZV"),
            ("29AAGCB7383J1Z4", "29AAGCB7383J1Z4"),
        ],
    )
    def test_valid_structure_is_accepted_and_upper_cased(self, typed, stored):
        assert normalize_gstin(typed) == stored

    def test_the_check_character_is_deliberately_not_verified(self):
        """Documented choice: never reject a genuine number because of an over-strict checksum."""
        assert normalize_gstin("27AAPFU0939F1Z0") == "27AAPFU0939F1Z0"

    @pytest.mark.parametrize(
        "bad",
        [
            "123",
            "27AAPFU0939F1Z",
            "27AAPFU0939F1ZVV",
            "XXAAPFU0939F1ZV",
            "27AAPFU0939F1AV",
            "27AAPFU09391FZV",
            "27-AAPFU0939F1ZV",
        ],
    )
    def test_wrong_structure_is_refused(self, bad):
        with pytest.raises(InvalidInputError) as caught:
            normalize_gstin(bad)

        assert caught.value.field == "gstin" and "GSTIN" in caught.value.message

    def test_optional(self):
        assert normalize_gstin("") is None and normalize_gstin(None) is None
