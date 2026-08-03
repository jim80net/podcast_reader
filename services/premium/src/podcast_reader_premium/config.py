from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_TEST_PRICE_ID = "price_test_premium"


@dataclass(frozen=True)
class Settings:
    database_path: Path
    public_origin: str
    user_code_pepper: bytes
    environment: str = "dev"
    build_sha: str = "development"
    session_ttl_seconds: int = 12 * 60 * 60
    access_ttl_seconds: int = 15 * 60
    refresh_ttl_seconds: int = 30 * 24 * 60 * 60
    device_ttl_seconds: int = 10 * 60
    device_poll_interval_seconds: int = 5
    device_max_polls: int = 150
    stripe_secret_key: str | None = None
    stripe_price_id: str | None = None
    stripe_webhook_secret: str | None = None
    premium_currency: str = "usd"
    premium_unit_amount: int = 999
    payment_claim_ttl_seconds: int = 5 * 60
    payment_retry_base_seconds: int = 5
    payment_max_attempts: int = 5
    email_maildir_path: Path | None = None
    email_delivery_hmac_key: bytes | None = None
    email_sink: str = "dev_maildir"

    def __post_init__(self) -> None:
        try:
            origin = urlsplit(self.public_origin)
            hostname = origin.hostname
            port = origin.port
        except ValueError as exc:
            raise ValueError("public_origin must be a canonical HTTPS origin") from exc
        if origin.scheme != "https" or not origin.netloc or origin.path != "":
            raise ValueError("public_origin must be an HTTPS origin without a path")
        if origin.query or origin.fragment or origin.username or origin.password:
            raise ValueError("public_origin must be an HTTPS origin without credentials")
        if hostname is None or hostname.endswith(".") or any(char.isspace() for char in hostname):
            raise ValueError("public_origin must contain a canonical hostname")
        try:
            hostname.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("public_origin hostname must use ASCII or punycode") from exc
        if port == 443:
            raise ValueError("public_origin must omit the default HTTPS port")
        display_host = f"[{hostname}]" if ":" in hostname else hostname
        canonical_netloc = display_host if port is None else f"{display_host}:{port}"
        if self.public_origin != f"https://{canonical_netloc}":
            raise ValueError("public_origin must be canonical lowercase HTTPS origin")
        if len(self.user_code_pepper) < 32:
            raise ValueError("user_code_pepper must contain at least 32 bytes")
        if self.environment not in {"dev", "test"}:
            raise ValueError("the premium service is restricted to dev or test environments")
        if self.email_sink != "dev_maildir":
            raise ValueError("only the DEV Maildir email sink is supported")
        if self.email_delivery_hmac_key is not None and len(self.email_delivery_hmac_key) < 32:
            raise ValueError("email_delivery_hmac_key must contain at least 32 bytes")
        positive_fields = {
            "session_ttl_seconds": self.session_ttl_seconds,
            "access_ttl_seconds": self.access_ttl_seconds,
            "refresh_ttl_seconds": self.refresh_ttl_seconds,
            "device_ttl_seconds": self.device_ttl_seconds,
            "device_poll_interval_seconds": self.device_poll_interval_seconds,
            "device_max_polls": self.device_max_polls,
            "premium_unit_amount": self.premium_unit_amount,
            "payment_claim_ttl_seconds": self.payment_claim_ttl_seconds,
            "payment_retry_base_seconds": self.payment_retry_base_seconds,
            "payment_max_attempts": self.payment_max_attempts,
        }
        invalid = [name for name, value in positive_fields.items() if value <= 0]
        if invalid:
            raise ValueError(f"security timing fields must be positive: {', '.join(invalid)}")
        if (
            not self.premium_currency.isascii()
            or not self.premium_currency.isalpha()
            or self.premium_currency != self.premium_currency.lower()
            or len(self.premium_currency) != 3
        ):
            raise ValueError("premium_currency must be a lowercase three-letter currency")

    @property
    def expected_stripe_price_id(self) -> str:
        return self.stripe_price_id or DEFAULT_TEST_PRICE_ID

    @property
    def expected_host(self) -> str:
        host = urlsplit(self.public_origin).netloc
        return host.lower()
