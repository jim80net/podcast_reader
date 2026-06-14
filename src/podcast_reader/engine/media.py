"""Engine media core: classification, probe, lazy single-flight cache.

The media core owns every non-YouTube playable source for the floating video
player. The future ``/v1/media`` routes instantiate one :class:`MediaManager`
and call :meth:`MediaManager.media_info` (the ``/info`` body) and
:meth:`MediaManager.ready_path` (the byte-serving file for a ``FileResponse``).

Responsibilities, each independently testable:

- **Classification** (:func:`MediaManager._classify`): YouTube URLs (reusing
  :func:`youtube.extract_video_id` — the renderer never parses URLs) report
  ``youtube`` immediately; an existing local file is probed; an other-remote
  URL reports ``video`` intent until a download refines its kind; anything
  with no playable result is ``unavailable``.
- **Probe** (:func:`parse_ffmpeg_probe`, pure): there is **no ``ffprobe``
  dependency** (F8 — it is not guaranteed in the frozen bundle, but ``ffmpeg``
  is, proven by ``diarize.py``). Duration and the presence of a real video
  track are parsed from ``ffmpeg -i <file>`` stderr (ffmpeg prints stream info
  to stderr and exits non-zero with no output file — that is expected).
- **Lazy single-flight download** keyed by ``source_id``: an in-process map
  plus a :class:`threading.Lock` (mirroring ``pack_manager``) so concurrent
  callers JOIN one download rather than starting duplicates, independent of the
  FIFO job worker. The download stages into an identity-bound staging dir; a
  failed/partial download is discarded, never served; on success the file is
  atomically placed into the cache and a terminal ``media_state`` ``ready``
  event is published (carrying ``source_id``, never ``job_id`` — mirroring the
  pack-event split).
- **Bounded LRU cache** under ``<data_dir>/media-cache/`` keyed by
  ``source_id``, with last-access tracked in a sidecar JSON (deterministic for
  tests via an injectable clock). :func:`eviction_victims` (pure) computes the
  least-recently-used ids to drop on insert until the total size is within the
  configured cap.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from podcast_reader import youtube
from podcast_reader.engine.settings import atomic_write_json
from podcast_reader.tools import resolve_tool, run_child
from podcast_reader.types import MediaInfo, MediaKind, MediaStatus, PipelineEvent

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from podcast_reader.engine.events import EventBus
    from podcast_reader.types import LibraryEntry

logger = logging.getLogger(__name__)

CACHE_DIR_NAME = "media-cache"
STAGING_DIR_NAME = "media-staging"
ACCESS_FILE = "access.json"
PARTIAL_SUFFIX = ".part"

#: ``ffmpeg -i`` prints ``Duration: HH:MM:SS.ss, ...`` to stderr.
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
#: A real video stream line. ``(attached pic)`` marks cover art (an mjpeg
#: still embedded in an audio file) — that is NOT a playable video track.
_VIDEO_STREAM_RE = re.compile(r"Stream #\d+:\d+.*: Video:")


# --------------------------------------------------------------------------
# Pure helpers (unit tested in isolation)
# --------------------------------------------------------------------------


def parse_ffmpeg_probe(stderr: str) -> tuple[float, bool]:
    """Parse ``ffmpeg -i`` stderr into ``(duration_s, has_video_track)``.

    Returns ``(0.0, False)`` when nothing parses — the caller treats a zero
    duration as "unknown", never as an error. A ``Video:`` stream marked
    ``(attached pic)`` is cover art, not a playable track, so it does not count.
    """
    duration = 0.0
    match = _DURATION_RE.search(stderr)
    if match is not None:
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    has_video = any(
        _VIDEO_STREAM_RE.search(line) and "attached pic" not in line for line in stderr.splitlines()
    )
    return duration, has_video


def eviction_victims(
    sizes: Mapping[str, int], access: Mapping[str, float], *, cap: int
) -> list[str]:
    """Least-recently-used ids to evict so the total size fits within *cap*.

    Pure and deterministic: ids are considered oldest-access first (a missing
    access time sorts as the oldest), and dropped until the remaining total is
    at most *cap*. Returns the victims in eviction order.
    """
    total = sum(sizes.values())
    if total <= cap:
        return []
    by_oldest = sorted(sizes, key=lambda sid: (access.get(sid, float("-inf")), sid))
    victims: list[str] = []
    for sid in by_oldest:
        if total <= cap:
            break
        victims.append(sid)
        total -= sizes[sid]
    return victims


# --------------------------------------------------------------------------
# MediaManager
# --------------------------------------------------------------------------


class _Download:
    """In-flight single-flight download state for one ``source_id``."""

    __slots__ = ("done", "thread")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.thread: threading.Thread | None = None


class MediaManager:
    """Classification, probing, and the lazy single-flight media cache.

    Constructed once in ``serve_engine`` (like ``PackManager``) with the data
    dir, the shared :class:`EventBus`, the cache cap, and a ``get_entry``
    callable that resolves a ``source_id`` to its :class:`LibraryEntry`
    (decoupling the manager from the library index module). *clock* is
    injectable so cache last-access ordering is deterministic in tests.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        bus: EventBus,
        cache_max_bytes: int | Callable[[], int],
        get_entry: Callable[[str], LibraryEntry | None],
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._bus = bus
        # The cap may be a live resolver (serve_engine passes one reading the
        # current setting) so a PUT /v1/settings change applies without a
        # restart; a plain int is wrapped for callers/tests that pass a constant.
        self._cache_cap: Callable[[], int] = (
            cache_max_bytes if callable(cache_max_bytes) else (lambda: cache_max_bytes)
        )
        self._get_entry = get_entry
        self._clock = clock if clock is not None else time.time
        self._lock = threading.Lock()
        self._downloads: dict[str, _Download] = {}
        # source_ids whose download terminally failed this session: media_info
        # reports them `unavailable` instead of re-triggering on every poll
        # (cleared on a restart, when a retry is appropriate).
        self._failed: set[str] = set()

    # -- public API ------------------------------------------------------

    def media_info(self, source_id: str) -> MediaInfo:
        """The ``GET /v1/media/{id}/info`` body for *source_id*.

        YouTube and local sources resolve immediately; an uncached remote
        source reports ``preparing`` and kicks off the single-flight download
        (joining one already in flight). A cached remote source reports
        ``ready`` after touching its access time.
        """
        entry = self._get_entry(source_id)
        if entry is None:
            return _unavailable()
        kind = self._classify(entry["source"])
        if kind == "unavailable":
            return _unavailable()
        if kind == "youtube":
            youtube_id = youtube.extract_video_id(entry["source"]) or ""
            return MediaInfo(
                kind="youtube",
                youtube_id=youtube_id,
                duration_s=0.0,
                status="ready",
                progress=1.0,
            )
        if self._is_local(entry["source"]):
            return self._local_info(Path(entry["source"]))
        return self._remote_info(source_id, entry)

    def ready_path(self, source_id: str) -> Path | None:
        """The on-disk media file to byte-serve, or ``None`` when not ready.

        Local sources return their own file directly; remote sources return the
        cached file only when present (a download in flight returns ``None`` —
        the route answers 404 until the terminal ``ready`` event lands).
        """
        entry = self._get_entry(source_id)
        if entry is None:
            return None
        if self._is_local(entry["source"]):
            path = Path(entry["source"])
            return path if path.is_file() else None
        cached = self._cache_path(source_id)
        if cached.is_file():
            self._touch(source_id)
            return cached
        return None

    def join_downloads(self, timeout: float | None = None) -> None:
        """Block until all in-flight downloads finish (tests/shutdown)."""
        with self._lock:
            downloads = list(self._downloads.values())
        for download in downloads:
            download.done.wait(timeout)

    @property
    def bus(self) -> EventBus:
        """The shared publish seam media-prep events ride."""
        return self._bus

    # -- classification + probe ------------------------------------------

    def _classify(self, source: str) -> MediaKind:
        """Map a source to a player kind without downloading anything.

        YouTube wins first (caption sources never download); an existing local
        file is probed for a real video track; an other-remote URL reports
        ``video`` intent (the post-download probe refines it); anything else
        (a missing local path) is ``unavailable``.
        """
        if youtube.extract_video_id(source) is not None:
            return "youtube"
        if source.startswith(("http://", "https://")):
            return "video"
        return "video" if Path(source).is_file() else "unavailable"

    def _is_local(self, source: str) -> bool:
        return not source.startswith(("http://", "https://")) and Path(source).is_file()

    def _local_info(self, path: Path) -> MediaInfo:
        duration, has_video = self._probe(path)
        kind: MediaKind = "video" if has_video else "audio"
        return MediaInfo(
            kind=kind, youtube_id="", duration_s=duration, status="ready", progress=1.0
        )

    def _probe(self, path: Path) -> tuple[float, bool]:
        """Probe a local media file via ``ffmpeg -i`` (no ffprobe, F8)."""
        try:
            result = run_child([resolve_tool("ffmpeg"), "-hide_banner", "-i", str(path)])
        except OSError as exc:
            logger.warning("ffmpeg probe of %s could not run: %s", path, exc)
            return 0.0, False
        return parse_ffmpeg_probe(result.stderr)

    # -- remote single-flight download -----------------------------------

    def _remote_info(self, source_id: str, entry: LibraryEntry) -> MediaInfo:
        """Info for a remote source: ready when cached, unavailable when it
        previously failed, else preparing + start (single-flight)."""
        cached = self._cache_path(source_id)
        if cached.is_file():
            self._touch(source_id)
            duration, has_video = self._probe(cached)
            kind: MediaKind = "video" if has_video else "audio"
            return MediaInfo(
                kind=kind, youtube_id="", duration_s=duration, status="ready", progress=1.0
            )
        # A terminal failure must not re-trigger a download on every poll nor
        # leave the client stuck on `preparing` (cubic P1): report unavailable.
        with self._lock:
            failed = source_id in self._failed
        if failed:
            return _unavailable()
        self._ensure_download(source_id, entry["source"])
        return MediaInfo(
            kind="video", youtube_id="", duration_s=0.0, status="preparing", progress=0.0
        )

    def _ensure_download(self, source_id: str, url: str) -> None:
        """Start a single-flight download for *source_id*, or join one running.

        Mirrors ``pack_manager``'s lock discipline: the map is consulted and
        mutated under the lock so two callers cannot both spawn a worker; the
        loser simply observes ``preparing``.
        """
        with self._lock:
            if source_id in self._downloads:
                return
            download = _Download()
            self._downloads[source_id] = download
            thread = threading.Thread(
                target=self._run_download,
                args=(source_id, url, download),
                name=f"media-download-{source_id[:8]}",
                daemon=True,
            )
            download.thread = thread
        thread.start()

    def _run_download(self, source_id: str, url: str, download: _Download) -> None:
        """Download into identity-bound staging, then atomically cache + publish.

        Lazy import of ``ytdlp.download_video`` keeps this module free of the
        managed-tools import chain at module load and lets tests patch the seam
        as ``podcast_reader.engine.media.download_video``.
        """
        staging = self._staging_dir(source_id)
        try:
            self._publish_state(source_id, "preparing", message="Preparing media")
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            produced = download_video(url, staging, on_event=self._forward_event(source_id))
            self._commit(source_id, produced)
            self._publish_state(source_id, "ready", message="Media ready")
        except Exception as exc:  # the download thread must survive anything
            logger.warning("Media download for %s failed: %s", source_id, exc)
            with self._lock:
                self._failed.add(source_id)  # terminal: no re-trigger until restart
            self._publish_state(source_id, "unavailable", message=f"Media unavailable: {exc}")
        finally:
            # The partial staging dir is discarded — a partial is never served.
            shutil.rmtree(staging, ignore_errors=True)
            with self._lock:
                self._downloads.pop(source_id, None)
            download.done.set()

    def _commit(self, source_id: str, produced: Path) -> None:
        """Atomically place *produced* into the cache, then evict to the cap."""
        cache_dir = self._cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        destination = self._cache_path(source_id)
        tmp = destination.with_suffix(destination.suffix + PARTIAL_SUFFIX)
        shutil.copy2(produced, tmp)
        os.replace(tmp, destination)
        self._touch(source_id)
        # Protect the just-committed file: a single media file larger than the
        # whole cap must not evict itself (which would fire `ready` for media
        # that `ready_path` then can't serve). It stays, over cap, until the
        # next commit displaces it as the least-recently-used.
        self._evict(protect=source_id)

    # -- cache + access tracking -----------------------------------------

    def _cache_dir(self) -> Path:
        return self._data_dir / CACHE_DIR_NAME

    def _cache_path(self, source_id: str) -> Path:
        return self._cache_dir() / source_id

    def _staging_dir(self, source_id: str) -> Path:
        return self._data_dir / STAGING_DIR_NAME / source_id

    def _touch(self, source_id: str) -> None:
        with self._lock:
            access = self._load_access()
            access[source_id] = self._clock()
            self._save_access(access)

    def _evict(self, protect: str | None = None) -> None:
        """Drop least-recently-used cache files until within the byte cap.

        *protect* is never evicted even if it is the LRU victim — used to keep a
        just-committed file that alone exceeds the cap (it stays over cap rather
        than firing a `ready` for media that cannot then be served).
        """
        with self._lock:
            cache_dir = self._cache_dir()
            sizes = {
                p.name: p.stat().st_size
                for p in cache_dir.iterdir()
                if p.is_file() and p.name != ACCESS_FILE and not p.name.endswith(PARTIAL_SUFFIX)
            }
            access = self._load_access()
            victims = [
                sid
                for sid in eviction_victims(sizes, access, cap=self._cache_cap())
                if sid != protect
            ]
            for sid in victims:
                (cache_dir / sid).unlink(missing_ok=True)
                access.pop(sid, None)
            if victims:
                self._save_access(access)

    def _load_access(self) -> dict[str, float]:
        path = self._cache_dir() / ACCESS_FILE
        try:
            loaded = json.loads(path.read_text())
            if not isinstance(loaded, dict):
                raise TypeError("access map must be a JSON object")
        except (OSError, ValueError, TypeError):
            return {}
        # A malformed value (non-numeric timestamp) is corruption, not a reason
        # to crash a media request (cubic P2) — skip the bad entry, keep the rest.
        access: dict[str, float] = {}
        for sid, ts in loaded.items():
            try:
                access[str(sid)] = float(ts)
            except (TypeError, ValueError):
                continue
        return access

    def _save_access(self, access: Mapping[str, float]) -> None:
        self._cache_dir().mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._cache_dir() / ACCESS_FILE, dict(access))

    # -- events ----------------------------------------------------------

    def _forward_event(self, source_id: str) -> Callable[[PipelineEvent], None]:
        """Re-publish a download's warning/progress events as media-prep events."""

        def forward(event: PipelineEvent) -> None:
            self._bus.publish(
                PipelineEvent(
                    kind="media_progress",
                    step=None,
                    message=event["message"],
                    data={"source_id": source_id},
                )
            )

        return forward

    def _publish_state(self, source_id: str, state: MediaStatus, *, message: str) -> None:
        # Media events carry source_id and MUST NOT carry job_id, mirroring the
        # pack-event split (job_id presence is the renderer's discriminator).
        data: dict[str, Any] = {"source_id": source_id, "state": state}
        self._bus.publish(PipelineEvent(kind="media_state", step=None, message=message, data=data))


def _unavailable() -> MediaInfo:
    return MediaInfo(
        kind="unavailable", youtube_id="", duration_s=0.0, status="unavailable", progress=0.0
    )


def download_video(
    url: str,
    output_dir: Path,
    cookies: Path | None = None,
    on_event: Callable[[PipelineEvent], None] | None = None,
) -> Path:
    """Indirection over :func:`ytdlp.download_video` (patchable seam).

    Imported lazily and re-exported here so the media core can be tested by
    patching ``podcast_reader.engine.media.download_video`` without dragging
    the yt-dlp/managed-tools import chain into module load.
    """
    from podcast_reader.ytdlp import download_video as _download_video

    return _download_video(url, output_dir, cookies, on_event)
