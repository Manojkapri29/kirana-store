"""How numbers are written in answers: rupees with Indian digit grouping, quantities without trailing zeros.

Pure text formatting. The numbers themselves always come from the database and are never changed here.
"""

from decimal import Decimal


def money(value: Decimal | None) -> str:
    """₹1,23,456.50. A missing value is "Not Available", never zero."""
    if value is None:
        return "Not Available"
    sign = "-" if value < 0 else ""
    whole, _, fraction = f"{abs(value):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}₹{whole}.{fraction}"


def quantity(value: Decimal) -> str:
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def percent_change(current: Decimal, previous: Decimal) -> Decimal | None:
    """(current - previous) / previous as a percentage with one decimal, or None when there is nothing to compare."""
    if previous == 0:
        return None
    return ((current - previous) * 100 / previous).quantize(Decimal("0.1"))
