"""Tests for podcast_reader.engine.media (the engine media core).

Subprocess (ffmpeg) and the yt-dlp download path are mocked at their seams —
never the network, never a real ffmpeg — per the project's no-auto-mock rule.
The pure helpers (ffmpeg-stderr probe parse, LRU eviction ordering) are unit
tested against captured samples and constructed sizes.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from podcast_reader.engine.events import EventBus
from podcast_reader.engine.media import (
    MediaManager,
    eviction_victims,
    parse_ffmpeg_probe,
)
from podcast_reader.types import LibraryEntry

if TYPE_CHECKING:
    from podcast_reader.types import PipelineEvent


# --------------------------------------------------------------------------
# Pure helper: ffmpeg -i stderr parsing (no ffprobe dependency, F8)
# --------------------------------------------------------------------------

# A real ``ffmpeg -i video.mp4`` stderr (truncated) — ffmpeg prints stream
# info to stderr and exits non-zero with no output file (expected).
_FFMPEG_VIDEO_STDERR = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'video.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:02:03.45, start: 0.000000, bitrate: 1200 kb/s
  Stream #0:0(und): Video: h264 (High), yuv420p, 1280x720, 1100 kb/s, 30 fps
  Stream #0:1(und): Audio: aac (LC), 44100 Hz, stereo, fltp, 128 kb/s
At least one output file must be specified
"""

_FFMPEG_AUDIO_STDERR = """\
Input #0, mp3, from 'audio.mp3':
  Metadata:
    title           : Episode 1
  Duration: 00:45:10.00, start: 0.000000, bitrate: 128 kb/s
  Stream #0:0: Audio: mp3, 44100 Hz, stereo, fltp, 128 kb/s
At least one output file must be specified
"""


class TestParseFfmpegProbe:
    def test_video_with_track(self) -> None:
        duration, has_video = parse_ffmpeg_probe(_FFMPEG_VIDEO_STDERR)
        assert duration == pytest.approx(123.45)
        assert has_video is True

    def test_audio_only(self) -> None:
        duration, has_video = parse_ffmpeg_probe(_FFMPEG_AUDIO_STDERR)
        assert duration == pytest.approx(2710.0)
        assert has_video is False

    def test_unparseable_returns_zero_duration(self) -> None:
        duration, has_video = parse_ffmpeg_probe("garbage with no duration")
        assert duration == 0.0
        assert has_video is False

    def test_image_attachment_is_not_a_video_track(self) -> None:
        """Cover art (mjpeg) attached to an mp3 must not classify as video."""
        stderr = (
            "Input #0, mp3, from 'a.mp3':\n"
            "  Duration: 00:01:00.00, start: 0.0, bitrate: 128 kb/s\n"
            "  Stream #0:0: Audio: mp3, 44100 Hz, stereo\n"
            "  Stream #0:1: Video: mjpeg (Baseline), yuvj420p, 600x600 (attached pic)\n"
            "At least one output file must be specified\n"
        )
        _duration, has_video = parse_ffmpeg_probe(stderr)
        assert has_video is False


# --------------------------------------------------------------------------
# Pure helper: LRU eviction ordering
# --------------------------------------------------------------------------


class TestEvictionVictims:
    def test_evicts_least_recently_used_until_under_cap(self) -> None:
        # sizes total 30; cap 18 → must evict to <= 18.
        sizes = {"a": 10, "b": 10, "c": 10}
        access = {"a": 1.0, "b": 2.0, "c": 3.0}  # a oldest, c newest
        victims = eviction_victims(sizes, access, cap=18)
        # Evicting "a" leaves 20 (> 18); also evict "b" → 10 (<= 18).
        assert victims == ["a", "b"]

    def test_no_eviction_when_under_cap(self) -> None:
        sizes = {"a": 5, "b": 5}
        access = {"a": 1.0, "b": 2.0}
        assert eviction_victims(sizes, access, cap=100) == []

    def test_evicts_in_strict_lru_order(self) -> None:
        sizes = {"x": 4, "y": 4, "z": 4}
        access = {"x": 30.0, "y": 10.0, "z": 20.0}  # y oldest, then z, then x
        # total 12, cap 4: drop y → 8 (>4), drop z → 4 (<=4), stop before x.
        victims = eviction_victims(sizes, access, cap=4)
        assert victims == ["y", "z"]

    def test_missing_access_time_treated_as_oldest(self) -> None:
        sizes = {"a": 10, "b": 10}
        access = {"b": 5.0}  # a has no recorded access → evicted first
        victims = eviction_victims(sizes, access, cap=10)
        assert victims == ["a"]


