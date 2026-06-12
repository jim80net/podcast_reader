"""Tests for podcast_reader.engine.pairing (in-memory pairing-code exchange)."""

from __future__ import annotations

from podcast_reader.engine.pairing import (
    CODE_ALPHABET,
    CODE_LENGTH,
    CODE_TTL_S,
    MAX_FAILED_ATTEMPTS,
    PairingState,
)


class _Clock:
    """Settable wall clock injected into PairingState."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class TestAlphabet:
    def test_alphabet_excludes_ambiguous_characters(self) -> None:
        """Design: unambiguous Crockford-style alphabet — no 0/O/1/I."""
        for ambiguous in "0O1Il":
            assert ambiguous not in CODE_ALPHABET

    def test_code_length_and_ttl_constants(self) -> None:
        assert CODE_LENGTH == 6
        assert CODE_TTL_S == 300.0
        assert MAX_FAILED_ATTEMPTS == 5


class TestMint:
    def test_mint_returns_code_from_alphabet(self) -> None:
        code, _expires_at = PairingState().mint()
        assert len(code) == CODE_LENGTH
        assert all(c in CODE_ALPHABET for c in code)

    def test_mint_expires_at_is_now_plus_ttl(self) -> None:
        clock = _Clock()
        _code, expires_at = PairingState(clock=clock).mint()
        assert expires_at == clock.now + CODE_TTL_S

    def test_new_mint_replaces_old_code(self) -> None:
        """Spec scenario: New mint replaces the old code — only the newly
        returned code can be claimed."""
        state = PairingState()
        old, _ = state.mint()
        new, _ = state.mint()
        assert state.claim(old) is False
        assert state.claim(new) is True


class TestClaim:
    def test_correct_code_claims_exactly_once(self) -> None:
        """Spec scenario: Valid claim returns the token once — a second claim
        with the same code is rejected (single-use)."""
        state = PairingState()
        code, _ = state.mint()
        assert state.claim(code) is True
        assert state.claim(code) is False

    def test_wrong_code_rejected_correct_still_claimable(self) -> None:
        state = PairingState()
        code, _ = state.mint()
        assert state.claim("XXXXXX") is False
        assert state.claim(code) is True

    def test_claim_without_pending_code_rejected(self) -> None:
        assert PairingState().claim("ABCDEF") is False

    def test_expired_code_rejected(self) -> None:
        """Spec scenario: Expired code rejected uniformly."""
        clock = _Clock()
        state = PairingState(clock=clock)
        code, _ = state.mint()
        clock.now += CODE_TTL_S
        assert state.claim(code) is False

    def test_unexpired_code_accepted_just_before_ttl(self) -> None:
        clock = _Clock()
        state = PairingState(clock=clock)
        code, _ = state.mint()
        clock.now += CODE_TTL_S - 0.001
        assert state.claim(code) is True


class TestAttemptBudget:
    def test_five_failed_attempts_invalidate_the_code(self) -> None:
        """Spec scenario: Attempt budget invalidates the code — after five
        wrong claims, even the correct code is rejected."""
        state = PairingState()
        code, _ = state.mint()
        for _ in range(MAX_FAILED_ATTEMPTS):
            assert state.claim("WRONG1") is False
        assert state.claim(code) is False

    def test_four_failed_attempts_leave_the_code_claimable(self) -> None:
        state = PairingState()
        code, _ = state.mint()
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            assert state.claim("WRONG1") is False
        assert state.claim(code) is True

    def test_new_mint_resets_the_budget(self) -> None:
        state = PairingState()
        state.mint()
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            state.claim("WRONG1")
        code, _ = state.mint()
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            assert state.claim("WRONG1") is False
        assert state.claim(code) is True
