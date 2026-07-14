"""Stateless signed sessions for the tailnet-only browser surface.

The engine bearer is the HMAC key but is never embedded in the credential.
Credentials carry only a format version, an explicit revocation generation,
issued/expiry times, and a random nonce.  Advancing ``generation`` globally or
rotating the engine bearer invalidates every existing browser session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

SESSION_PREFIX = "prws1"
SESSION_VERSION = 1
SESSION_LIFETIME_S = 180 * 24 * 60 * 60
_NONCE_BYTES = 18
_PAYLOAD_KEYS = frozenset({"v", "g", "iat", "exp", "n"})


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


@dataclass(frozen=True)
class WebSessionSigner:
    """Issue and verify fixed-lifetime web-session credentials.

    ``generation`` is the global revoke hook: construct the signer with a
    larger generation to invalidate all credentials issued under the old one.
    The caller owns persistence of that generation when the revoke control is
    exposed; M1 starts at generation 1.
    """

    signing_key: bytes = field(repr=False)
    generation: int
    clock: Callable[[], float] = field(default=time.time, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.signing_key:
            raise ValueError("signing key must not be empty")
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 1
        ):
            raise ValueError("generation must be a positive integer")

    def issue(self) -> str:
        """Return a new signed 180-day credential."""
        issued_at = int(self.clock())
        payload = {
            "exp": issued_at + SESSION_LIFETIME_S,
            "g": self.generation,
            "iat": issued_at,
            "n": secrets.token_urlsafe(_NONCE_BYTES),
            "v": SESSION_VERSION,
        }
        encoded_payload = _encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signing_input = f"{SESSION_PREFIX}.{encoded_payload}".encode("ascii")
        signature = hmac.new(self.signing_key, signing_input, hashlib.sha256).digest()
        return f"{signing_input.decode('ascii')}.{_encode(signature)}"

    def verify(self, credential: str) -> bool:
        """Return true only for an intact, current, unexpired credential."""
        if len(credential) > 1024:
            return False
        try:
            prefix, encoded_payload, encoded_signature = credential.split(".")
            signature = _decode(encoded_signature)
            payload_bytes = _decode(encoded_payload)
        except (TypeError, ValueError):
            return False
        if (
            prefix != SESSION_PREFIX
            or len(signature) != hashlib.sha256().digest_size
            or _encode(signature) != encoded_signature
            or _encode(payload_bytes) != encoded_payload
        ):
            return False

        signing_input = f"{prefix}.{encoded_payload}".encode("ascii")
        expected = hmac.new(self.signing_key, signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return False

        try:
            payload = json.loads(payload_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
            return False

        version = payload.get("v")
        generation = payload.get("g")
        issued_at = payload.get("iat")
        expires_at = payload.get("exp")
        nonce = payload.get("n")
        if isinstance(version, bool) or not isinstance(version, int):
            return False
        if isinstance(generation, bool) or not isinstance(generation, int):
            return False
        if isinstance(issued_at, bool) or not isinstance(issued_at, int):
            return False
        if isinstance(expires_at, bool) or not isinstance(expires_at, int):
            return False
        if (
            version != SESSION_VERSION
            or generation != self.generation
            or not isinstance(nonce, str)
            or len(nonce) < 20
            or expires_at != issued_at + SESSION_LIFETIME_S
        ):
            return False
        now = int(self.clock())
        return issued_at <= now < expires_at
