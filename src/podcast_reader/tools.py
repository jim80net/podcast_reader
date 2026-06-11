"""Locate console-script executables and run registered child processes.

Lives at the bottom of the import graph (no project imports) so both the
pipeline's tool call sites and the engine process model can share it without
cycles. Tool call sites spawn children via :func:`run_child`, which keeps a
registry of live children so :func:`kill_children` can reap them when the
engine shuts down.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

_CHILDREN_LOCK = threading.Lock()
_CHILDREN: dict[int, subprocess.Popen[str]] = {}


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


def resolve_bundled_worker(name: str) -> str | None:
    """Locate a sibling worker executable inside a frozen onedir bundle.

    Bundled workers (e.g. ``whisper-worker``) are a distinct class from
    external tools: in a onedir bundle both entry points sit at bundle root,
    which is exactly ``Path(sys.executable).parent`` (spike evidence in
    spike/SPIKE_REPORT.md). Unfrozen runs have no bundled workers — callers
    fall back to external tool resolution via :func:`resolve_tool`.
    """
    if not getattr(sys, "frozen", False):
        return None
    return shutil.which(name, path=str(Path(sys.executable).parent))


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


def run_child(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run an external tool to completion with captured text output.

    Drop-in for ``subprocess.run(args, capture_output=True, text=True,
    **popen_kwargs())`` that additionally registers the child in the module
    registry while it runs, so :func:`kill_children` can terminate it on
    engine shutdown. Raises :class:`FileNotFoundError` when the executable
    does not exist, exactly like ``subprocess.run``.
    """
    proc: subprocess.Popen[str] = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **popen_kwargs(),
    )
    with _CHILDREN_LOCK:
        _CHILDREN[proc.pid] = proc
    try:
        stdout, stderr = proc.communicate()
    except BaseException:
        # Preserve subprocess.run's kill-on-exception guarantee: without it a
        # KeyboardInterrupt would orphan the detached child (start_new_session
        # insulates it from the terminal's SIGINT). On POSIX kill the whole
        # process group (pid == pgid via start_new_session) so grandchildren
        # like yt-dlp's ffmpeg die too — mirroring kill_children. On Windows
        # the engine's Job Object reaps descendants, so killing the direct
        # child suffices.
        if sys.platform == "win32":
            proc.kill()
        else:
            with contextlib.suppress(ProcessLookupError):  # already exited
                os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise
    finally:
        with _CHILDREN_LOCK:
            _CHILDREN.pop(proc.pid, None)
    return subprocess.CompletedProcess(list(args), proc.wait(), stdout, stderr)


def live_children() -> list[int]:
    """PIDs of registered children that are still running."""
    with _CHILDREN_LOCK:
        return [pid for pid, proc in _CHILDREN.items() if proc.poll() is None]


def kill_children(grace_s: float = 5.0) -> None:
    """Terminate every live registered child's process group (POSIX only).

    Each child runs in its own session (:func:`popen_kwargs`), so its pid is
    also its process-group id; signalling the group reaches grandchildren too.
    SIGTERM goes out first; children that have not exited after *grace_s*
    seconds (e.g. a tool that traps TERM) get SIGKILL on the whole group.
    Windows is a no-op: the engine's kill-on-close Job Object reaps children
    there (see ``podcast_reader.engine.process``).
    """
    if sys.platform == "win32":
        return
    with _CHILDREN_LOCK:
        procs = list(_CHILDREN.values())
    survivors: list[subprocess.Popen[str]] = []
    for proc in procs:
        if proc.poll() is not None:
            continue
        with contextlib.suppress(ProcessLookupError):  # finished between poll and kill
            os.killpg(proc.pid, signal.SIGTERM)
        survivors.append(proc)
    deadline = time.monotonic() + grace_s
    for proc in survivors:
        try:
            proc.wait(timeout=max(deadline - time.monotonic(), 0))
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):  # exited at the last moment
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
