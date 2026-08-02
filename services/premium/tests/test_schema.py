from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from podcast_reader_premium.config import Settings
from podcast_reader_premium.db import create_database, require_current_schema


@pytest.mark.parametrize("revision", [None, "9999_future_schema"])
def test_service_fails_closed_for_missing_or_newer_schema(
    tmp_path: Path, revision: str | None
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'schema.sqlite3'}")
    if revision is not None:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": revision},
            )
    with pytest.raises(RuntimeError):
        require_current_schema(engine)


def test_database_url_preserves_question_mark(tmp_path: Path) -> None:
    database_path = tmp_path / "premium?dev.sqlite3"
    settings = Settings(
        database_path=database_path,
        public_origin="https://premium.test",
        user_code_pepper=b"test-pepper-is-at-least-thirty-two-bytes",
        environment="test",
    )
    engine = create_database(settings)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE marker (value INTEGER)"))
    assert database_path.is_file()
    assert not (tmp_path / "premium").exists()
    engine.dispose()


def test_auth_migration_indexes_access_token_family(client: TestClient) -> None:
    app = cast("Any", client.app)
    indexes = inspect(app.state.engine).get_indexes("access_tokens")
    assert {item["name"] for item in indexes} == {"ix_access_tokens_family_id"}
