"""Tests for podcast_reader.engine.pack_manager (downloader + installer).

All downloads run through ``httpx.MockTransport`` — never the network.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import httpx
import pytest

from podcast_reader.engine.pack_manager import (
    InstallAbortedError,
    PackDownloadError,
    discard_stale_partials,
    download_file,
    partial_path,
)
from podcast_reader.engine.packs import PackFilePin

if TYPE_CHECKING:
    from pathlib import Path


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
