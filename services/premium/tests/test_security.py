from __future__ import annotations

import pytest

from podcast_reader_premium.security import RateLimiter, verify_password


def test_rate_limiter_bounds_and_evicts_key_store() -> None:
    limiter = RateLimiter(attempts=2, window_seconds=5, max_keys=2)
    assert limiter.allow("first", at=0)
    assert limiter.allow("second", at=0)
    assert limiter.allow("third", at=0)
    assert list(limiter._events) == ["second", "third"]
    assert limiter.allow("second", at=10)
    assert list(limiter._events) == ["third", "second"]
    assert len(limiter._events) == 2


@pytest.mark.parametrize(
    "malformed_hash",
    ["$argon2id$malformed", "not-a-hash", "$2b$12$bcryptstylehash", ""],
)
def test_malformed_password_hash_is_not_an_exception(malformed_hash: str) -> None:
    assert not verify_password(malformed_hash, "candidate")
