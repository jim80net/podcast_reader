from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from podcast_reader_premium.app import create_app
from podcast_reader_premium.config import Settings
from podcast_reader_premium.db import create_database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "premium?test.sqlite3",
        public_origin="https://premium.test",
        user_code_pepper=b"test-pepper-is-at-least-thirty-two-bytes",
        environment="test",
        build_sha="test-sha",
        device_poll_interval_seconds=5,
        stripe_price_id="price_test_premium",
    )


@pytest.fixture
def client(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("prepend_sys_path", str(root / "src"))
    config.attributes["database_path"] = settings.database_path
    command.upgrade(config, "head")
    engine = create_database(settings)
    with TestClient(create_app(settings, engine=engine), base_url=settings.public_origin) as value:
        yield value


@pytest.fixture
def account(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/v1/accounts", json={"email": " Reader@Example.COM ", "password": "correct horse battery"}
    )
    assert response.status_code == 201
    return cast("dict[str, object]", response.json())


@pytest.fixture
def browser_auth(client: TestClient, account: dict[str, object]) -> dict[str, str]:
    response = client.post(
        "/v1/browser-sessions",
        json={"email": account["email"], "password": "correct horse battery"},
    )
    assert response.status_code == 201
    csrf_token = cast("str", response.json()["csrf_token"])
    return {"X-CSRF-Token": csrf_token, "Origin": "https://premium.test"}
