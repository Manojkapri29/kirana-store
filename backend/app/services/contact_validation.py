"""Validation and normalisation of contact details (phone, email, GSTIN).

Generic and reusable: suppliers use it now, customers will later. The rules are deliberately lenient. The goal
is to catch typos, not to turn away real details: a valid-looking value is accepted, and what is stored is a
consistent form, which also makes duplicate detection and search reliable.

    phone   any layout people write ("98765 43210", "+91-98765-43210", "(0141) 234 5678") -> "9876543210",
            "+919876543210", "01412345678". 6 to 15 digits, optional leading "+". Devanagari and other
            Unicode digits are accepted and stored as ASCII digits.
    email   one address, no spaces; stored lower-case.
    GSTIN   the 15-character structure (2 digits, 5 letters, 4 digits, letter, entity code, "Z", check
            character), stored upper-case. The check character is NOT verified: a wrong checksum is far more
            likely to reject a genuine number than to catch a typo we could not also catch by eye.
"""

import re
import unicodedata

from app.services.errors import InvalidInputError

_SEPARATORS = re.compile(r"[\s\-().]")
_PHONE = re.compile(r"\+?[0-9]{6,15}")
_EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")
_GSTIN = re.compile(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]")

MAX_EMAIL_LENGTH = 254


def blank_to_none(value: str | None) -> str | None:
    """Empty or whitespace-only text means "not given"."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _ascii_digits(text: str) -> str:
    """Turn any Unicode decimal digit (Devanagari १२३, Arabic-Indic ١٢٣) into 0-9."""
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() else ch for ch in text)


def normalize_phone(value: str | None, *, field: str) -> str | None:
    text = blank_to_none(value)
    if text is None:
        return None
    compact = _SEPARATORS.sub("", _ascii_digits(text))
    if not _PHONE.fullmatch(compact):
        raise InvalidInputError(
            "Enter a valid phone number: 6 to 15 digits, optionally starting with +.", field=field
        )
    return compact


def normalize_email(value: str | None, *, field: str = "email") -> str | None:
    text = blank_to_none(value)
    if text is None:
        return None
    if len(text) > MAX_EMAIL_LENGTH or not _EMAIL.fullmatch(text):
        raise InvalidInputError("Enter a valid email address, for example name@example.com.", field=field)
    return text.lower()


def normalize_gstin(value: str | None, *, field: str = "gstin") -> str | None:
    text = blank_to_none(value)
    if text is None:
        return None
    gstin = re.sub(r"\s", "", text).upper()
    if not _GSTIN.fullmatch(gstin):
        raise InvalidInputError("Enter a valid 15-character GSTIN, for example 27AAPFU0939F1ZV.", field=field)
    return gstin
