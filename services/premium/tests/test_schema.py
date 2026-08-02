from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from podcast_reader_premium.db import require_current_schema


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
