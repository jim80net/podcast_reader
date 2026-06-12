"""Tests for engine tools seeding and the scheduled yt-dlp self-update."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

from podcast_reader.engine.managed_tools import (
    UPDATE_CHECK_INTERVAL_S,
    ToolsManifest,
    export_tools_dir,
    is_managed,
    load_user_manifest,
    maybe_self_update_ytdlp,
    save_user_manifest,
    seed_tools,
    tools_dir,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_seed_dir(root: Path, versions: dict[str, str], content_tag: str = "seed") -> Path:
    """A bundle-like tools dir: executable seed files plus the seed manifest."""
    seed_dir = root / "bundle-tools"
    seed_dir.mkdir(parents=True, exist_ok=True)
    for name in versions:
        seed = seed_dir / name
        seed.write_text(f"#!/bin/sh\necho {content_tag}-{name}\n")
        seed.chmod(0o755)
    (seed_dir / "tools-manifest.json").write_text(json.dumps(versions))
    return seed_dir


_SEED_VERSIONS = {"yt-dlp": "2026.05.01", "ffmpeg": "7.1", "ffprobe": "7.1"}


class TestSeedTools:
    def test_first_run_seeds_all_tools(self, tmp_path: Path) -> None:
        """Spec scenario: first run copies yt-dlp/ffmpeg/ffprobe with a
        manifest recording their versions, preserving execute bits."""
        seed_dir = _make_seed_dir(tmp_path, _SEED_VERSIONS)
        base = tmp_path / "data"

        seed_tools(base, seed_dir)

        for name in _SEED_VERSIONS:
            copy = tools_dir(base) / name
            assert copy.read_text() == f"#!/bin/sh\necho seed-{name}\n"
            if os.name == "posix":
                assert stat.S_IMODE(copy.stat().st_mode) & stat.S_IXUSR
        manifest = load_user_manifest(base)
        assert manifest["versions"] == _SEED_VERSIONS
        assert manifest["last_update_check"] == 0.0

    def test_newer_seed_replaces_older_user_copy(self, tmp_path: Path) -> None:
        """Spec scenario: an app update shipping a newer seed replaces the
        user-data copy and updates the recorded version."""
        base = tmp_path / "data"
        seed_tools(base, _make_seed_dir(tmp_path, {"yt-dlp": "2026.01.01"}, "old"))
        seed_tools(base, _make_seed_dir(tmp_path, {"yt-dlp": "2026.05.01"}, "new"))

        assert (tools_dir(base) / "yt-dlp").read_text() == "#!/bin/sh\necho new-yt-dlp\n"
        assert load_user_manifest(base)["versions"]["yt-dlp"] == "2026.05.01"

    def test_self_updated_user_copy_preserved(self, tmp_path: Path) -> None:
        """Spec scenario: a user-data copy recorded newer (a prior -U) wins."""
        base = tmp_path / "data"
        seed_tools(base, _make_seed_dir(tmp_path, {"yt-dlp": "2026.01.01"}, "old"))
        manifest = load_user_manifest(base)
        manifest["versions"]["yt-dlp"] = "2026.09.09"  # self-updated since
        save_user_manifest(base, manifest)
        (tools_dir(base) / "yt-dlp").write_text("self-updated binary")

        seed_tools(base, _make_seed_dir(tmp_path, {"yt-dlp": "2026.05.01"}, "new"))

        assert (tools_dir(base) / "yt-dlp").read_text() == "self-updated binary"
        assert load_user_manifest(base)["versions"]["yt-dlp"] == "2026.09.09"

    def test_equal_version_leaves_copy_untouched(self, tmp_path: Path) -> None:
        base = tmp_path / "data"
        seed_tools(base, _make_seed_dir(tmp_path, {"yt-dlp": "2026.05.01"}, "first"))
        seed_tools(base, _make_seed_dir(tmp_path, {"yt-dlp": "2026.05.01"}, "second"))

        assert (tools_dir(base) / "yt-dlp").read_text() == "#!/bin/sh\necho first-yt-dlp\n"

    def test_missing_user_file_reseeded_even_when_version_recorded(self, tmp_path: Path) -> None:
        """A deleted user-data binary is re-seeded regardless of the recorded
        version — the lookup-time invariant needs the file, not the record."""
        base = tmp_path / "data"
        seed_dir = _make_seed_dir(tmp_path, {"yt-dlp": "2026.05.01"})
        seed_tools(base, seed_dir)
        (tools_dir(base) / "yt-dlp").unlink()

        seed_tools(base, seed_dir)

        assert (tools_dir(base) / "yt-dlp").exists()

    def test_no_seed_dir_is_a_noop(self, tmp_path: Path) -> None:
        base = tmp_path / "data"
        seed_tools(base, None)  # unfrozen: bundle_tools_dir() is None
        seed_tools(base, tmp_path / "absent")  # frozen but no manifest shipped
        assert not tools_dir(base).exists()

    def test_seed_failure_logs_and_continues(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Spec scenario: a failing copy logs a warning and the engine serves
        normally — and the other tools still get seeded."""
        seed_dir = _make_seed_dir(tmp_path, _SEED_VERSIONS)
        base = tmp_path / "data"
        real_copy2 = shutil.copy2

        def failing_copy(src: object, dst: object) -> object:
            if "yt-dlp" in str(src):
                raise OSError("permission denied")
            return real_copy2(src, dst)

        with patch("podcast_reader.engine.managed_tools.shutil.copy2", side_effect=failing_copy):
            seed_tools(base, seed_dir)  # must not raise

        assert "yt-dlp" in caplog.text and "failed" in caplog.text
        assert not (tools_dir(base) / "yt-dlp").exists()
        assert (tools_dir(base) / "ffmpeg").exists()
        assert load_user_manifest(base)["versions"].get("ffmpeg") == "7.1"


