"""Fail-closed, byte-exact policy for inline transcript scripts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ScriptPin:
    """A stable policy name bound to exact script text."""

    name: str
    text: str
    sha256: str

    @property
    def actual_sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


@dataclass(frozen=True)
class CompiledScriptPolicy:
    """Validated policy output; invalid input authorizes no sequence."""

    pins: tuple[ScriptPin, ...]
    sequence_names: tuple[tuple[str, ...], ...]
    sequences: frozenset[tuple[str, ...]]
    errors: tuple[str, ...]


def compile_script_policy(
    pins: Iterable[ScriptPin], sequence_names: Iterable[tuple[str, ...]]
) -> CompiledScriptPolicy:
    """Resolve named sequences only when every pin and tuple is coherent."""
    pin_tuple = tuple(pins)
    names_tuple = tuple(sequence_names)
    errors: list[str] = []
    by_name: dict[str, ScriptPin] = {}
    text_owner: dict[str, str] = {}

    for pin in pin_tuple:
        if not pin.name or pin.name in by_name:
            errors.append(f"duplicate or empty script pin name: {pin.name!r}")
        else:
            by_name[pin.name] = pin
        prior_owner = text_owner.get(pin.text)
        if prior_owner is not None:
            errors.append(f"script pins {prior_owner!r} and {pin.name!r} have identical text")
        else:
            text_owner[pin.text] = pin.name
        if not _SHA256_RE.fullmatch(pin.sha256):
            errors.append(f"script pin {pin.name!r} has an invalid sha256")
        elif pin.actual_sha256 != pin.sha256:
            errors.append(
                f"script pin {pin.name!r} digest mismatch: "
                f"expected {pin.sha256}, got {pin.actual_sha256}"
            )

    seen_sequences: set[tuple[str, ...]] = set()
    referenced: set[str] = set()
    resolved: set[tuple[str, ...]] = set()
    for sequence in names_tuple:
        if sequence in seen_sequences:
            errors.append(f"duplicate allowed script sequence: {sequence!r}")
        seen_sequences.add(sequence)
        if len(sequence) != len(set(sequence)):
            errors.append(f"allowed script sequence repeats a pin: {sequence!r}")
        unknown = [name for name in sequence if name not in by_name]
        if unknown:
            errors.append(f"allowed script sequence references unknown pins: {unknown!r}")
            continue
        referenced.update(sequence)
        resolved.add(tuple(by_name[name].text for name in sequence))

    unreferenced = sorted(set(by_name) - referenced)
    if unreferenced:
        errors.append(f"unreferenced script pins must be retired or authorized: {unreferenced!r}")

    return CompiledScriptPolicy(
        pins=pin_tuple,
        sequence_names=names_tuple,
        sequences=frozenset() if errors else frozenset(resolved),
        errors=tuple(errors),
    )
