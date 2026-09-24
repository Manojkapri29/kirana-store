"""Passwords: hashing with Argon2id, and the rules a new password must meet.

Passwords are never stored, logged or returned. What is stored is an Argon2id hash (a salted, memory-hard hash with its own
parameters inside it, so the parameters can be raised later and old hashes are upgraded at the next sign-in).
"""

import secrets
import threading
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings, get_settings
from app.services.errors import InvalidInputError

UNUSABLE = "!"  # a hash value that never verifies: "no password has been set"
MAX_LENGTH = 128
_COMMON = {
    "password", "password1", "password123", "1234567890", "12345678910", "qwertyuiop", "qwerty12345", "iloveyou12",
    "admin12345", "welcome123", "letmein123", "changeme123", "abc1234567", "0123456789", "1q2w3e4r5t", "passw0rd123",
}  # fmt: skip


@lru_cache
def _hasher(time_cost: int, memory_kib: int) -> PasswordHasher:
    return PasswordHasher(time_cost=time_cost, memory_cost=memory_kib, parallelism=1)


def _current(settings: Settings | None = None) -> PasswordHasher:
    settings = settings or get_settings()
    return _hasher(settings.password_hash_time_cost, settings.password_hash_memory_kib)


_gate_lock = threading.Lock()
_gate: tuple[int, threading.BoundedSemaphore] | None = None


def _turnstile(settings: Settings | None = None) -> threading.BoundedSemaphore:
    """At most `password_hash_concurrency` hashes run at once. Each takes ~64 MiB, so a burst of sign-ins queues briefly instead of
    multiplying memory use (12 at once measured 12 x 64 MiB before this)."""
    global _gate  # noqa: PLW0603
    limit = (settings or get_settings()).password_hash_concurrency
    with _gate_lock:
        if _gate is None or _gate[0] != limit:
            _gate = (limit, threading.BoundedSemaphore(limit))
        return _gate[1]


def hash_password(password: str, settings: Settings | None = None) -> str:
    with _turnstile(settings):
        return _current(settings).hash(password)


def verify_password(stored: str, password: str, settings: Settings | None = None) -> bool:
    """True only for the right password. Any problem with the stored value counts as "no"."""
    if not stored or stored == UNUSABLE:
        return False
    try:
        with _turnstile(settings):
            return _current(settings).verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str, settings: Settings | None = None) -> bool:
    try:
        return _current(settings).check_needs_rehash(stored)
    except InvalidHashError:
        return False


@lru_cache
def _dummy_hash(time_cost: int, memory_kib: int) -> str:
    return _hasher(time_cost, memory_kib).hash(secrets.token_urlsafe(16))


def spend_the_same_time(password: str, settings: Settings | None = None) -> None:
    """Verify against a throwaway hash, so an unknown email takes as long to refuse as a known one."""
    settings = settings or get_settings()
    verify_password(
        _dummy_hash(settings.password_hash_time_cost, settings.password_hash_memory_kib), password, settings
    )


def problems_with(password: str, *, email: str | None = None, settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    found: list[str] = []
    if len(password) < settings.password_min_length:
        found.append(f"Use at least {settings.password_min_length} characters.")
    if len(password) > MAX_LENGTH:
        found.append(f"Use at most {MAX_LENGTH} characters.")
    lowered = password.lower()
    if lowered in _COMMON or len(set(password)) <= 2:
        found.append("Choose a password that is harder to guess.")
    if email:
        local = email.split("@", 1)[0].lower()
        if lowered == email.lower() or (len(local) >= 4 and local in lowered):
            found.append("Do not use your email address in your password.")
    return found


def check_new_password(password: str, *, email: str | None = None, settings: Settings | None = None) -> None:
    found = problems_with(password, email=email, settings=settings)
    if found:
        raise InvalidInputError(" ".join(found), field="password")
