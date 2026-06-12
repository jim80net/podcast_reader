"""In-memory pairing-code exchange state (the extension's token bootstrap).

A bearer-authed client mints a short-lived 6-character code; a token-less
client claims it once via ``POST /v1/pair/claim`` and receives the engine
token. Codes live exclusively in process memory — never in any file or log
(the key-store discipline) — and are hardened per the engine-service spec:
single-use, 300-second TTL, a 5-failed-attempt budget, constant-time
comparison, and replacement on every new mint.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: Crockford-style unambiguous alphabet: no 0/O, no 1/I/L, no U.
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 6
CODE_TTL_S = 300.0
MAX_FAILED_ATTEMPTS = 5


class PairingState:
    """Thread-safe holder of the single pending pairing code.

    At most one code is pending at a time: :meth:`mint` replaces any prior
    code (resetting its budget), and a successful :meth:`claim` consumes it.
    *clock* is injectable for expiry tests; production uses wall time so the
    returned ``expires_at`` is meaningful to clients.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._code: str | None = None
        self._expires_at = 0.0
        self._failed_attempts = 0

    def mint(self) -> tuple[str, float]:
        """Mint a new single-use code, replacing any pending one.

        Returns ``(code, expires_at)`` with *expires_at* in epoch seconds.
        """
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        with self._lock:
            self._code = code
            self._expires_at = self._clock() + CODE_TTL_S
            self._failed_attempts = 0
            return code, self._expires_at

    def claim(self, code: str) -> bool:
        """Attempt to claim the pending code; True exactly once on a match.

        The comparison is constant-time (``hmac.compare_digest``). A mismatch
        burns one unit of the attempt budget and invalidates the pending code
        once :data:`MAX_FAILED_ATTEMPTS` is reached. Expired and absent codes
        are simply rejected — the caller surfaces every rejection uniformly,
        so no oracle distinguishes the cases.
        """
        with self._lock:
            pending = self._code
            if pending is None or self._clock() >= self._expires_at:
                self._invalidate()
                # Compare against a dummy so timing stays flat across branches.
                hmac.compare_digest(code.encode(), b"\x00" * CODE_LENGTH)
                return False
            if not hmac.compare_digest(code.encode(), pending.encode()):
                self._failed_attempts += 1
                if self._failed_attempts >= MAX_FAILED_ATTEMPTS:
                    self._invalidate()
                return False
            self._invalidate()  # single-use: a successful claim consumes the code
            return True

    def _invalidate(self) -> None:
        """Clear the pending code and its budget; caller holds the lock."""
        self._code = None
        self._expires_at = 0.0
        self._failed_attempts = 0
