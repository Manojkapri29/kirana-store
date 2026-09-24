"""Location: what can be done with no outside service, and the seam for what cannot.

Distance between two known points is plain arithmetic (haversine) and needs no provider and sends nothing anywhere. Turning an address
into coordinates (geocoding) needs a provider; none is bundled, so it reports "Provider Not Configured". A customer's address is never
sent to a provider except by an explicit call to a configured one, and coordinates are not stored on customers by this module.
"""

from decimal import Decimal
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = Decimal("6371.0088")
PROVIDERS: dict[str, type] = {}  # name -> LocationProvider factory. Empty on purpose.


def distance_km(lat1: Decimal, lon1: Decimal, lat2: Decimal, lon2: Decimal) -> Decimal:
    """Great-circle distance in kilometres (a straight line, not a road distance)."""
    for value, limit in ((lat1, 90), (lat2, 90), (lon1, 180), (lon2, 180)):
        if abs(value) > limit:
            raise ValueError("coordinate out of range")
    p1, p2 = radians(float(lat1)), radians(float(lat2))
    a = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(float(lon2) - float(lon1)) / 2) ** 2
    return (EARTH_RADIUS_KM * Decimal(2 * asin(sqrt(a)))).quantize(Decimal("0.01"))
