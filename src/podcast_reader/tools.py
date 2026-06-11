"""Locate console-script executables bundled with this installation."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def resolve_tool(name: str, tools_dir: Path | None = None) -> str:
    """Resolve *name* per spec precedence: explicit/env tools dir → frozen bundle
    tools dir (or interpreter sibling when unfrozen) → bare name for PATH.

    Console scripts of dependencies (e.g. yt-dlp) land in the same bin
    directory as the running Python, but that directory is not on PATH when
    the package is installed via ``uv tool install`` — only the primary
    ``podcast-reader`` entry point gets exposed. Under ``sys.frozen`` no
    console scripts exist next to the executable, so only the bundle's
    ``tools`` directory is consulted. ``shutil.which`` with an explicit
    search path handles Windows script suffixes (``.exe`` via PATHEXT) and
    requires the execute bit on POSIX. Returns the bare name when no
    preferred copy exists, so PATH lookup still covers externally installed
    tools.
    """
    if tools_dir is None:
        env = os.environ.get("PODCAST_READER_TOOLS_DIR")
        tools_dir = Path(env) if env else None
    if tools_dir is not None:
        found = shutil.which(name, path=str(tools_dir))
        if found:
            return found
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        found = shutil.which(name, path=str(bundle / "tools"))
        return found if found else name
    found = shutil.which(name, path=str(Path(sys.executable).parent))
    return found if found else name
