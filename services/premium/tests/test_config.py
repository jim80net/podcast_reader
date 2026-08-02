from __future__ import annotations

from pathlib import Path

import pytest

from podcast_reader_premium.config import Settings


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_path": tmp_path / "premium.sqlite3",
        "public_origin": "https://premium.test",
        "user_code_pepper": b"test-pepper-is-at-least-thirty-two-bytes",
        "environment": "test",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "public_origin",
    [
        "https://premium.test/",
        "https://PREMIUM.test",
        "https://premium.test:443",
        "https://premium.test:invalid",
        "https://premium.test/path",
    ],
)
def test_public_origin_must_be_canonical(tmp_path: Path, public_origin: str) -> None:
    with pytest.raises(ValueError):
        _settings(tmp_path, public_origin=public_origin)


@pytest.mark.parametrize(
    "field",
    [
        "session_ttl_seconds",
        "access_ttl_seconds",
        "refresh_ttl_seconds",
        "device_ttl_seconds",
        "device_poll_interval_seconds",
        "device_max_polls",
    ],
)
def test_security_timing_fields_must_be_positive(tmp_path: Path, field: str) -> None:
    with pytest.raises(ValueError):
        _settings(tmp_path, **{field: 0})
