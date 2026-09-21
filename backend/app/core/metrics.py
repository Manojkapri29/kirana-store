"""Application metrics, kept in memory and exposed in the Prometheus text format when the operator turns them on.

Nothing here needs a monitoring product: it is a few counters and histograms. Labels are bounded (a method, a route
TEMPLATE such as `/api/v1/sales/{id}`, a status class) and never carry a person, a shop, an email, a token or any business
value, so the metrics contain no personal data. The counters live in this process (with several workers each has its own; a
scraper reads them all, as Prometheus does). If you use OpenTelemetry or a hosted product, feed it from the same call sites.
"""

import re
import threading
from collections import defaultdict

BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_ID = re.compile(r"/\d+(?=/|$)")

HELP = {
    "kirana_http_requests_total": "HTTP requests by method, route template and status class",
    "kirana_http_request_duration_seconds": "HTTP request duration in seconds",
    "kirana_db_errors_total": "Database errors returned to callers",
    "kirana_external_api_errors_total": "Failures of outside services (prices, barcodes, AI providers)",
    "kirana_ai_failures_total": "AI provider failures",
    "kirana_notification_failures_total": "Notification deliveries that could not be sent",
    "kirana_job_runs_total": "Background job runs by type and outcome",
    "kirana_auth_events_total": "Sign-in related events",
    "kirana_system_events_total": "Platform events by category and severity",
}


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._hist: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = {}

    def inc(self, name: str, amount: float = 1.0, **labels: str) -> None:
        with self._lock:
            self._counters[(name, tuple(sorted(labels.items())))] += amount

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            row = self._hist.setdefault(key, [0.0] * (len(BUCKETS) + 2))  # buckets..., +Inf, sum
            for i, bound in enumerate(BUCKETS):
                if value <= bound:
                    row[i] += 1
            row[len(BUCKETS)] += 1  # +Inf (also the count)
            row[len(BUCKETS) + 1] += value

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._hist.clear()
        _KNOWN.clear()

    def value(self, name: str, **labels: str) -> float:
        with self._lock:
            return self._counters.get((name, tuple(sorted(labels.items()))), 0.0)

    def total(self, name: str) -> float:
        with self._lock:
            return sum(v for (n, _), v in self._counters.items() if n == name)

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            names = sorted({n for n, _ in self._counters} | {n for n, _ in self._hist})
            for name in names:
                lines.append(f"# HELP {name} {HELP.get(name, name)}")
                lines.append(
                    f"# TYPE {name} {'histogram' if any(n == name for n, _ in self._hist) else 'counter'}"
                )
                for (n, labels), v in sorted(self._counters.items()):
                    if n == name:
                        lines.append(f"{n}{_fmt(labels)} {v:g}")
                for (n, labels), row in sorted(self._hist.items()):
                    if n != name:
                        continue
                    for i, bound in enumerate(BUCKETS):
                        lines.append(f"{n}_bucket{_fmt(labels + (('le', f'{bound:g}'),))} {row[i]:g}")
                    lines.append(f"{n}_bucket{_fmt(labels + (('le', '+Inf'),))} {row[len(BUCKETS)]:g}")
                    lines.append(f"{n}_sum{_fmt(labels)} {row[len(BUCKETS) + 1]:g}")
                    lines.append(f"{n}_count{_fmt(labels)} {row[len(BUCKETS)]:g}")
        return "\n".join(lines) + "\n"


def _fmt(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in labels) + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


registry = Registry()
inc = registry.inc
observe = registry.observe


_KNOWN: set[str] = set()
_MAX_ROUTES = 300  # a bound on the label set: a scanner inventing paths cannot grow memory without limit
_PREFIXES = ("/api/v1/", "/health", "/metrics")


def route_label(path: str, status: int) -> str:
    """A bounded label: record numbers masked; paths outside the API, and any beyond the bound, share one label."""
    label = _ID.sub("/{id}", path)[:80]
    if not label.startswith(_PREFIXES):
        return "other"
    if label not in _KNOWN:
        if len(_KNOWN) >= _MAX_ROUTES:
            return "other"
        _KNOWN.add(label)
    return label


def record_request(method: str, path: str, status: int, seconds: float) -> None:
    route = route_label(path, status)
    inc("kirana_http_requests_total", method=method, route=route, status=f"{status // 100}xx")
    observe("kirana_http_request_duration_seconds", seconds, method=method, route=route)


def record_system_event(category: str, severity: str) -> None:
    inc("kirana_system_events_total", category=category, severity=severity)
    if category == "integration":
        inc("kirana_external_api_errors_total")
    elif category == "ai":
        inc("kirana_ai_failures_total")
    elif category == "notification":
        inc("kirana_notification_failures_total")


def record_db_error() -> None:
    inc("kirana_db_errors_total")


def record_auth_event(event: str) -> None:
    inc("kirana_auth_events_total", event=event)
