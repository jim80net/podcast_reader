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
from .db import begin_immediate, create_database, require_current_schema
from .entitlements import ensure_projection, rebuild_projection, record_audit, repair_projection
from .models import EntitlementProjection, User
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
        stripe_secret_key=os.environ.get("STRIPE_SECRET_KEY"),
        stripe_price_id=os.environ.get("STRIPE_PRICE_ID"),
        stripe_webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET"),
        premium_currency=os.environ.get("PREMIUM_PRICE_CURRENCY", "usd"),
        premium_unit_amount=int(os.environ.get("PREMIUM_PRICE_UNIT_AMOUNT", "999")),
        email_maildir_path=(
            Path(value) if (value := os.environ.get("PREMIUM_EMAIL_MAILDIR")) else None
        ),
        email_delivery_hmac_key=(
            value.encode() if (value := os.environ.get("PREMIUM_EMAIL_DELIVERY_HMAC_KEY")) else None
        ),
    )


def _migrate(database: Path) -> None:
    service_root = Path(__file__).resolve().parents[2]
    ini_path = service_root / "alembic.ini"
    migrations_path = service_root / "migrations"
    if not ini_path.is_file() or not migrations_path.is_dir():
        raise SystemExit("P1 migration commands require a services/premium source checkout")
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(migrations_path))
    config.set_main_option("prepend_sys_path", str(service_root / "src"))
    config.attributes["database_path"] = database
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
        user = User(
            id=record_id("usr"),
            email=email,
            password_hash=hash_password(password),
            role="admin",
            status="active",
            verification="unverified_test",
            created_at=now_epoch(),
        )
        database.add(user)
        database.flush()
        ensure_projection(database, user.id, timestamp=user.created_at)
        database.commit()
    engine.dispose()
    print("development admin created")


def _repair_entitlements(settings: Settings) -> None:
    engine = create_database(settings)
    require_current_schema(engine)
    repaired = 0
    with Session(engine) as database:
        begin_immediate(database)
        for user_id in database.scalars(select(User.id).order_by(User.id)):
            projection = database.get(EntitlementProjection, user_id)
            before = (
                {"missing": True}
                if projection is None
                else {
                    "provider_tier": projection.provider_tier,
                    "provider_source": projection.provider_source,
                    "admin_override": projection.admin_override,
                    "effective_tier": projection.effective_tier,
                    "revision": projection.revision,
                    "last_event_id": projection.last_event_id,
                }
            )
            after = rebuild_projection(database, user_id)
            timestamp = now_epoch()
            if repair_projection(database, user_id, timestamp=timestamp):
                repaired += 1
                record_audit(
                    database,
                    actor_user_id=None,
                    action="entitlement_projection.repair",
                    target_kind="user",
                    target_id=user_id,
                    before=before,
                    after=after,
                    reason="host CLI ledger replay repair",
                    request_id=record_id("cli"),
                    timestamp=timestamp,
                )
        database.commit()
    engine.dispose()
    print(f"repaired {repaired} entitlement projection(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Podcast Reader premium development service")
    parser.add_argument("--database", default="premium-dev.sqlite3")
    parser.add_argument("--public-origin", default="https://premium.localhost")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate")
    subparsers.add_parser("bootstrap-admin")
    subparsers.add_parser("repair-entitlements")
    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    settings = _settings(args)
    if args.command == "migrate":
        _migrate(settings.database_path)
    elif args.command == "bootstrap-admin":
        _bootstrap_admin(settings)
    elif args.command == "repair-entitlements":
        _repair_entitlements(settings)
    else:
        if not 1 <= args.port <= 65535:
            raise SystemExit("serve port must be between 1 and 65535")
        require_current_schema(create_database(settings))
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
