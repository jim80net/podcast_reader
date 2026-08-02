from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import unicodedata
from collections import OrderedDict, deque
from threading import Lock

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65_536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("not-a-real-account-password")
USER_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def now_epoch() -> int:
    return int(time.time())


def opaque_token() -> str:
    return secrets.token_urlsafe(32)


def record_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_digest(token: str) -> str:
    return token_digest(token)


def user_code() -> str:
    raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def user_code_digest(code: str, pepper: bytes) -> str:
    canonical = code.strip().upper().replace("-", "")
    if len(canonical) != 8 or any(char not in USER_CODE_ALPHABET for char in canonical):
        raise ValueError("invalid user code")
    return hmac.new(pepper, canonical.encode("ascii", "strict"), hashlib.sha256).hexdigest()


def normalize_email(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value.strip()).casefold()
    if len(normalized) > 320 or normalized.count("@") != 1:
        raise ValueError("invalid email address")
    local, domain = normalized.split("@", 1)
    if not local or not domain or "." not in domain or any(char.isspace() for char in normalized):
        raise ValueError("invalid email address")
    return normalized


def validate_password(value: str) -> None:
    if len(value) < 12 or len(value) > 1024:
        raise ValueError("password must contain between 12 and 1024 characters")


def hash_password(value: str) -> str:
    validate_password(value)
    return PASSWORD_HASHER.hash(value)


def verify_password(encoded: str, candidate: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(encoded, candidate)
    except (VerificationError, InvalidHashError):
        return False


class RateLimiter:
    """Small single-process limiter for the explicitly single-worker dev service."""

    def __init__(
        self, *, attempts: int = 8, window_seconds: int = 60, max_keys: int = 4096
    ) -> None:
        if attempts <= 0 or window_seconds <= 0 or max_keys <= 0:
            raise ValueError("rate limiter bounds must be positive")
        self.attempts = attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._events: OrderedDict[str, deque[int]] = OrderedDict()
        self._lock = Lock()

    def allow(self, key: str, at: int | None = None) -> bool:
        timestamp = now_epoch() if at is None else at
        with self._lock:
            events = self._events.get(key)
            if events is not None:
                cutoff = timestamp - self.window_seconds
                while events and events[0] <= cutoff:
                    events.popleft()
                if not events:
                    del self._events[key]
                    events = None
            if events is None:
                if len(self._events) >= self.max_keys:
                    self._events.popitem(last=False)
                events = deque()
                self._events[key] = events
            else:
                self._events.move_to_end(key)
            if len(events) >= self.attempts:
                return False
            events.append(timestamp)
            return True
