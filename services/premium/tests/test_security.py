from __future__ import annotations

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


def test_malformed_argon2_hash_is_not_an_exception() -> None:
    assert not verify_password("$argon2id$malformed", "candidate")