# --------------------------------------------------------------------------
# MediaManager — classification + MediaInfo assembly
# --------------------------------------------------------------------------


def _entry(source: str, source_id: str = "i" * 64) -> LibraryEntry:
    return LibraryEntry(
        source_id=source_id,
        source=source,
        title="t",
        html_path="t.html",
        created_at=0.0,
    )


def _manager(tmp_path: Path, **kw: object) -> MediaManager:
    return MediaManager(
        data_dir=tmp_path,
        bus=EventBus(),
        cache_max_bytes=kw.pop("cache_max_bytes", 1024**3),  # type: ignore[arg-type]
        get_entry=kw.pop("get_entry"),  # type: ignore[arg-type]
        clock=kw.pop("clock", time.time),  # type: ignore[arg-type]
    )


class TestClassifyAndInfo:
    def test_youtube_source_is_immediate_and_ready(self, tmp_path: Path) -> None:
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        mgr = _manager(tmp_path, get_entry=lambda sid: _entry(url, sid))
        info = mgr.media_info("a" * 64)
        assert info["kind"] == "youtube"
        assert info["youtube_id"] == "dQw4w9WgXcQ"
        assert info["status"] == "ready"
        assert info["progress"] == 1.0

    def test_unknown_entry_is_unavailable(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path, get_entry=lambda sid: None)
        info = mgr.media_info("a" * 64)
        assert info["kind"] == "unavailable"
        assert info["status"] == "unavailable"

    def test_missing_local_file_is_unavailable(self, tmp_path: Path) -> None:
        missing = tmp_path / "gone.mp3"
        mgr = _manager(tmp_path, get_entry=lambda sid: _entry(str(missing), sid))
        info = mgr.media_info("a" * 64)
        assert info["kind"] == "unavailable"
        assert info["status"] == "unavailable"

    def test_local_video_probes_to_ready_video(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        local = tmp_path / "clip.mp4"
        local.write_bytes(b"\x00" * 100)
        mgr = _manager(tmp_path, get_entry=lambda sid: _entry(str(local), sid))
        monkeypatch.setattr(
            "podcast_reader.engine.media.run_child",
            lambda _args: _ffmpeg_completed(_FFMPEG_VIDEO_STDERR),
        )
        info = mgr.media_info("a" * 64)
        assert info["kind"] == "video"
        assert info["status"] == "ready"
        assert info["duration_s"] == pytest.approx(123.45)

    def test_local_audio_probes_to_ready_audio(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        local = tmp_path / "ep.mp3"
        local.write_bytes(b"\x00" * 100)
        mgr = _manager(tmp_path, get_entry=lambda sid: _entry(str(local), sid))
        monkeypatch.setattr(
            "podcast_reader.engine.media.run_child",
            lambda _args: _ffmpeg_completed(_FFMPEG_AUDIO_STDERR),
        )
        info = mgr.media_info("a" * 64)
        assert info["kind"] == "audio"
        assert info["status"] == "ready"

    def test_remote_uncached_reports_preparing_and_starts_download(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        url = "https://x.com/user/status/1"
        sid = "b" * 64
        started = threading.Event()
        release = threading.Event()

        def fake_download(
            u: str, out_dir: object, cookies: object = None, on_event: object = None
        ) -> object:
            started.set()
            release.wait(2)
            produced = tmp_path / "media-staging" / sid / "dl.mp4"
            produced.parent.mkdir(parents=True, exist_ok=True)
            produced.write_bytes(b"\x00" * 10)
            return produced

        monkeypatch.setattr("podcast_reader.engine.media.download_video", fake_download)
        monkeypatch.setattr(
            "podcast_reader.engine.media.run_child",
            lambda _args: _ffmpeg_completed(_FFMPEG_VIDEO_STDERR),
        )
        mgr = _manager(tmp_path, get_entry=lambda s: _entry(url, s))
        info = mgr.media_info(sid)
        assert info["status"] == "preparing"
        assert started.wait(2)
        release.set()
        mgr.join_downloads(2)
        # Second info call: cached now → ready.
        info2 = mgr.media_info(sid)
        assert info2["status"] == "ready"


def _ffmpeg_completed(stderr: str) -> object:
    import subprocess

    # ffmpeg -i with no output exits non-zero with stream info on stderr.
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


# --------------------------------------------------------------------------
# MediaManager — single-flight download, .part discipline, cache + events
# --------------------------------------------------------------------------


class TestLazyDownload:
    def test_concurrent_requests_join_one_download(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        url = "https://x.com/user/status/2"
        sid = "c" * 64
        calls: list[str] = []
        proceed = threading.Event()

        def fake_download(
            u: str, out_dir: object, cookies: object = None, on_event: object = None
        ) -> object:
            calls.append(u)
            proceed.wait(2)
            produced = Path(str(out_dir)) / "dl.mp4"
            produced.write_bytes(b"\x00" * 10)
            return produced

        monkeypatch.setattr("podcast_reader.engine.media.download_video", fake_download)
        monkeypatch.setattr(
            "podcast_reader.engine.media.run_child",
            lambda _args: _ffmpeg_completed(_FFMPEG_VIDEO_STDERR),
        )
        mgr = _manager(tmp_path, get_entry=lambda s: _entry(url, s))

        # Two near-simultaneous info calls before the download completes.
        mgr.media_info(sid)
        mgr.media_info(sid)
        proceed.set()
        mgr.join_downloads(2)
        assert len(calls) == 1  # joined, not duplicated

    def test_failed_download_leaves_no_servable_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        url = "https://x.com/user/status/3"
        sid = "d" * 64

        def boom(
            u: str, out_dir: object, cookies: object = None, on_event: object = None
        ) -> object:
            # Write a partial into staging, then fail — it must never be served.
            (Path(str(out_dir)) / "half.part").write_bytes(b"junk")
            raise RuntimeError("network died")

        monkeypatch.setattr("podcast_reader.engine.media.download_video", boom)
        mgr = _manager(tmp_path, get_entry=lambda s: _entry(url, s))
        mgr.media_info(sid)
        mgr.join_downloads(2)
        assert mgr.ready_path(sid) is None
        # No leftover .part survives in the cache.
        assert not list((tmp_path / "media-cache").glob("*")) or all(
            not p.name.endswith(".part") for p in (tmp_path / "media-cache").iterdir()
        )

    def test_publishes_ready_event_with_source_id_not_job_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        url = "https://x.com/user/status/4"
        sid = "e" * 64
        bus = EventBus()
        q = bus.subscribe()

        def fake_download(
            u: str, out_dir: object, cookies: object = None, on_event: object = None
        ) -> object:
            produced = Path(str(out_dir)) / "dl.mp4"
            produced.write_bytes(b"\x00" * 10)
            return produced

        monkeypatch.setattr("podcast_reader.engine.media.download_video", fake_download)
        monkeypatch.setattr(
            "podcast_reader.engine.media.run_child",
            lambda _args: _ffmpeg_completed(_FFMPEG_VIDEO_STDERR),
        )
        mgr = MediaManager(
            data_dir=tmp_path,
            bus=bus,
            cache_max_bytes=1024**3,
            get_entry=lambda s: _entry(url, s),
        )
        mgr.media_info(sid)
        mgr.join_downloads(2)
        events: list[PipelineEvent] = []
        while not q.empty():
            events.append(q.get_nowait())
        states = [e for e in events if e["kind"] == "media_state"]
        ready = [e for e in states if e["data"].get("state") == "ready"]
        assert ready, f"expected a ready media_state, got {events}"
        for e in events:
            assert e["data"].get("source_id") == sid
            assert "job_id" not in e["data"]

    def test_ready_path_serves_cached_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        url = "https://x.com/user/status/5"
        sid = "f" * 64

        def fake_download(
            u: str, out_dir: object, cookies: object = None, on_event: object = None
        ) -> object:
            produced = Path(str(out_dir)) / "dl.mp4"
            produced.write_bytes(b"VIDEOBYTES")
            return produced

        monkeypatch.setattr("podcast_reader.engine.media.download_video", fake_download)
        monkeypatch.setattr(
            "podcast_reader.engine.media.run_child",
            lambda _args: _ffmpeg_completed(_FFMPEG_VIDEO_STDERR),
        )
        mgr = _manager(tmp_path, get_entry=lambda s: _entry(url, s))
        mgr.media_info(sid)
        mgr.join_downloads(2)
        path = mgr.ready_path(sid)
        assert path is not None
        assert path.read_bytes() == b"VIDEOBYTES"

    def test_local_ready_path_returns_the_source_file(self, tmp_path: Path) -> None:
        local = tmp_path / "song.mp3"
        local.write_bytes(b"LOCAL")
        mgr = _manager(tmp_path, get_entry=lambda s: _entry(str(local), s))
        path = mgr.ready_path("g" * 64)
        assert path == local

    def test_just_committed_file_over_cap_is_not_self_evicted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A single media file larger than the whole cap must survive its own
        # commit — else `ready` fires for media `ready_path` can't serve.
        url = "https://x.com/user/status/6"
        sid = "a1" * 32

        def fake_download(
            u: str, out_dir: object, cookies: object = None, on_event: object = None
        ) -> object:
            produced = Path(str(out_dir)) / "big.mp4"
            produced.write_bytes(b"\x00" * 50)  # 50 bytes > 10-byte cap
            return produced

        monkeypatch.setattr("podcast_reader.engine.media.download_video", fake_download)
        monkeypatch.setattr(
            "podcast_reader.engine.media.run_child",
            lambda _args: _ffmpeg_completed(_FFMPEG_VIDEO_STDERR),
        )
        mgr = _manager(tmp_path, cache_max_bytes=10, get_entry=lambda s: _entry(url, s))
        mgr.media_info(sid)
        mgr.join_downloads(2)
        assert mgr.ready_path(sid) is not None


class TestCacheEviction:
    def test_insert_evicts_lru_to_stay_under_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = {"t": 100.0}

        def tick() -> float:
            clock["t"] += 1.0
            return clock["t"]

        # cap holds ~2 files of 10 bytes.
        mgr = MediaManager(
            data_dir=tmp_path,
            bus=EventBus(),
            cache_max_bytes=25,
            get_entry=lambda s: _entry(f"https://x.com/{s}", s),
            clock=tick,
        )

        def make_download(name: str) -> object:
            def fake_download(
                u: str, out_dir: object, cookies: object = None, on_event: object = None
            ) -> object:
                produced = Path(str(out_dir)) / f"{name}.mp4"
                produced.write_bytes(b"\x00" * 10)
                return produced

            return fake_download

        monkeypatch.setattr(
            "podcast_reader.engine.media.run_child",
            lambda _args: _ffmpeg_completed(_FFMPEG_VIDEO_STDERR),
        )
        ids = ["1" * 64, "2" * 64, "3" * 64]
        for i, sid in enumerate(ids):
            monkeypatch.setattr("podcast_reader.engine.media.download_video", make_download(str(i)))
            mgr.media_info(sid)
            mgr.join_downloads(2)

        # Three 10-byte inserts under a 25-byte cap → the oldest (first) evicted.
        assert mgr.ready_path(ids[0]) is None
        assert mgr.ready_path(ids[1]) is not None
        assert mgr.ready_path(ids[2]) is not None
