from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from .app import create_app
from .config import Settings
from .db import create_database, require_current_schema
from .models import User
from .security import hash_password, normalize_email, now_epoch, record_id


def _settings(args: argparse.Namespace) -> Settings:
    pepper = os.environ.get("PREMIUM_USER_CODE_PEPPER")
    if pepper is None:
        raise SystemExit("PREMIUM_USER_CODE_PEPPER must be set to at least 32 characters")
    return Settings(
        database_path=Path(args.database),
        public_origin=args.public_origin,
        user_code_pepper=pepper.encode(),
        environment="dev",
        build_sha=os.environ.get("PREMIUM_BUILD_SHA", "development"),
    )


def _migrate(database: Path) -> None:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")


def _bootstrap_admin(settings: Settings) -> None:
    email = normalize_email(input("Admin email: "))
    password = getpass.getpass("Admin password: ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("passwords did not match")
    engine = create_database(settings)
    require_current_schema(engine)
    with Session(engine) as database:
        if database.scalar(select(User.id).where(User.email == email)) is not None:
            raise SystemExit("account already exists")
        database.add(
            User(
                id=record_id("usr"),
                email=email,
                password_hash=hash_password(password),
                role="admin",
                status="active",
                verification="unverified_test",
                created_at=now_epoch(),
            )
        )
        database.commit()
    engine.dispose()
    print("development admin created")


def main() -> None:
    parser = argparse.ArgumentParser(description="Podcast Reader premium development service")
    parser.add_argument("--database", default="premium-dev.sqlite3")
    parser.add_argument("--public-origin", default="https://premium.localhost")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate")
    subparsers.add_parser("bootstrap-admin")
    subparsers.add_parser("serve")
    args = parser.parse_args()
    settings = _settings(args)
    if args.command == "migrate":
        _migrate(settings.database_path)
    elif args.command == "bootstrap-admin":
        _bootstrap_admin(settings)
    else:
        require_current_schema(create_database(settings))
        uvicorn.run(create_app(settings), host="127.0.0.1", port=8787, workers=1)


if __name__ == "__main__":
    main()
