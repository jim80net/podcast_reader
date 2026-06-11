"""Tests for podcast_reader.engine.app (FastAPI TestClient)."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import TYPE_CHECKING

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from podcast_reader.engine.app import create_app
from podcast_reader.engine.jobs import JobStore
from podcast_reader.engine.library import add_entry, entry_dir, source_identity
from podcast_reader.engine.settings import load_engine_state, load_settings
from podcast_reader.types import LibraryEntry, PipelineEvent, PipelineResult

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from podcast_reader.types import JobRecord

_RESULT = PipelineResult(
    json_path="/lib/aaa/a.json",
    chapters_path=None,
    html_path="/lib/aaa/a.html",
    title="Title",
)

# (method, path) for every route the app exposes
_ROUTES = [
    ("GET", "/v1/health"),
    ("POST", "/v1/jobs"),
    ("GET", "/v1/jobs"),
    ("GET", "/v1/jobs/some-id"),
    ("GET", "/v1/events"),
    ("GET", "/v1/library"),
    ("GET", "/v1/transcripts/abc123.html"),
    ("GET", "/v1/settings"),
    ("PUT", "/v1/settings"),
    ("PUT", "/v1/keys"),
]


def _wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _Engine:
    """Test harness bundling data dir, store, app, client, and auth headers."""

    def __init__(self, data_dir: Path, runner_release: threading.Event) -> None:
        self.data_dir = data_dir
        self.runner_release = runner_release

        def runner(record: JobRecord, on_event: Callable[[PipelineEvent], None]) -> PipelineResult:
            assert runner_release.wait(timeout=10)
            on_event(
                PipelineEvent(kind="step_started", step="resolve", message="Resolving...", data={})
            )
            on_event(PipelineEvent(kind="job_done", step=None, message="Done", data={}))
            return _RESULT

        self.store = JobStore(data_dir, runner)
        self.key_store: dict[str, str] = {}
        self.app = create_app(data_dir, self.store, key_store=self.key_store, heartbeat_s=0.05)
        self.token = load_engine_state(data_dir)["token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.client = TestClient(self.app)
        self.base_url = ""  # filled in by the live_engine fixture


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[_Engine]:
    release = threading.Event()
    release.set()  # jobs run immediately unless a test clears it
    harness = _Engine(tmp_path, release)
    harness.store.start_worker()
    yield harness
    release.set()
    harness.store.shutdown()


@pytest.fixture
def live_engine(tmp_path: Path) -> Iterator[_Engine]:
    """Engine served by real uvicorn on an ephemeral port.

    The starlette TestClient buffers whole responses, so the infinite SSE
    stream can only be tested against a live server with real httpx streaming.
    """
    release = threading.Event()
    release.set()
    harness = _Engine(tmp_path, release)
    harness.store.start_worker()

    config = uvicorn.Config(harness.app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert _wait_for(lambda: server.started)
    port = server.servers[0].sockets[0].getsockname()[1]
    harness.base_url = f"http://127.0.0.1:{port}"

    yield harness

    server.should_exit = True
    thread.join(timeout=10)
    release.set()
    harness.store.shutdown()


class TestAuth:
    @pytest.mark.parametrize(("method", "path"), _ROUTES)
    def test_missing_token_401_everywhere(self, engine: _Engine, method: str, path: str) -> None:
        response = engine.client.request(method, path)
        assert response.status_code == 401

    @pytest.mark.parametrize(("method", "path"), _ROUTES)
    def test_wrong_token_401_everywhere(self, engine: _Engine, method: str, path: str) -> None:
        response = engine.client.request(
            method, path, headers={"Authorization": "Bearer wrong-token"}
        )
        assert response.status_code == 401

    def test_query_token_rejected(self, engine: _Engine) -> None:
        response = engine.client.post(
            f"/v1/jobs?token={engine.token}",
            json={"source": "https://example.com/a"},
        )
        assert response.status_code == 401
        # and no work was performed
        assert engine.store.list_jobs() == []

    def test_valid_token_accepted(self, engine: _Engine) -> None:
        response = engine.client.get("/v1/health", headers=engine.headers)
        assert response.status_code == 200

    @pytest.mark.parametrize("scheme", ["bearer", "BEARER", "BeArEr"])
    def test_scheme_is_case_insensitive(self, engine: _Engine, scheme: str) -> None:
        """RFC 7235: the auth scheme token is case-insensitive (D6)."""
        response = engine.client.get(
            "/v1/health", headers={"Authorization": f"{scheme} {engine.token}"}
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(("method", "path"), _ROUTES)
    def test_401_carries_www_authenticate_challenge(
        self, engine: _Engine, method: str, path: str
    ) -> None:
        """RFC 7235: a 401 must include a WWW-Authenticate challenge (D6)."""
        response = engine.client.request(method, path)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_wrong_scheme_rejected(self, engine: _Engine) -> None:
        response = engine.client.get(
            "/v1/health", headers={"Authorization": f"Basic {engine.token}"}
        )
        assert response.status_code == 401


class TestHealth:
    def test_health_returns_version_and_fingerprint(self, engine: _Engine) -> None:
        response = engine.client.get("/v1/health", headers=engine.headers)
        assert response.status_code == 200
        body = response.json()
        expected_fp = hashlib.sha256(engine.token.encode()).hexdigest()[:16]
        assert body["token_fingerprint"] == expected_fp
        assert body["version"]


class TestJobs:
    def test_post_job_then_get_state(self, engine: _Engine) -> None:
        response = engine.client.post(
            "/v1/jobs",
            json={"source": "https://example.com/a", "title": "T"},
            headers=engine.headers,
        )
        assert response.status_code == 201
        job_id = response.json()["id"]
        assert response.json()["state"] == "queued"

        assert _wait_for(
            lambda: (
                engine.client.get(f"/v1/jobs/{job_id}", headers=engine.headers).json()["state"]
                == "done"
            )
        )
        record = engine.client.get(f"/v1/jobs/{job_id}", headers=engine.headers).json()
        assert record["result"] == dict(_RESULT)

    def test_get_jobs_lists_all(self, engine: _Engine) -> None:
        engine.client.post(
            "/v1/jobs", json={"source": "https://example.com/a"}, headers=engine.headers
        )
        engine.client.post(
            "/v1/jobs", json={"source": "https://example.com/b"}, headers=engine.headers
        )
        response = engine.client.get("/v1/jobs", headers=engine.headers)
        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_get_unknown_job_404(self, engine: _Engine) -> None:
        response = engine.client.get("/v1/jobs/unknown", headers=engine.headers)
        assert response.status_code == 404

    def test_job_record_contains_events_after_stream_missed(self, engine: _Engine) -> None:
        """The job record is the source of truth for clients that missed SSE."""
        response = engine.client.post(
            "/v1/jobs", json={"source": "https://example.com/a"}, headers=engine.headers
        )
        job_id = response.json()["id"]
        assert _wait_for(
            lambda: (
                engine.client.get(f"/v1/jobs/{job_id}", headers=engine.headers).json()["state"]
                == "done"
            )
        )
        record = engine.client.get(f"/v1/jobs/{job_id}", headers=engine.headers).json()
        kinds = [e["kind"] for e in record["events"]]
        assert "step_started" in kinds
        assert kinds[-1] == "job_done"


class TestEvents:
    def test_events_heartbeats_when_idle(self, live_engine: _Engine) -> None:
        with httpx.stream(
            "GET",
            f"{live_engine.base_url}/v1/events",
            headers=live_engine.headers,
            timeout=10,
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            for line in response.iter_lines():
                if line:
                    assert line == ": keepalive"
                    break

    def test_events_streams_job_events(self, live_engine: _Engine) -> None:
        live_engine.runner_release.clear()  # hold the job until the stream is open
        submitted = httpx.post(
            f"{live_engine.base_url}/v1/jobs",
            json={"source": "https://example.com/a"},
            headers=live_engine.headers,
            timeout=10,
        )
        job_id = submitted.json()["id"]

        data_line: str | None = None
        with httpx.stream(
            "GET",
            f"{live_engine.base_url}/v1/events",
            headers=live_engine.headers,
            timeout=10,
        ) as stream:
            live_engine.runner_release.set()
            for line in stream.iter_lines():
                if line.startswith("data: "):
                    data_line = line
                    break
        assert data_line is not None
        event = json.loads(data_line.removeprefix("data: "))
        assert event["data"]["job_id"] == job_id

    def test_disconnect_cleans_up_subscriber(self, live_engine: _Engine) -> None:
        with httpx.stream(
            "GET",
            f"{live_engine.base_url}/v1/events",
            headers=live_engine.headers,
            timeout=10,
        ) as response:
            for line in response.iter_lines():
                if line:
                    break
            assert live_engine.store.subscriber_count == 1
        assert _wait_for(lambda: live_engine.store.subscriber_count == 0)


class TestLibrary:
    def test_library_and_transcript_html(self, engine: _Engine, tmp_path: Path) -> None:
        settings = load_settings(tmp_path)
        library_dir = tmp_path / "library"
        assert settings["library_dir"] == str(library_dir)

        source = "https://example.com/episode"
        source_id = source_identity(source)
        edir = entry_dir(library_dir, source_id)
        edir.mkdir(parents=True)
        html_path = edir / "episode.html"
        html_path.write_text("<html>seeded</html>")
        add_entry(
            library_dir,
            LibraryEntry(
                source_id=source_id,
                source=source,
                title="Seeded",
                html_path=str(html_path),
                created_at=time.time(),
            ),
        )

        listing = engine.client.get("/v1/library", headers=engine.headers)
        assert listing.status_code == 200
        assert [e["source_id"] for e in listing.json()] == [source_id]

        transcript = engine.client.get(f"/v1/transcripts/{source_id}.html", headers=engine.headers)
        assert transcript.status_code == 200
        assert transcript.text == "<html>seeded</html>"

    def test_unknown_transcript_404(self, engine: _Engine) -> None:
        response = engine.client.get("/v1/transcripts/deadbeef.html", headers=engine.headers)
        assert response.status_code == 404


class TestSettings:
    def test_settings_get_put_roundtrip(self, engine: _Engine, tmp_path: Path) -> None:
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        assert current["whisper_model"] == "large-v3"

        current["whisper_device"] = "cpu"
        current["sentences"] = 3
        put = engine.client.put("/v1/settings", json=current, headers=engine.headers)
        assert put.status_code == 200

        fetched = engine.client.get("/v1/settings", headers=engine.headers).json()
        assert fetched["whisper_device"] == "cpu"
        assert fetched["sentences"] == 3
        # persisted on disk too
        assert load_settings(tmp_path)["whisper_device"] == "cpu"

    def test_put_settings_expands_tilde_in_library_dir(
        self, engine: _Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["library_dir"] = "~/lib"
        put = engine.client.put("/v1/settings", json=current, headers=engine.headers)
        assert put.status_code == 200
        assert put.json()["library_dir"] == str(tmp_path / "lib")
        assert load_settings(tmp_path)["library_dir"] == str(tmp_path / "lib")

    def test_provider_fields_roundtrip(self, engine: _Engine, tmp_path: Path) -> None:
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        assert current["chapter_provider"] == "anthropic"
        assert current["chapter_model"] == ""  # provider default

        current["chapter_provider"] = "custom"
        current["custom_provider_url"] = "https://llm.example.com/v1"
        put = engine.client.put("/v1/settings", json=current, headers=engine.headers)
        assert put.status_code == 200

        fetched = engine.client.get("/v1/settings", headers=engine.headers).json()
        assert fetched["chapter_provider"] == "custom"
        assert fetched["custom_provider_url"] == "https://llm.example.com/v1"
        assert load_settings(tmp_path)["chapter_provider"] == "custom"

    def test_put_settings_unknown_provider_400(self, engine: _Engine, tmp_path: Path) -> None:
        """M1: an unknown chapter_provider is rejected at PUT time (mirrors
        PUT /v1/keys) instead of surfacing later as an opaque job warning."""
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["chapter_provider"] = "nonsense"
        put = engine.client.put("/v1/settings", json=current, headers=engine.headers)
        assert put.status_code == 400
        assert "nonsense" in put.json()["detail"]
        # nothing was persisted
        assert load_settings(tmp_path)["chapter_provider"] == "anthropic"

    def test_put_settings_invalid_custom_url_400(self, engine: _Engine, tmp_path: Path) -> None:
        """M1: a plain-http remote custom URL is rejected at PUT time with the
        validator's own (self-authored) message."""
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["chapter_provider"] = "custom"
        current["custom_provider_url"] = "http://evil.example.com"
        put = engine.client.put("/v1/settings", json=current, headers=engine.headers)
        assert put.status_code == 400
        assert "must be https" in put.json()["detail"]
        assert load_settings(tmp_path)["custom_provider_url"] == ""

    def test_old_shape_put_succeeds_and_keeps_new_field_values(self, engine: _Engine) -> None:
        """Spec scenario: Old-shape PUT succeeds — pre-change clients omit the
        new fields; the request succeeds and the new fields keep current values."""
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["chapter_provider"] = "deepseek"
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )

        old_shape = {
            k: v for k, v in current.items() if k not in ("chapter_provider", "custom_provider_url")
        }
        old_shape["sentences"] = 7
        put = engine.client.put("/v1/settings", json=old_shape, headers=engine.headers)
        assert put.status_code == 200

        fetched = engine.client.get("/v1/settings", headers=engine.headers).json()
        assert fetched["sentences"] == 7
        assert fetched["chapter_provider"] == "deepseek"  # kept, not reset


