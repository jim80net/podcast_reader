"""Tests for podcast_reader.engine.pack_manager (downloader + installer).

All downloads run through ``httpx.MockTransport`` — never the network.
"""

from __future__ import annotations

import hashlib
import io
import os
import pathlib
import threading
import time
import zipfile
from typing import TYPE_CHECKING

import httpx
import pytest

from podcast_reader.engine.events import EventBus
from podcast_reader.engine.pack_manager import (
    InstallAbortedError,
    PackDownloadError,
    PackInstallingError,
    PackManager,
    PackUnavailableError,
    UnknownPackError,
    discard_stale_partials,
    download_file,
    partial_path,
)
from podcast_reader.engine.packs import (
    REGISTRY,
    LicenseNotice,
    PackEntry,
    PackFilePin,
    manifest_path,
    pack_dir,
    read_manifest,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from podcast_reader.engine.packs import PackManifest


def _pin(content: bytes, path: str = "file.bin") -> PackFilePin:
    return PackFilePin(
        path=path,
        url=f"https://packs.example.com/{path}",
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def _serving(
    bodies: dict[str, bytes], requests: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    """A Range-aware mock file host."""

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        body = bodies[str(request.url)]
        range_header = request.headers.get("range")
        if range_header:
            start = int(range_header.removeprefix("bytes=").rstrip("-"))
            return httpx.Response(206, content=body[start:])
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler)


def _client(transport: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=transport)


class TestDownloadFile:
    def test_fresh_download_stages_by_sha256_and_verifies(self, tmp_path: Path) -> None:
        """Staging partials carry their identity: named by expected sha256
        (per S2)."""
        content = b"x" * 1000
        pin = _pin(content)
        with _client(_serving({pin["url"]: content})) as client:
            part = download_file(client, pin, tmp_path)
        assert part == tmp_path / f"{pin['sha256']}.part"
        assert part.read_bytes() == content

    def test_resume_sends_range_from_partial_offset(self, tmp_path: Path) -> None:
        """Spec scenario: Interrupted download resumes — continues from the
        partial file's byte offset rather than restarting from zero."""
        content = b"abcdefghij" * 100
        pin = _pin(content)
        partial_path(tmp_path, pin).write_bytes(content[:300])
        requests: list[httpx.Request] = []
        with _client(_serving({pin["url"]: content}, requests)) as client:
            part = download_file(client, pin, tmp_path)
        assert [r.headers.get("range") for r in requests] == ["bytes=300-"]
        assert part.read_bytes() == content

    def test_server_ignoring_range_restarts_from_zero(self, tmp_path: Path) -> None:
        """A 200 despite Range means the server ignored it: restart that file
        from zero rather than appending a duplicate prefix."""
        content = b"0123456789" * 50
        pin = _pin(content)
        partial_path(tmp_path, pin).write_bytes(content[:100])

        def ignore_range(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=content)

        with _client(httpx.MockTransport(ignore_range)) as client:
            part = download_file(client, pin, tmp_path)
        assert part.read_bytes() == content

    def test_complete_partial_skips_the_network(self, tmp_path: Path) -> None:
        content = b"already-complete"
        pin = _pin(content)
        partial_path(tmp_path, pin).write_bytes(content)
        requests: list[httpx.Request] = []
        with _client(_serving({pin["url"]: content}, requests)) as client:
            part = download_file(client, pin, tmp_path)
        assert requests == []
        assert part.read_bytes() == content

    def test_sha256_mismatch_fails_closed_and_deletes_partial(self, tmp_path: Path) -> None:
        """Spec scenario: Hash mismatch fails closed — corrupt content is
        never installed."""
        content = b"expected-bytes"
        pin = _pin(content)
        with (
            _client(_serving({pin["url"]: b"tampered-bytes"})) as client,
            pytest.raises(PackDownloadError, match="sha256"),
        ):
            download_file(client, pin, tmp_path)
        assert not partial_path(tmp_path, pin).exists()

    def test_http_error_raises_structured(self, tmp_path: Path) -> None:
        pin = _pin(b"body")

        def gone(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        with (
            _client(httpx.MockTransport(gone)) as client,
            pytest.raises(PackDownloadError, match="404"),
        ):
            download_file(client, pin, tmp_path)

    def test_stop_mid_stream_aborts_and_keeps_partial(self, tmp_path: Path) -> None:
        """Engine shutdown mid-download leaves a resumable partial."""
        content = b"z" * (256 * 1024)
        pin = _pin(content)
        calls = {"n": 0}

        def stop_after_first_chunk() -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        with (
            _client(_serving({pin["url"]: content})) as client,
            pytest.raises(InstallAbortedError),
        ):
            download_file(client, pin, tmp_path, should_stop=stop_after_first_chunk)
        part = partial_path(tmp_path, pin)
        assert part.exists()
        assert 0 < part.stat().st_size < pin["size"]

    def test_on_progress_reports_absolute_bytes(self, tmp_path: Path) -> None:
        content = b"y" * 100_000
        pin = _pin(content)
        seen: list[int] = []
        with _client(_serving({pin["url"]: content})) as client:
            download_file(client, pin, tmp_path, on_progress=seen.append)
        assert seen, "progress callback never fired"
        assert seen == sorted(seen)
        assert seen[-1] == pin["size"]

    def test_resume_progress_starts_at_partial_offset(self, tmp_path: Path) -> None:
        content = b"q" * 1000
        pin = _pin(content)
        partial_path(tmp_path, pin).write_bytes(content[:400])
        seen: list[int] = []
        with _client(_serving({pin["url"]: content})) as client:
            download_file(client, pin, tmp_path, on_progress=seen.append)
        assert all(value > 400 for value in seen)
        assert seen[-1] == pin["size"]


class TestStalePartials:
    def test_partials_outside_current_pins_are_discarded(self, tmp_path: Path) -> None:
        """Spec scenario: Stale partial discarded after a pin bump (per S2)."""
        keep = _pin(b"current-content")
        partial_path(tmp_path, keep).write_bytes(b"cur")
        stale = tmp_path / ("ab" * 32 + ".part")
        stale.write_bytes(b"old-revision-bytes")

        discard_stale_partials(tmp_path, {keep["sha256"]})

        assert partial_path(tmp_path, keep).exists()
        assert not stale.exists()

    def test_non_part_files_untouched(self, tmp_path: Path) -> None:
        other = tmp_path / "notes.txt"
        other.write_text("keep me")
        discard_stale_partials(tmp_path, set())
        assert other.exists()


# ---------------------------------------------------------------------------
# PackManager (installer thread, atomic install, manifest-first uninstall,
# startup validation)
# ---------------------------------------------------------------------------


def _wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


_MODEL_CONTENT = {
    "model.bin": b"model-weights-bytes" * 10,
    "config.json": b'{"model": true}',
}


def _test_entry(
    pack_id: str = "model-test",
    *,
    contents: dict[str, bytes] | None = None,
    platforms: list[str] | None = None,
    extract_wheels: bool = False,
    compat: dict[str, str] | None = None,
    licenses: list[LicenseNotice] | None = None,
) -> tuple[PackEntry, dict[str, bytes]]:
    """A synthetic published pack plus its URL->bytes body map."""
    contents = contents if contents is not None else _MODEL_CONTENT
    pins = [_pin(body, path=name) for name, body in contents.items()]
    entry = PackEntry(
        id=pack_id,
        kind="model",
        display_name="Test pack",
        platforms=platforms,
        install_dir=f"models/{pack_id}",
        extract_wheels=extract_wheels,
        files=pins,
        version="rev-1",
        component_versions={"model_revision": "rev-1"},
        compat=compat if compat is not None else {},
        licenses=licenses if licenses is not None else [],
    )
    bodies = {pin["url"]: contents[pin["path"]] for pin in pins}
    return entry, bodies


class _Harness:
    """PackManager + mock transport + a subscribed bus queue."""

    def __init__(
        self,
        data_dir: Path,
        registry: dict[str, PackEntry],
        bodies: dict[str, bytes],
        *,
        platform: str = "linux",
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.gate: threading.Event | None = None
        self.entered = threading.Event()

        def handler(request: httpx.Request) -> httpx.Response:
            self.entered.set()
            if self.gate is not None:
                assert self.gate.wait(timeout=10)
            self.requests.append(request)
            body = bodies[str(request.url)]
            range_header = request.headers.get("range")
            if range_header:
                start = int(range_header.removeprefix("bytes=").rstrip("-"))
                return httpx.Response(206, content=body[start:])
            return httpx.Response(200, content=body)

        self.bus = EventBus()
        self.events = self.bus.subscribe()
        self.manager = PackManager(
            data_dir,
            bus=self.bus,
            registry=registry,
            transport=httpx.MockTransport(handler),
            platform=platform,
            progress_step=1,
        )

    def drain_events(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        while True:
            try:
                out.append(dict(self.events.get_nowait()))
            except Exception:
                return out

    def state_of(self, pack_id: str) -> str:
        for status in self.manager.statuses():
            if status["id"] == pack_id:
                return status["state"]
        raise AssertionError(f"pack {pack_id} not listed")

    def status_of(self, pack_id: str) -> dict[str, object]:
        for status in self.manager.statuses():
            if status["id"] == pack_id:
                return dict(status)
        raise AssertionError(f"pack {pack_id} not listed")


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[_Harness]:
    entry, bodies = _test_entry()
    h = _Harness(tmp_path, {entry["id"]: entry}, bodies)
    h.manager.start_worker()
    yield h
    if h.gate is not None:
        h.gate.set()
    h.manager.shutdown()


class TestInstall:
    def test_install_places_files_and_writes_manifest_last(
        self, harness: _Harness, tmp_path: Path
    ) -> None:
        harness.manager.request_install("model-test")
        assert _wait_for(lambda: harness.state_of("model-test") == "installed")
        target = tmp_path / "models" / "model-test"
        for name, body in _MODEL_CONTENT.items():
            assert (target / name).read_bytes() == body
        manifest = read_manifest(target)
        assert manifest is not None
        assert manifest["id"] == "model-test"
        assert manifest["version"] == "rev-1"
        assert {f["path"] for f in manifest["files"]} == set(_MODEL_CONTENT)
        status = harness.status_of("model-test")
        assert status["installed_version"] == "rev-1"

    def test_installed_survives_restart(self, harness: _Harness, tmp_path: Path) -> None:
        """Spec scenario: Compatible packs pass silently — a fresh manager
        derives installed state from disk."""
        harness.manager.request_install("model-test")
        assert _wait_for(lambda: harness.state_of("model-test") == "installed")
        entry, bodies = _test_entry()
        fresh = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        assert fresh.state_of("model-test") == "installed"

    def test_pack_events_flow_with_pack_id_and_never_job_id(self, harness: _Harness) -> None:
        """Spec scenario: Job event consumers unaffected (per Q5) — pack
        events are self-describing by kind, carry pack_id, and MUST NOT
        carry a job_id field."""
        harness.manager.request_install("model-test")
        assert _wait_for(lambda: harness.state_of("model-test") == "installed")
        events = harness.drain_events()
        kinds = {e["kind"] for e in events}
        assert "pack_state" in kinds
        assert "pack_progress" in kinds
        for event in events:
            data = event["data"]
            assert isinstance(data, dict)
            assert data["pack_id"] == "model-test"
            assert "job_id" not in data, f"pack event leaked job_id: {event}"

    def test_progress_events_report_monotonic_bytes(self, harness: _Harness) -> None:
        """Spec scenario: Progress observable live."""
        harness.manager.request_install("model-test")
        assert _wait_for(lambda: harness.state_of("model-test") == "installed")
        progress = [e for e in harness.drain_events() if e["kind"] == "pack_progress"]
        assert progress
        sizes = [e["data"]["bytes"] for e in progress]  # type: ignore[index]
        assert sizes == sorted(sizes)
        total = sum(len(b) for b in _MODEL_CONTENT.values())
        assert progress[-1]["data"]["total"] == total  # type: ignore[index]

    def test_duplicate_request_while_installing_is_idempotent(self, harness: _Harness) -> None:
        """Spec scenario: Duplicate install request is idempotent — no
        second download starts."""
        harness.gate = threading.Event()
        harness.manager.request_install("model-test")
        assert harness.entered.wait(timeout=10)
        assert harness.state_of("model-test") == "installing"
        harness.manager.request_install("model-test")  # idempotent, no error
        harness.gate.set()
        assert _wait_for(lambda: harness.state_of("model-test") == "installed")
        urls = [str(r.url) for r in harness.requests]
        assert len(urls) == len(set(urls)), f"a file downloaded twice: {urls}"

    def test_request_for_installed_pack_does_no_work(self, harness: _Harness) -> None:
        harness.manager.request_install("model-test")
        assert _wait_for(lambda: harness.state_of("model-test") == "installed")
        first_count = len(harness.requests)
        harness.manager.request_install("model-test")
        time.sleep(0.1)  # give a wrongly-enqueued install time to surface
        assert len(harness.requests) == first_count

    def test_unknown_pack_raises(self, harness: _Harness) -> None:
        with pytest.raises(UnknownPackError):
            harness.manager.request_install("nonsense")
        with pytest.raises(UnknownPackError):
            harness.manager.uninstall("nonsense")

    def test_unpublished_pack_not_installable(self, tmp_path: Path) -> None:
        """Spec scenario: Unpublished pack is not installable (per S5) —
        against the real registry's diarization entry."""
        manager = PackManager(tmp_path, registry=REGISTRY, platform="linux")
        with pytest.raises(PackUnavailableError):
            manager.request_install("diarization")
        states = {s["id"]: s["state"] for s in manager.statuses()}
        assert states["diarization"] == "unavailable"

    def test_platform_gated_pack_not_installable(self, tmp_path: Path) -> None:
        """Spec scenario: Platform-gated pack excluded — CUDA off-Windows."""
        manager = PackManager(tmp_path, registry=REGISTRY, platform="darwin")
        with pytest.raises(PackUnavailableError):
            manager.request_install("cuda-runtime")
        states = {s["id"]: s["state"] for s in manager.statuses()}
        assert states["cuda-runtime"] == "unavailable"

    def test_sha_mismatch_marks_failed_without_manifest(self, tmp_path: Path) -> None:
        """Spec scenario: Hash mismatch fails closed (manager level)."""
        entry, bodies = _test_entry()
        tampered = {url: b"tampered!" + body for url, body in bodies.items()}
        h = _Harness(tmp_path, {entry["id"]: entry}, tampered)
        h.manager.start_worker()
        try:
            h.manager.request_install("model-test")
            assert _wait_for(lambda: h.state_of("model-test") == "failed")
            status = h.status_of("model-test")
            error = status["error"]
            assert isinstance(error, dict)
            assert error["code"] == "verification_failed"
            assert "sha256" in str(error["message"])
            assert read_manifest(pack_dir(tmp_path, entry)) is None
            failed_events = [
                e
                for e in h.drain_events()
                if e["kind"] == "pack_state" and e["data"]["state"] == "failed"  # type: ignore[index]
            ]
            assert failed_events
        finally:
            h.manager.shutdown()

    def test_failed_install_can_be_retried(self, tmp_path: Path) -> None:
        entry, bodies = _test_entry()
        h = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        # first attempt fails: poison one body via a mutable mapping
        poisoned = dict(bodies)
        first_url = next(iter(bodies))
        poisoned[first_url] = b"wrong-bytes"
        h2 = _Harness(tmp_path, {entry["id"]: entry}, poisoned)
        h2.manager.start_worker()
        try:
            h2.manager.request_install("model-test")
            assert _wait_for(lambda: h2.state_of("model-test") == "failed")
        finally:
            h2.manager.shutdown()
        # retry against the healthy host succeeds and clears the error
        h.manager.start_worker()
        try:
            h.manager.request_install("model-test")
            assert _wait_for(lambda: h.state_of("model-test") == "installed")
            assert h.status_of("model-test")["error"] is None
        finally:
            h.manager.shutdown()


class TestResumeAndStaging:
    def test_crash_mid_install_leaves_no_phantom_pack(self, tmp_path: Path) -> None:
        """Spec scenario: Crash mid-install leaves no phantom pack — files
        without a manifest are not installed."""
        entry, bodies = _test_entry()
        target = pack_dir(tmp_path, entry)
        target.mkdir(parents=True)
        (target / "model.bin").write_bytes(b"staged-before-crash")
        h = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        assert h.state_of("model-test") == "not-installed"

    def test_partial_download_surfaces_as_resumable(self, tmp_path: Path) -> None:
        """Spec scenario: Partial download surfaces as resumable."""
        entry, bodies = _test_entry()
        h = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        pin = entry["files"][0]  # type: ignore[index]
        staging = h.manager.staging_dir("model-test")
        staging.mkdir(parents=True)
        partial_path(staging, pin).write_bytes(b"first-bytes")
        assert h.state_of("model-test") == "resumable"

    def test_reinstall_resumes_from_partial_offset(self, tmp_path: Path) -> None:
        """Spec scenario: Interrupted download resumes (manager level)."""
        entry, bodies = _test_entry()
        h = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        pin = entry["files"][0]  # type: ignore[index]
        body = bodies[pin["url"]]
        staging = h.manager.staging_dir("model-test")
        staging.mkdir(parents=True)
        partial_path(staging, pin).write_bytes(body[:10])
        h.manager.start_worker()
        try:
            h.manager.request_install("model-test")
            assert _wait_for(lambda: h.state_of("model-test") == "installed")
        finally:
            h.manager.shutdown()
        ranged = [r.headers.get("range") for r in h.requests if str(r.url) == pin["url"]]
        assert ranged == ["bytes=10-"]

    def test_stale_partial_discarded_at_install_start(self, tmp_path: Path) -> None:
        """Spec scenario: Stale partial discarded after a pin bump (per S2,
        manager level) — the stale partial never resumes."""
        entry, bodies = _test_entry()
        h = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        staging = h.manager.staging_dir("model-test")
        staging.mkdir(parents=True)
        stale = staging / ("ff" * 32 + ".part")
        stale.write_bytes(b"bytes-of-the-previous-revision")
        h.manager.start_worker()
        try:
            h.manager.request_install("model-test")
            assert _wait_for(lambda: h.state_of("model-test") == "installed")
        finally:
            h.manager.shutdown()
        assert not stale.exists()
        assert all(r.headers.get("range") is None for r in h.requests)


class TestReinstall:
    def test_old_manifest_removed_before_new_files_land(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T1: reinstall mirrors uninstall's manifest-first discipline (per
        S1) — a validator racing the reinstall sees not-installed, never the
        OLD manifest describing mixed old/new files."""
        entry, bodies = _test_entry()
        target = pack_dir(tmp_path, entry)
        h1 = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        h1.manager.start_worker()
        try:
            h1.manager.request_install("model-test")
            assert _wait_for(lambda: h1.state_of("model-test") == "installed")
        finally:
            h1.manager.shutdown()

        # The "updated" registry pins new content and moves the compat range,
        # so the rev-1 install reads incompatible -> re-download affordance.
        new_contents = {name: b"rev-2:" + body for name, body in _MODEL_CONTENT.items()}
        updated_entry, new_bodies = _test_entry(contents=new_contents)
        updated = dict(updated_entry)
        updated["version"] = "rev-2"
        updated["component_versions"] = {"model_revision": "rev-2"}
        updated["compat"] = {"model_revision": "rev-2"}

        manifests_at_replace: list[PackManifest | None] = []
        real_replace = os.replace

        def spying_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
            manifests_at_replace.append(read_manifest(target))
            real_replace(src, dst)

        monkeypatch.setattr("podcast_reader.engine.pack_manager.os.replace", spying_replace)
        h2 = _Harness(tmp_path, {entry["id"]: dict(updated)}, new_bodies)  # type: ignore[arg-type]
        assert h2.state_of("model-test") == "incompatible"
        h2.manager.start_worker()
        try:
            h2.manager.request_install("model-test")
            assert _wait_for(lambda: h2.state_of("model-test") == "installed")
        finally:
            h2.manager.shutdown()

        assert manifests_at_replace, "os.replace never observed"
        # Every file placement (and the manifest write itself) happened with
        # NO manifest on disk: mid-install reads as not-installed.
        assert manifests_at_replace == [None] * len(manifests_at_replace)
        manifest = read_manifest(target)
        assert manifest is not None
        assert manifest["version"] == "rev-2"
        for name, body in new_contents.items():
            assert (target / name).read_bytes() == body


class TestUninstall:
    def _install(self, harness: _Harness) -> None:
        harness.manager.request_install("model-test")
        assert _wait_for(lambda: harness.state_of("model-test") == "installed")

    def test_uninstall_removes_manifest_and_files(self, harness: _Harness, tmp_path: Path) -> None:
        """Spec scenario: Uninstall removes the pack."""
        self._install(harness)
        harness.manager.uninstall("model-test")
        target = tmp_path / "models" / "model-test"
        assert read_manifest(target) is None
        for name in _MODEL_CONTENT:
            assert not (target / name).exists()
        assert harness.state_of("model-test") == "not-installed"

    def test_uninstall_deletes_manifest_first(
        self, harness: _Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Per S1: the manifest goes first — even when file removal dies
        mid-way, the pack is already atomically not-installed."""
        self._install(harness)

        def boom(pack_dir_path: Path, manifest: PackManifest) -> None:
            raise OSError("file removal interrupted")

        monkeypatch.setattr(PackManager, "_remove_files", staticmethod(boom))
        with pytest.raises(OSError, match="interrupted"):
            harness.manager.uninstall("model-test")
        target = tmp_path / "models" / "model-test"
        assert read_manifest(target) is None  # manifest already gone
        assert (target / "model.bin").exists()  # files orphaned, pack not installed
        assert harness.state_of("model-test") == "not-installed"

    def test_locked_file_does_not_fail_uninstall(
        self,
        harness: _Harness,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """T3: a Windows-style PermissionError on one in-use file logs and
        continues — the manifest is already gone, so the pack IS
        uninstalled; the leftover bytes are reclaimed on reinstall or a
        later uninstall sweep."""
        self._install(harness)
        real_unlink = pathlib.Path.unlink

        def locked(path: pathlib.Path, missing_ok: bool = False) -> None:
            if path.name == "model.bin":
                raise PermissionError(13, "file in use", str(path))
            real_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(pathlib.Path, "unlink", locked)
        with caplog.at_level("WARNING", logger="podcast_reader.engine.pack_manager"):
            harness.manager.uninstall("model-test")  # must not raise
        target = tmp_path / "models" / "model-test"
        assert read_manifest(target) is None
        assert (target / "model.bin").exists()  # orphaned, reclaimed later
        assert not (target / "config.json").exists()  # the rest still removed
        assert harness.state_of("model-test") == "not-installed"
        assert any("model.bin" in record.getMessage() for record in caplog.records)

    def test_uninstall_while_installing_is_409(self, harness: _Harness) -> None:
        """Spec scenario: Uninstall refused while installing."""
        harness.gate = threading.Event()
        harness.manager.request_install("model-test")
        assert harness.entered.wait(timeout=10)
        with pytest.raises(PackInstallingError):
            harness.manager.uninstall("model-test")
        harness.gate.set()
        assert _wait_for(lambda: harness.state_of("model-test") == "installed")

    def test_uninstall_not_installed_is_idempotent(self, harness: _Harness) -> None:
        harness.manager.uninstall("model-test")  # no error
        assert harness.state_of("model-test") == "not-installed"

    def test_uninstall_clears_resumable_partials(self, harness: _Harness) -> None:
        staging = harness.manager.staging_dir("model-test")
        staging.mkdir(parents=True)
        (staging / ("aa" * 32 + ".part")).write_bytes(b"partial")
        harness.manager.uninstall("model-test")
        assert harness.state_of("model-test") == "not-installed"


class TestStartupValidation:
    def _installed(self, tmp_path: Path, entry: PackEntry, bodies: dict[str, bytes]) -> None:
        h = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        h.manager.start_worker()
        try:
            h.manager.request_install(entry["id"])
            assert _wait_for(lambda: h.state_of(entry["id"]) == "installed")
        finally:
            h.manager.shutdown()

    def test_compat_range_moved_by_update_flags_incompatible(self, tmp_path: Path) -> None:
        """Spec scenario: App update moves the compat range."""
        entry, bodies = _test_entry()
        self._installed(tmp_path, entry, bodies)
        # the "updated" registry now requires a different component major
        updated = dict(entry)
        updated["compat"] = {"model_revision": "rev2"}
        fresh = _Harness(tmp_path, {entry["id"]: dict(updated)}, bodies)  # type: ignore[arg-type]
        assert fresh.state_of("model-test") == "incompatible"
        flagged = fresh.manager.validate_installed()
        assert "model-test" in flagged

    def test_missing_file_flags_failed_with_error(self, tmp_path: Path) -> None:
        """Spec scenario: Missing or truncated pack file detected at startup
        (per S8)."""
        entry, bodies = _test_entry()
        self._installed(tmp_path, entry, bodies)
        (pack_dir(tmp_path, entry) / "model.bin").unlink()
        fresh = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        status = fresh.status_of("model-test")
        assert status["state"] == "failed"
        error = status["error"]
        assert isinstance(error, dict)
        assert "model.bin" in str(error["message"])
        assert "model-test" in fresh.manager.validate_installed()

    def test_truncated_file_flags_failed(self, tmp_path: Path) -> None:
        entry, bodies = _test_entry()
        self._installed(tmp_path, entry, bodies)
        (pack_dir(tmp_path, entry) / "model.bin").write_bytes(b"tr")
        fresh = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        assert fresh.state_of("model-test") == "failed"

    def test_failed_pack_is_reinstallable(self, tmp_path: Path) -> None:
        """Per S8: integrity failures route to the re-download affordance."""
        entry, bodies = _test_entry()
        self._installed(tmp_path, entry, bodies)
        (pack_dir(tmp_path, entry) / "model.bin").unlink()
        fresh = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        fresh.manager.start_worker()
        try:
            fresh.manager.request_install("model-test")
            assert _wait_for(lambda: fresh.state_of("model-test") == "installed")
        finally:
            fresh.manager.shutdown()

    def test_garbage_parseable_manifest_survives_startup_validation(self, tmp_path: Path) -> None:
        """T2: serve_engine calls validate_installed unguarded before boot —
        a corrupt-but-parseable manifest must read as not installed, never
        raise out of validation or status derivation."""
        entry, bodies = _test_entry()
        target = pack_dir(tmp_path, entry)
        target.mkdir(parents=True)
        manifest_path(target).write_text('{"pack_schema": 1, "files": "garbage"}')
        fresh = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        assert fresh.manager.validate_installed() == {}
        assert fresh.state_of("model-test") == "not-installed"

    def test_clean_install_passes_validation(self, tmp_path: Path) -> None:
        entry, bodies = _test_entry()
        self._installed(tmp_path, entry, bodies)
        fresh = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        assert fresh.manager.validate_installed() == {}
        assert fresh.state_of("model-test") == "installed"


class TestLicenses:
    """License attributions ride PackStatus (task 8.1: Settings renders what
    the engine sends — engine-authoritative)."""

    _NOTICE = LicenseNotice(name="Test License", text="Test attribution text.")

    def test_uninstalled_pack_carries_registry_licenses(self, tmp_path: Path) -> None:
        entry, bodies = _test_entry(licenses=[self._NOTICE])
        h = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        status = h.status_of("model-test")
        assert status["state"] == "not-installed"
        assert status["licenses"] == [self._NOTICE]

    def test_installed_pack_carries_manifest_licenses(self, tmp_path: Path) -> None:
        """Once installed, the manifest (what is actually on disk) is the
        attribution source — a later registry edit must not rewrite history."""
        entry, bodies = _test_entry(licenses=[self._NOTICE])
        h = _Harness(tmp_path, {entry["id"]: entry}, bodies)
        h.manager.start_worker()
        try:
            h.manager.request_install("model-test")
            assert _wait_for(lambda: h.state_of("model-test") == "installed")
        finally:
            h.manager.shutdown()
        updated_notice = LicenseNotice(name="Updated License", text="New registry text.")
        updated = dict(entry)
        updated["licenses"] = [updated_notice]
        fresh = _Harness(tmp_path, {entry["id"]: dict(updated)}, bodies)  # type: ignore[arg-type]
        status = fresh.status_of("model-test")
        assert status["state"] == "installed"
        assert status["licenses"] == [self._NOTICE]

    def test_unavailable_pack_carries_registry_licenses(self, tmp_path: Path) -> None:
        entry, bodies = _test_entry(platforms=["win32"], licenses=[self._NOTICE])
        h = _Harness(tmp_path, {entry["id"]: entry}, bodies, platform="linux")
        status = h.status_of("model-test")
        assert status["state"] == "unavailable"
        assert status["licenses"] == [self._NOTICE]


def _wheel_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return buffer.getvalue()


class TestWheelExtraction:
    def test_complete_dll_set_extracted_and_archives_deleted(self, tmp_path: Path) -> None:
        """Spec scenario: Complete DLL set installed (runtime-packs) — every
        nvidia/*/bin/*.dll lands flat in the pack dir; archives are deleted."""
        cublas = _wheel_bytes(
            {
                "nvidia/cublas/bin/cublas64_12.dll": b"cublas-dll",
                "nvidia/cublas/bin/cublasLt64_12.dll": b"cublaslt-dll",
                "nvidia/cublas/include/cublas.h": b"not-a-dll",
                "nvidia_cublas_cu12-1.0.dist-info/METADATA": b"meta",
            }
        )
        cudnn = _wheel_bytes(
            {
                "nvidia/cudnn/bin/cudnn64_9.dll": b"cudnn-dll",
                "nvidia/cudnn/bin/cudnn_ops64_9.dll": b"cudnn-ops-dll",
                "nvidia/cudnn/lib/x64/cudnn.lib": b"not-a-dll-either",
            }
        )
        contents = {"cublas.whl": cublas, "cudnn.whl": cudnn}
        entry, bodies = _test_entry(
            "cuda-test", contents=contents, extract_wheels=True, platforms=["win32"]
        )
        entry["install_dir"] = "runtime"
        h = _Harness(tmp_path, {entry["id"]: entry}, bodies, platform="win32")
        h.manager.start_worker()
        try:
            h.manager.request_install("cuda-test")
            assert _wait_for(lambda: h.state_of("cuda-test") == "installed")
        finally:
            h.manager.shutdown()
        runtime = tmp_path / "runtime"
        dll_names = {
            "cublas64_12.dll",
            "cublasLt64_12.dll",
            "cudnn64_9.dll",
            "cudnn_ops64_9.dll",
        }
        assert {p.name for p in runtime.iterdir() if p.suffix == ".dll"} == dll_names
        assert (runtime / "cublas64_12.dll").read_bytes() == b"cublas-dll"
        manifest = read_manifest(runtime)
        assert manifest is not None
        assert {f["path"] for f in manifest["files"]} == dll_names
        # wheel archives deleted after extraction
        staging = h.manager.staging_dir("cuda-test")
        assert not staging.exists() or list(staging.glob("*.part")) == []
