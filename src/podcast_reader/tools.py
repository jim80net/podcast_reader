"""Locate console-script executables bundled with this installation."""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_tool(name: str) -> str:
    """Resolve *name* to the executable installed alongside this interpreter.

    Console scripts of dependencies (e.g. yt-dlp) land in the same bin
    directory as the running Python, but that directory is not on PATH when
    the package is installed via ``uv tool install`` — only the primary
    ``podcast-reader`` entry point gets exposed. Returns the bare name when
    no sibling executable exists, so PATH lookup still covers externally
    installed tools.
    """
    candidate = Path(sys.executable).parent / name
    if candidate.is_file():
        return str(candidate)
    return name
