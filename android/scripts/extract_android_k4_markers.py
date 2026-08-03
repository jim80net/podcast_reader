#!/usr/bin/env python3
"""Emit the Android K4 full/prefix sweep list from its Kotlin source of truth."""

from __future__ import annotations

import re
import sys
from pathlib import Path


DEFINITION = re.compile(
    r'^\s*Marker\(name = "(?P<name>[A-Z][A-Z0-9_]*)", '
    r'full = "(?P<full>[ -~]+)", prefix = "(?P<prefix>[ -~]+)"\),\s*$',
    re.MULTILINE,
)


def extract(source: str) -> list[str]:
    definitions = list(DEFINITION.finditer(source))
    declared_lines = [line for line in source.splitlines() if line.lstrip().startswith("Marker(name =")]
    if not definitions or len(definitions) != len(declared_lines):
        raise ValueError("K4 marker manifest contains an unparseable definition")

    names: set[str] = set()
    values: set[str] = set()
    result: list[str] = []
    for definition in definitions:
        name = definition.group("name")
        full = definition.group("full")
        prefix = definition.group("prefix")
        if name in names or full in values or prefix in values:
            raise ValueError("K4 marker manifest contains a duplicate")
        if len(prefix) < 8 or not full.startswith(prefix) or full == prefix:
            raise ValueError("K4 marker manifest contains an unsafe full/prefix pair")
        names.add(name)
        values.update((full, prefix))
        result.extend((full, prefix))
    return result


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: extract_android_k4_markers.py KOTLIN_SOURCE", file=sys.stderr)
        return 2
    try:
        markers = extract(Path(sys.argv[1]).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Android K4 marker extraction failed: {error}", file=sys.stderr)
        return 1
    print("\n".join(markers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
