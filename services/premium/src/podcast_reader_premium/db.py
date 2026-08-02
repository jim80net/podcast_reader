from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings

EXPECTED_SCHEMA_REVISION = "0001_auth_foundation"


def create_database(settings: Settings) -> Engine:
    engine = create_engine(f"sqlite:///{settings.database_path}", future=True)

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def require_current_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        try:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        except Exception as exc:
            raise RuntimeError("premium database is missing its schema migration") from exc
    if revision != EXPECTED_SCHEMA_REVISION:
        raise RuntimeError(
            f"premium database schema is {revision!r}; expected {EXPECTED_SCHEMA_REVISION!r}"
        )


def begin_immediate(session: Session) -> None:
    """Acquire SQLite's write reservation before a read-modify-write section."""
    session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def session_dependency(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        yield session
