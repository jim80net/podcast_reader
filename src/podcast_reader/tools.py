"""Locate console-script executables and spawn-time process options.

Lives at the bottom of the import graph (no project imports) so both the
pipeline's tool call sites and the engine process model can share it without
cycles.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any


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


def popen_kwargs() -> dict[str, Any]:
    """Extra ``subprocess`` keyword arguments so children die with the engine.

    POSIX: each child gets its own session (process group), letting the engine
    kill the whole group on shutdown. Windows: returns no extra kwargs — child
    reaping is handled by the engine joining a Job Object with
    kill-on-job-close at startup (children inherit job membership), see
    ``podcast_reader.engine.process``.
    """
    if sys.platform == "win32":
        return {}
    return {"start_new_session": True}
