"""Tests for podcast_reader.tools module."""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import TYPE_CHECKING

import pytest

from podcast_reader.tools import (
    kill_children,
    live_children,
    resolve_bundled_worker,
    resolve_tool,
    run_child,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _FrozenSys:
    """Stand-in for the sys module under PyInstaller freezing."""

    frozen = True

    def __init__(self, executable: str, meipass: str) -> None:
        self.executable = executable
        self._MEIPASS = meipass


class TestResolveTool:
    def test_resolves_executable_next_to_interpreter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A console script in the interpreter's bin dir is resolved to its full path."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").touch()
        sibling = bin_dir / "yt-dlp"
        sibling.touch()
        sibling.chmod(0o755)

        monkeypatch.setattr("podcast_reader.tools.sys.executable", str(bin_dir / "python"))

        assert resolve_tool("yt-dlp") == str(sibling)

    def test_ignores_non_executable_sibling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-executable file of the same name must not shadow PATH lookup."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").touch()
        (bin_dir / "yt-dlp").touch()  # exists but not executable

        monkeypatch.setattr("podcast_reader.tools.sys.executable", str(bin_dir / "python"))

        assert resolve_tool("yt-dlp") == "yt-dlp"

    def test_falls_back_to_bare_name_for_path_lookup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no sibling executable exists, the bare name is returned for PATH lookup."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").touch()

        monkeypatch.setattr("podcast_reader.tools.sys.executable", str(bin_dir / "python"))

        assert resolve_tool("whisper-ctranslate2") == "whisper-ctranslate2"

    def test_tools_dir_param_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An explicit tools_dir takes precedence over the interpreter sibling dir."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        exe = tools_dir / "yt-dlp"
        exe.touch()
        exe.chmod(0o755)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sib = bin_dir / "yt-dlp"
        sib.touch()
        sib.chmod(0o755)
        (bin_dir / "python").touch()
        monkeypatch.setattr("podcast_reader.tools.sys.executable", str(bin_dir / "python"))
        assert resolve_tool("yt-dlp", tools_dir=tools_dir) == str(exe)

    def test_env_var_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """PODCAST_READER_TOOLS_DIR supplies the tools dir when no param is given."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        exe = tools_dir / "yt-dlp"
        exe.touch()
        exe.chmod(0o755)
        monkeypatch.setenv("PODCAST_READER_TOOLS_DIR", str(tools_dir))
        assert resolve_tool("yt-dlp") == str(exe)

    def test_frozen_skips_interpreter_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Under sys.frozen the interpreter sibling dir must not be searched."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sib = bin_dir / "yt-dlp"
        sib.touch()
        sib.chmod(0o755)
        (bin_dir / "python").touch()
        monkeypatch.setattr("podcast_reader.tools.sys.executable", str(bin_dir / "python"))
        monkeypatch.setattr(
            "podcast_reader.tools.sys",
            _FrozenSys(str(bin_dir / "python"), str(tmp_path / "bundle")),
        )
        # bundle tools dir empty; interpreter dir NOT searched
        assert resolve_tool("yt-dlp") == "yt-dlp"

    def test_bundled_worker_resolved_when_frozen(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Frozen onedir bundles place worker EXEs next to the main executable."""
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        worker = bundle / "whisper-worker"
        worker.touch()
        worker.chmod(0o755)
        monkeypatch.setattr(
            "podcast_reader.tools.sys",
            _FrozenSys(str(bundle / "engine"), str(bundle)),
        )
        assert resolve_bundled_worker("whisper-worker") == str(worker)

    def test_bundled_worker_none_when_unfrozen(self) -> None:
        assert resolve_bundled_worker("whisper-worker") is None

    def test_bundled_worker_none_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        monkeypatch.setattr(
            "podcast_reader.tools.sys",
            _FrozenSys(str(bundle / "engine"), str(bundle)),
        )
        assert resolve_bundled_worker("whisper-worker") is None

    def test_frozen_bundle_tools_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Under sys.frozen the bundle's tools directory is searched."""
        bundle = tmp_path / "bundle"
        (bundle / "tools").mkdir(parents=True)
        exe = bundle / "tools" / "yt-dlp"
        exe.touch()
        exe.chmod(0o755)
        monkeypatch.setattr(
            "podcast_reader.tools.sys",
            _FrozenSys(str(bundle / "engine.exe"), str(bundle)),
        )
        assert resolve_tool("yt-dlp") == str(exe)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
class TestChildRegistry:
    def test_run_child_captures_output(self) -> None:
        result = run_child([sys.executable, "-c", "print('out')"])
        assert result.returncode == 0
        assert result.stdout.strip() == "out"
        assert live_children() == []

    def test_run_child_propagates_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            run_child([str(tmp_path / "no-such-tool")])

    def test_kill_children_terminates_process_group(self) -> None:
        """A live registered child's whole process group dies on kill_children()."""
        results: list[int] = []

        def run() -> None:
            sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
            results.append(run_child(sleeper).returncode)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        assert _wait_for(lambda: len(live_children()) == 1)
        child_pid = live_children()[0]

        kill_children()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert live_children() == []
        assert results == [-15]  # SIGTERM
        with pytest.raises(ProcessLookupError):
            os.killpg(child_pid, 0)  # the whole process group is gone

    def test_kill_children_noop_when_registry_empty(self) -> None:
        kill_children()  # must not raise
        assert live_children() == []

    def test_run_child_kills_child_when_communicate_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """KeyboardInterrupt mid-run must not orphan the detached child (N1)."""
        import subprocess

        captured: list[subprocess.Popen[str]] = []
        real_popen = subprocess.Popen

        class SpyPopen(real_popen):  # type: ignore[type-arg]
            def communicate(self, *args: object, **kwargs: object) -> tuple[str, str]:
                captured.append(self)
                raise KeyboardInterrupt

        monkeypatch.setattr("podcast_reader.tools.subprocess.Popen", SpyPopen)
        sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
        with pytest.raises(KeyboardInterrupt):
            run_child(sleeper)

        proc = captured[0]
        assert proc.returncode is not None, "child must be killed and reaped on interrupt"
        assert live_children() == []

    def test_run_child_kills_grandchildren_when_communicate_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole process group dies on interrupt, not just the direct child (C2).

        yt-dlp spawns ffmpeg; a bare ``proc.kill()`` would orphan it. The child
        spawns its own sleeper, then signals readiness on stdout so the
        interrupt cannot race the grandchild's spawn.
        """
        import subprocess

        captured: list[subprocess.Popen[str]] = []
        real_popen = subprocess.Popen

        class SpyPopen(real_popen):  # type: ignore[type-arg]
            def communicate(self, *args: object, **kwargs: object) -> tuple[str, str]:
                captured.append(self)
                assert self.stdout is not None
                assert self.stdout.readline().strip() == "ready"
                raise KeyboardInterrupt

        monkeypatch.setattr("podcast_reader.tools.subprocess.Popen", SpyPopen)
        spawner = (
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print('ready', flush=True)\n"
            "time.sleep(60)\n"
        )
        with pytest.raises(KeyboardInterrupt):
            run_child([sys.executable, "-c", spawner])

        proc = captured[0]
        assert proc.returncode is not None, "child must be killed and reaped on interrupt"
        assert live_children() == []

        def group_gone() -> bool:
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                return True
            return False

        assert _wait_for(group_gone), "grandchild must die with the process group"
