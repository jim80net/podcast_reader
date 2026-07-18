"""Unit gates for fail-closed inline-script policy compilation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from podcast_reader.engine.script_policy import ScriptPin, compile_script_policy


def _pin(name: str, text: str) -> ScriptPin:
    return ScriptPin(name, text, hashlib.sha256(text.encode()).hexdigest())


def test_valid_named_sequences_resolve_to_exact_text() -> None:
    policy = compile_script_policy(
        [_pin("sync-v1", "sync"), _pin("search-v1", "search")],
        [(), ("sync-v1", "search-v1")],
    )
    assert policy.errors == ()
    assert policy.sequences == frozenset({(), ("sync", "search")})


def test_changed_text_with_stale_pin_authorizes_nothing() -> None:
    original = _pin("search-v1", "original")
    changed = ScriptPin(original.name, "modified", original.sha256)
    policy = compile_script_policy([changed], [("search-v1",)])
    assert "digest mismatch" in " ".join(policy.errors)
    assert policy.sequences == frozenset()


def test_unknown_or_unreferenced_pin_authorizes_nothing() -> None:
    policy = compile_script_policy([_pin("known-v1", "known")], [("unknown-v1",)])
    assert "unknown pins" in " ".join(policy.errors)
    assert "unreferenced" in " ".join(policy.errors)
    assert policy.sequences == frozenset()


def test_duplicate_text_or_repeated_pin_authorizes_nothing() -> None:
    policy = compile_script_policy(
        [_pin("one-v1", "same"), _pin("two-v1", "same")],
        [("one-v1", "one-v1"), ("two-v1",)],
    )
    assert "identical text" in " ".join(policy.errors)
    assert "repeats a pin" in " ".join(policy.errors)
    assert policy.sequences == frozenset()


def test_ci_runs_the_byte_exact_policy_checker() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml").read_text()
    assert "uv run python scripts/csp_scripts.py check" in workflow