class TestKeys:
    """Spec: In-memory key store — PUT /v1/keys, write-only, memory-only."""

    def test_put_key_stores_in_shared_memory_dict(self, engine: _Engine) -> None:
        response = engine.client.put(
            "/v1/keys",
            json={"provider": "anthropic", "api_key": "sk-ant-pushed"},
            headers=engine.headers,
        )
        assert response.status_code == 204
        assert engine.key_store == {"anthropic": "sk-ant-pushed"}

    def test_put_key_overwrites_previous(self, engine: _Engine) -> None:
        for key in ("sk-first", "sk-second"):
            engine.client.put(
                "/v1/keys",
                json={"provider": "openai", "api_key": key},
                headers=engine.headers,
            )
        assert engine.key_store == {"openai": "sk-second"}

    def test_unknown_provider_rejected(self, engine: _Engine) -> None:
        response = engine.client.put(
            "/v1/keys",
            json={"provider": "nonsense", "api_key": "sk-x"},
            headers=engine.headers,
        )
        assert response.status_code == 400
        assert engine.key_store == {}

    def test_empty_api_key_clears_pushed_key_restoring_env_fallback(
        self, engine: _Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """M4: PUT with api_key "" clears the pushed key; key resolution then
        falls back to the provider's env variable (truthiness is intentional)."""
        from podcast_reader.engine.process import _resolve_chapter_key

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-fallback")
        engine.client.put(
            "/v1/keys",
            json={"provider": "anthropic", "api_key": "sk-pushed"},
            headers=engine.headers,
        )
        assert _resolve_chapter_key("anthropic", engine.key_store) == "sk-pushed"

        put = engine.client.put(
            "/v1/keys",
            json={"provider": "anthropic", "api_key": ""},
            headers=engine.headers,
        )
        assert put.status_code == 204
        assert engine.key_store == {"anthropic": ""}
        assert _resolve_chapter_key("anthropic", engine.key_store) == "sk-env-fallback"

    def test_keys_cannot_be_read_back(self, engine: _Engine) -> None:
        engine.client.put(
            "/v1/keys",
            json={"provider": "anthropic", "api_key": "sk-ant-pushed"},
            headers=engine.headers,
        )
        assert engine.client.get("/v1/keys", headers=engine.headers).status_code == 405

    def test_keys_are_write_only_sweep(self, engine: _Engine, tmp_path: Path) -> None:
        """Spec scenario: after a key is PUT, no endpoint response and no
        persisted file contains the key value."""
        key = "sk-test-write-only-0123456789abcdef"
        put = engine.client.put(
            "/v1/keys",
            json={"provider": "anthropic", "api_key": key},
            headers=engine.headers,
        )
        assert key not in put.text

        # run a job so journal/library files exist and events flow
        submitted = engine.client.post(
            "/v1/jobs", json={"source": "https://example.com/a"}, headers=engine.headers
        )
        job_id = submitted.json()["id"]
        assert _wait_for(
            lambda: (
                engine.client.get(f"/v1/jobs/{job_id}", headers=engine.headers).json()["state"]
                == "done"
            )
        )

        for method, path in _ROUTES:
            if method != "GET" or path == "/v1/events":
                continue  # SSE stream blocks forever under TestClient
            response = engine.client.request(method, path, headers=engine.headers)
            assert key not in response.text, f"key leaked via {method} {path}"

        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert key not in path.read_text(errors="replace"), f"key leaked into {path}"
