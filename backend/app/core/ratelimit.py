"""Rate limiting: a sliding window per key, kept in memory.

A key is a shop and user (for signed-in routes) or a client address (for public ones). Limits are settings, so an
operator can tune them per environment; the defaults are far above what a person working at a till produces.

This is per PROCESS. With several worker processes each has its own counters, so a shared store (Redis) is the next step
when the application runs on more than one (documented in docs/PRODUCTION.md). It protects against a runaway script or a
misbehaving client, and against cost abuse of the paid endpoints (AI, photos, outside lookups); it is not a substitute for
a network-level limit in front of the application.
"""

import threading
import time
from collections import deque

from app.core.config import Settings, get_settings
from app.services.errors import RateLimitedError

GROUP_SETTINGS = {
    "ai": "rate_limit_ai",
    "image": "rate_limit_image",
    "external": "rate_limit_external",
    "coupon": "rate_limit_coupon",
    "export": "rate_limit_export",
    "admin": "rate_limit_admin",
    "admin_auth": "rate_limit_admin_auth_failures",
    "auth": "rate_limit_auth",
    "public": "rate_limit_public",
    "webhook": "rate_limit_webhook",
    "store_order": "rate_limit_store_order",
    "sync": "rate_limit_sync",
    "message": "rate_limit_message",
    "payment": "rate_limit_payment",
}
_MAX_KEYS = 50_000  # a bound on memory: when exceeded, idle keys are dropped


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: float, now: float | None = None) -> float | None:
        """Record one call. Returns None if allowed, else the seconds to wait before the next call would be."""
        current = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= current - window:
                hits.popleft()
            if len(hits) >= limit:
                return max(0.1, hits[0] + window - current)
            hits.append(current)
            if len(self._hits) > _MAX_KEYS:
                for stale in [k for k, v in self._hits.items() if not v or v[-1] <= current - window]:
                    del self._hits[stale]
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def keys(self) -> int:
        with self._lock:
            return len(self._hits)


limiter = SlidingWindowLimiter()


def enforce(group: str, key: str, settings: Settings | None = None) -> None:
    """Raise `RateLimitedError` (HTTP 429 with Retry-After) if this key has used up its calls for the group."""
    settings = settings or get_settings()
    if not settings.rate_limit_enabled:
        return
    limit = getattr(settings, GROUP_SETTINGS[group])
    wait = limiter.hit(f"{group}:{key}", limit, settings.rate_limit_window_seconds)
    if wait is not None:
        raise RateLimitedError(retry_after=int(wait) + 1, group=group)
