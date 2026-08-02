from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


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

    def __post_init__(self) -> None:
        origin = urlsplit(self.public_origin)
        if origin.scheme != "https" or not origin.netloc or origin.path not in {"", "/"}:
            raise ValueError("public_origin must be an HTTPS origin without a path")
        if origin.query or origin.fragment or origin.username or origin.password:
            raise ValueError("public_origin must be an HTTPS origin without credentials")
        if len(self.user_code_pepper) < 32:
            raise ValueError("user_code_pepper must contain at least 32 bytes")
        if self.environment not in {"dev", "test"}:
            raise ValueError("the premium service is restricted to dev or test environments")

    @property
    def expected_host(self) -> str:
        host = urlsplit(self.public_origin).netloc
        return host.lower()