class TestExportToolsDir:
    def test_sets_when_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PODCAST_READER_TOOLS_DIR", raising=False)
        try:
            export_tools_dir(tmp_path)
            assert os.environ["PODCAST_READER_TOOLS_DIR"] == str(tmp_path / "tools")
        finally:
            os.environ.pop("PODCAST_READER_TOOLS_DIR", None)

    def test_explicit_override_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Spec scenario: an already-set variable is never overwritten."""
        monkeypatch.setenv("PODCAST_READER_TOOLS_DIR", "/custom/tools")
        export_tools_dir(tmp_path)
        assert os.environ["PODCAST_READER_TOOLS_DIR"] == "/custom/tools"


class TestIsManaged:
    def test_true_inside_user_tools_dir(self, tmp_path: Path) -> None:
        binary = tools_dir(tmp_path) / "yt-dlp"
        binary.parent.mkdir(parents=True)
        binary.touch()
        assert is_managed(str(binary), tmp_path) is True

    def test_false_elsewhere(self, tmp_path: Path) -> None:
        assert is_managed("/usr/bin/yt-dlp", tmp_path) is False
        assert is_managed("yt-dlp", tmp_path) is False  # bare PATH name

    def test_defaults_to_data_dir_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        assert is_managed(str(tools_dir(tmp_path) / "yt-dlp")) is True
        assert not tmp_path.joinpath("never").exists()  # no mkdir side effect


def _completed(
    args: list[str], returncode: int = 0, stdout: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


class TestMaybeSelfUpdate:
    def _managed_binary(self, base: Path) -> Path:
        binary = tools_dir(base) / "yt-dlp"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.touch()
        return binary

    def test_stale_check_runs_update_and_records(self, tmp_path: Path) -> None:
        """Spec scenario: stale check triggers `yt-dlp -U` against the
        user-data copy; the new version comes from `yt-dlp --version`."""
        binary = self._managed_binary(tmp_path)
        responses = {
            "-U": _completed([], 0, "Updated yt-dlp\n"),
            "--version": _completed([], 0, "2026.06.06\n"),
        }

        def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
            assert args[0] == str(binary)
            return responses[args[1]]

        with (
            patch("podcast_reader.engine.managed_tools.resolve_tool", return_value=str(binary)),
            patch("podcast_reader.engine.managed_tools.run_child", side_effect=fake_run) as run,
        ):
            updated = maybe_self_update_ytdlp(tmp_path, now=UPDATE_CHECK_INTERVAL_S + 1.0)

        assert updated is True
        assert [c.args[0][1] for c in run.call_args_list] == ["-U", "--version"]
        manifest = load_user_manifest(tmp_path)
        assert manifest["versions"]["yt-dlp"] == "2026.06.06"
        assert manifest["last_update_check"] == UPDATE_CHECK_INTERVAL_S + 1.0

    def test_fresh_check_skips(self, tmp_path: Path) -> None:
        self._managed_binary(tmp_path)
        save_user_manifest(tmp_path, ToolsManifest(versions={}, last_update_check=1_000_000.0))
        with patch("podcast_reader.engine.managed_tools.run_child") as run:
            updated = maybe_self_update_ytdlp(tmp_path, now=1_000_000.0 + 60.0)
        assert updated is False
        run.assert_not_called()

    def test_unmanaged_binary_never_updated(self, tmp_path: Path) -> None:
        """Spec scenario: dev environments untouched — a PATH-resolved yt-dlp
        is never self-updated."""
        with (
            patch(
                "podcast_reader.engine.managed_tools.resolve_tool",
                return_value="/usr/bin/yt-dlp",
            ),
            patch("podcast_reader.engine.managed_tools.run_child") as run,
        ):
            updated = maybe_self_update_ytdlp(tmp_path, now=UPDATE_CHECK_INTERVAL_S + 1.0)
        assert updated is False
        run.assert_not_called()

    def test_failed_update_not_recorded_so_next_start_retries(self, tmp_path: Path) -> None:
        binary = self._managed_binary(tmp_path)
        with (
            patch("podcast_reader.engine.managed_tools.resolve_tool", return_value=str(binary)),
            patch(
                "podcast_reader.engine.managed_tools.run_child",
                return_value=_completed([], returncode=1),
            ),
        ):
            updated = maybe_self_update_ytdlp(tmp_path, now=UPDATE_CHECK_INTERVAL_S + 1.0)
        assert updated is False
        assert load_user_manifest(tmp_path)["last_update_check"] == 0.0

    def test_never_raises(self, tmp_path: Path) -> None:
        """Background-thread contract: any internal explosion is swallowed."""
        with patch(
            "podcast_reader.engine.managed_tools.resolve_tool",
            side_effect=RuntimeError("boom"),
        ):
            assert maybe_self_update_ytdlp(tmp_path, now=UPDATE_CHECK_INTERVAL_S + 1.0) is False
