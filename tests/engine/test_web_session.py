"""Tests for the stateless browser-session credential."""

from __future__ import annotations

import base64
import json

import pytest

from podcast_reader.engine.web_session import (
    SESSION_LIFETIME_S,
    SESSION_PREFIX,
    WebSessionSigner,
)


class _Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _payload(credential: str) -> dict[str, object]:
    encoded = credential.split(".")[1]
    padded = encoded + "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))  # type: ignore[no-any-return]


class TestWebSessionSigner:
    def test_issue_and_verify_180_day_credential(self) -> None:
        clock = _Clock()
        signer = WebSessionSigner(b"engine-bearer", generation=7, clock=clock)

        credential = signer.issue()

        assert credential.startswith(f"{SESSION_PREFIX}.")
        assert signer.verify(credential)
        payload = _payload(credential)
        assert payload["v"] == 1
        assert payload["g"] == 7
        assert payload["iat"] == int(clock.now)
        assert payload["exp"] == int(clock.now) + SESSION_LIFETIME_S
        assert isinstance(payload["n"], str) and len(payload["n"]) >= 20
        assert "engine-bearer" not in credential

    def test_nonce_makes_each_credential_distinct(self) -> None:
        signer = WebSessionSigner(b"engine-bearer", generation=1, clock=_Clock())
        assert signer.issue() != signer.issue()

    def test_expiry_is_fail_closed(self) -> None:
        clock = _Clock()
        signer = WebSessionSigner(b"engine-bearer", generation=1, clock=clock)
        credential = signer.issue()
        clock.now += SESSION_LIFETIME_S - 1
        assert signer.verify(credential)
        clock.now += 1
        assert not signer.verify(credential)

    def test_generation_change_revokes_existing_sessions(self) -> None:
        clock = _Clock()
        old = WebSessionSigner(b"engine-bearer", generation=4, clock=clock)
        credential = old.issue()
        revoked = WebSessionSigner(b"engine-bearer", generation=5, clock=clock)
        assert not revoked.verify(credential)

    def test_bearer_rotation_revokes_existing_sessions(self) -> None:
        clock = _Clock()
        credential = WebSessionSigner(b"old-bearer", generation=1, clock=clock).issue()
        assert not WebSessionSigner(b"new-bearer", generation=1, clock=clock).verify(credential)

    @pytest.mark.parametrize(
        "credential",
        [
            "",
            "not-a-session",
            f"{SESSION_PREFIX}.only-two",
            f"{SESSION_PREFIX}.bad-base64.!",
            "wrong-prefix.e30.signature",
        ],
    )
    def test_malformed_credentials_reject_without_raising(self, credential: str) -> None:
        assert not WebSessionSigner(b"engine-bearer", generation=1).verify(credential)

    def test_tampered_payload_and_signature_reject(self) -> None:
        signer = WebSessionSigner(b"engine-bearer", generation=1, clock=_Clock())
        prefix, payload, signature = signer.issue().split(".")
        replacement = "A" if payload[-1] != "A" else "B"
        assert not signer.verify(f"{prefix}.{payload[:-1]}{replacement}.{signature}")
        replacement = "A" if signature[-1] != "A" else "B"
        assert not signer.verify(f"{prefix}.{payload}.{signature[:-1]}{replacement}")

    def test_noncanonical_base64url_alias_rejected(self) -> None:
        signer = WebSessionSigner(b"engine-bearer", generation=1, clock=_Clock())
        prefix, payload, signature = signer.issue().split(".")
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        canonical_index = alphabet.index(signature[-1])
        assert canonical_index % 4 == 0  # SHA-256 leaves two unused trailing bits.
        alias = alphabet[canonical_index + 1]
        assert not signer.verify(f"{prefix}.{payload}.{signature[:-1]}{alias}")

    def test_oversized_credential_rejected(self) -> None:
        signer = WebSessionSigner(b"engine-bearer", generation=1)
        assert not signer.verify(f"{SESSION_PREFIX}.{'A' * 2000}.signature")

    @pytest.mark.parametrize("generation", [0, -1, True])
    def test_generation_must_be_a_positive_integer(self, generation: int) -> None:
        with pytest.raises(ValueError, match="generation"):
            WebSessionSigner(b"engine-bearer", generation=generation)

    def test_empty_signing_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="signing key"):
            WebSessionSigner(b"", generation=1)
