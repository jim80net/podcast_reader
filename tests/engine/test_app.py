"""Tests for podcast_reader.engine.app (FastAPI TestClient)."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import stat
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from podcast_reader.engine.app import create_app
from podcast_reader.engine.jobs import JobStore
from podcast_reader.engine.library import add_entry, entry_dir, get_entry, source_identity
from podcast_reader.engine.pack_manager import PackManager
from podcast_reader.engine.packs import (
    REGISTRY,
    HardwareInfo,
    LicenseNotice,
    PackEntry,
    PackFilePin,
)
from podcast_reader.engine.pairing import CODE_ALPHABET, CODE_LENGTH, CODE_TTL_S, PairingState
from podcast_reader.engine.settings import load_engine_state, load_settings, save_settings
from podcast_reader.engine.web_session import (
    SESSION_LIFETIME_S,
    SESSION_PREFIX,
    WebSessionSigner,
)
from podcast_reader.html import build_html
from podcast_reader.providers import PROVIDERS
from podcast_reader.types import LibraryEntry, PipelineEvent, PipelineResult

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from podcast_reader.types import JobOverrides, JobRecord

_RESULT = PipelineResult(
    json_path="/lib/aaa/a.json",
    chapters_path=None,
    html_path="/lib/aaa/a.html",
    title="Title",
)


def _completion(content: str) -> dict[str, object]:
    """Minimal OpenAI-compatible /chat/completions response body."""
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


# (method, path) for every route the app exposes
_ROUTES = [
    ("GET", "/v1/health"),
    ("POST", "/v1/jobs"),
    ("GET", "/v1/jobs"),
    ("GET", "/v1/jobs/some-id"),
    ("POST", "/v1/jobs/some-id/confirm"),
    ("DELETE", "/v1/jobs/some-id"),
    ("GET", "/v1/events"),
    ("GET", "/v1/library"),
    ("GET", "/v1/transcripts/abc123.html"),
    ("GET", "/v1/settings"),
    ("PUT", "/v1/settings"),
    ("PUT", "/v1/keys"),
    ("POST", "/v1/keys/test"),
    ("GET", "/v1/providers"),
    ("GET", "/v1/packs"),
    ("POST", "/v1/packs/some-id/install"),
    ("DELETE", "/v1/packs/some-id"),
    ("POST", "/v1/shutdown"),
    ("POST", "/v1/pair"),
    # The middleware exemption is (method, path): only POST /v1/pair/claim is
    # unauthenticated — any other method on the claim path still 401s (per U5).
    ("GET", "/v1/pair/claim"),
    ("PUT", "/v1/cookies"),
    ("GET", "/v1/cookies"),
    ("DELETE", "/v1/cookies/example.com"),
]

_PACK_CONTENT = {"model.bin": b"engine-test-weights" * 16, "config.json": b'{"ok": true}'}


def _pack_entry() -> tuple[PackEntry, dict[str, bytes]]:
    """A synthetic installable pack plus its URL -> bytes body map."""
    pins = [
        PackFilePin(
            path=name,
            url=f"https://packs.example.com/{name}",
            sha256=hashlib.sha256(body).hexdigest(),
            size=len(body),
        )
        for name, body in _PACK_CONTENT.items()
    ]
    entry = PackEntry(
        id="model-apitest",
        kind="model",
        display_name="API test pack",
        platforms=None,
        install_dir="models/apitest",
        extract_wheels=False,
        files=pins,
        version="rev-api-1",
        component_versions={"model_revision": "rev-api-1"},
        compat={},
        licenses=[LicenseNotice(name="API Test License", text="API test attribution.")],
    )
    return entry, {pin["url"]: _PACK_CONTENT[pin["path"]] for pin in pins}


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
        self.shutdown_requests: list[bool] = []
        # POST /v1/keys/test outbound traffic: tests set key_test_handler;
        # recorded requests land in key_test_requests.
        self.key_test_handler: Callable[[httpx.Request], httpx.Response] | None = None
        self.key_test_requests: list[httpx.Request] = []

        def key_test_transport_handler(request: httpx.Request) -> httpx.Response:
            assert self.key_test_handler is not None, "test must set engine.key_test_handler"
            self.key_test_requests.append(request)
            return self.key_test_handler(request)

        # Pack surface: real registry plus a synthetic installable pack served
        # by a Range-aware mock host (never the network); hardware injected.
        self.pack_entry, pack_bodies = _pack_entry()
        self.pack_requests: list[httpx.Request] = []
        self.pack_gate: threading.Event | None = None
        self.hardware = HardwareInfo(platform="win32", nvidia_gpu=True, gpu_names=["Test GPU 4090"])

        def pack_handler(request: httpx.Request) -> httpx.Response:
            if self.pack_gate is not None:
                assert self.pack_gate.wait(timeout=10)
            self.pack_requests.append(request)
            body = pack_bodies[str(request.url)]
            range_header = request.headers.get("range")
            if range_header:
                start = int(range_header.removeprefix("bytes=").rstrip("-"))
                return httpx.Response(206, content=body[start:])
            return httpx.Response(200, content=body)

        self.pack_manager = PackManager(
            data_dir,
            bus=self.store.bus,
            registry={**REGISTRY, self.pack_entry["id"]: self.pack_entry},
            transport=httpx.MockTransport(pack_handler),
            platform="win32",
            progress_step=1,
            hardware_provider=lambda: self.hardware,
        )

        # Pairing with a settable clock so expiry is testable via TestClient.
        self.pairing_now = time.time()
        self.pairing = PairingState(clock=lambda: self.pairing_now)
        self.web_session_now = self.pairing_now
        self.web_session_signer = WebSessionSigner(
            load_engine_state(data_dir)["token"].encode(),
            generation=1,
            clock=lambda: self.web_session_now,
        )

        self.app = create_app(
            data_dir,
            self.store,
            key_store=self.key_store,
            heartbeat_s=0.05,
            on_shutdown=lambda: self.shutdown_requests.append(True),
            key_test_transport=httpx.MockTransport(key_test_transport_handler),
            pack_manager=self.pack_manager,
            pairing=self.pairing,
            web_session_signer=self.web_session_signer,
        )
        self.token = load_engine_state(data_dir)["token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.client = TestClient(self.app)
        self.web_client = TestClient(self.app, base_url="https://web.test")
        self.base_url = ""  # filled in by the live_engine fixture


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[_Engine]:
    release = threading.Event()
    release.set()  # jobs run immediately unless a test clears it
    harness = _Engine(tmp_path, release)
    harness.store.start_worker()
    harness.pack_manager.start_worker()
    yield harness
    release.set()
    if harness.pack_gate is not None:
        harness.pack_gate.set()
    harness.pack_manager.shutdown()
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
    harness.pack_manager.start_worker()

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
    harness.pack_manager.shutdown()
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


class TestJobOverrides:
    def test_submit_with_overrides_stores_only_set_fields(self, engine: _Engine) -> None:
        # A rerun submission carries model overrides; the job record keeps just
        # the fields that were set (exclude_none) so the runner clears exactly
        # the right cached artifacts.
        response = engine.client.post(
            "/v1/jobs",
            json={
                "source": "https://example.com/over",
                "overrides": {"chapter_provider": "xai", "chapter_model": "grok-4"},
            },
            headers=engine.headers,
        )
        assert response.status_code == 201
        assert response.json()["overrides"] == {
            "chapter_provider": "xai",
            "chapter_model": "grok-4",
        }

    def test_explicit_null_override_fields_are_dropped(self, engine: _Engine) -> None:
        # exclude_none: a field sent as null must not be stored (else the runner
        # would treat it as an override and clear/clobber wrongly).
        response = engine.client.post(
            "/v1/jobs",
            json={
                "source": "https://example.com/nulls",
                "overrides": {"chapter_provider": "xai", "whisper_model": None},
            },
            headers=engine.headers,
        )
        assert response.status_code == 201
        assert response.json()["overrides"] == {"chapter_provider": "xai"}

    def test_unknown_override_provider_is_400(self, engine: _Engine) -> None:
        response = engine.client.post(
            "/v1/jobs",
            json={"source": "https://example.com/x", "overrides": {"chapter_provider": "nope"}},
            headers=engine.headers,
        )
        assert response.status_code == 400
        assert engine.store.list_jobs() == []  # nothing enqueued

    def test_invalid_override_custom_url_is_400(self, engine: _Engine) -> None:
        response = engine.client.post(
            "/v1/jobs",
            json={
                "source": "https://example.com/x",
                "overrides": {"chapter_provider": "custom", "custom_provider_url": "ftp://nope"},
            },
            headers=engine.headers,
        )
        assert response.status_code == 400

    def test_submit_without_overrides_is_none(self, engine: _Engine) -> None:
        response = engine.client.post(
            "/v1/jobs", json={"source": "https://example.com/plain"}, headers=engine.headers
        )
        assert response.status_code == 201
        assert response.json()["overrides"] is None


class TestYoutubeEmbed:
    def test_embed_is_tokenless_and_returns_html(self, engine: _Engine) -> None:
        # The Reader iframe holds no token; the embed page MUST load without one
        # (and from the loopback http origin — that is the Error 152/153 fix).
        response = engine.client.get("/v1/embed/dQw4w9WgXcQ")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "dQw4w9WgXcQ" in response.text
        assert "youtube-nocookie.com" in response.text

    def test_invalid_id_with_token_is_404(self, engine: _Engine) -> None:
        # A valid token reaches the route; the route rejects a bad id (here,
        # over the 32-char cap) with a 404.
        response = engine.client.get("/v1/embed/" + "A" * 33, headers=engine.headers)
        assert response.status_code == 404

    def test_invalid_id_is_not_tokenless_exempt(self, engine: _Engine) -> None:
        # The exemption matches only valid ids, so a bad id WITHOUT a token hits
        # the bearer check (401), never the tokenless page — keeping the
        # unauthenticated surface to exactly the real route.
        assert engine.client.get("/v1/embed/" + "A" * 33).status_code == 401
        assert engine.client.get("/v1/embed/bad!id").status_code == 401

    def test_only_get_is_exempt_from_auth(self, engine: _Engine) -> None:
        # The tokenless exemption is GET-only: a POST to the embed path still
        # requires the bearer token (no auth bypass surface).
        response = engine.client.post("/v1/embed/dQw4w9WgXcQ")
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


class TestAwaitingConfirmationRoutes:
    """Spec: Awaiting-confirmation state — reachable through the API."""

    def _submit_pending(self, engine: _Engine) -> str:
        response = engine.client.post(
            "/v1/jobs",
            json={"source": "https://example.com/p", "requires_confirmation": True},
            headers=engine.headers,
        )
        assert response.status_code == 201
        assert response.json()["state"] == "awaiting-confirmation"
        return str(response.json()["id"])

    def test_default_submission_stays_queued(self, engine: _Engine) -> None:
        """Spec scenario: Default submission stays queued — existing clients
        (no requires_confirmation field) are unchanged by this change."""
        response = engine.client.post(
            "/v1/jobs", json={"source": "https://example.com/a"}, headers=engine.headers
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

    def test_confirmation_required_job_does_not_execute(self, engine: _Engine) -> None:
        """Spec scenario: Confirmation-required job does not execute."""
        pending_id = self._submit_pending(engine)
        # a later default job completing proves the worker drained past it
        baseline = engine.client.post(
            "/v1/jobs", json={"source": "https://example.com/b"}, headers=engine.headers
        ).json()["id"]
        assert _wait_for(
            lambda: (
                engine.client.get(f"/v1/jobs/{baseline}", headers=engine.headers).json()["state"]
                == "done"
            )
        )
        record = engine.client.get(f"/v1/jobs/{pending_id}", headers=engine.headers).json()
        assert record["state"] == "awaiting-confirmation"
        assert record["events"] == []  # no pipeline step ran

    def test_confirm_enqueues_and_executes(self, engine: _Engine) -> None:
        """Spec scenario: Confirm enqueues."""
        pending_id = self._submit_pending(engine)
        response = engine.client.post(f"/v1/jobs/{pending_id}/confirm", headers=engine.headers)
        assert response.status_code == 200
        assert response.json()["state"] == "queued"
        assert _wait_for(
            lambda: (
                engine.client.get(f"/v1/jobs/{pending_id}", headers=engine.headers).json()["state"]
                == "done"
            )
        )

    def test_confirm_unknown_job_404(self, engine: _Engine) -> None:
        response = engine.client.post("/v1/jobs/unknown/confirm", headers=engine.headers)
        assert response.status_code == 404

    def test_confirm_wrong_state_409(self, engine: _Engine) -> None:
        """Spec scenario: Confirm rejected in other states."""
        done_id = engine.client.post(
            "/v1/jobs", json={"source": "https://example.com/a"}, headers=engine.headers
        ).json()["id"]
        assert _wait_for(
            lambda: (
                engine.client.get(f"/v1/jobs/{done_id}", headers=engine.headers).json()["state"]
                == "done"
            )
        )
        response = engine.client.post(f"/v1/jobs/{done_id}/confirm", headers=engine.headers)
        assert response.status_code == 409
        assert "done" in response.json()["detail"]
        # the job is unchanged
        record = engine.client.get(f"/v1/jobs/{done_id}", headers=engine.headers).json()
        assert record["state"] == "done"

    def test_delete_discards_awaiting_confirmation_job(self, engine: _Engine) -> None:
        """Spec scenario: Dismiss discards only pending confirmations."""
        pending_id = self._submit_pending(engine)
        response = engine.client.delete(f"/v1/jobs/{pending_id}", headers=engine.headers)
        assert response.status_code == 204
        assert (
            engine.client.get(f"/v1/jobs/{pending_id}", headers=engine.headers).status_code == 404
        )

    def test_delete_unknown_job_404(self, engine: _Engine) -> None:
        response = engine.client.delete("/v1/jobs/unknown", headers=engine.headers)
        assert response.status_code == 404

    def test_delete_wrong_state_409(self, engine: _Engine) -> None:
        done_id = engine.client.post(
            "/v1/jobs", json={"source": "https://example.com/a"}, headers=engine.headers
        ).json()["id"]
        assert _wait_for(
            lambda: (
                engine.client.get(f"/v1/jobs/{done_id}", headers=engine.headers).json()["state"]
                == "done"
            )
        )
        response = engine.client.delete(f"/v1/jobs/{done_id}", headers=engine.headers)
        assert response.status_code == 409
        assert engine.client.get(f"/v1/jobs/{done_id}", headers=engine.headers).status_code == 200

    def test_pending_confirmation_survives_restart_unenqueued(self, tmp_path: Path) -> None:
        """Spec scenario: Pending confirmation survives restart — exercised
        through the API of a second engine instance over the same data dir."""
        release = threading.Event()
        release.set()
        first = _Engine(tmp_path, release)
        first.store.start_worker()
        pending_id = TestAwaitingConfirmationRoutes._submit_pending(self, first)
        first.store.shutdown()

        restarted = _Engine(tmp_path, release)
        restarted.store.start_worker()
        try:
            record = restarted.client.get(
                f"/v1/jobs/{pending_id}", headers=restarted.headers
            ).json()
            assert record["state"] == "awaiting-confirmation"
            # still un-enqueued: a baseline job completes while it sits there
            baseline = restarted.client.post(
                "/v1/jobs", json={"source": "https://example.com/b"}, headers=restarted.headers
            ).json()["id"]
            assert _wait_for(
                lambda: (
                    restarted.client.get(f"/v1/jobs/{baseline}", headers=restarted.headers).json()[
                        "state"
                    ]
                    == "done"
                )
            )
            after = restarted.client.get(f"/v1/jobs/{pending_id}", headers=restarted.headers).json()
            assert after["state"] == "awaiting-confirmation"
            assert after["events"] == []
        finally:
            restarted.store.shutdown()


class TestShutdownRoute:
    """Spec: Graceful shutdown endpoint (the full-process exit lives in
    test_process.py — here: 202, hook invocation, auth, and the no-hook case)."""

    def test_shutdown_returns_202_and_invokes_hook(self, engine: _Engine) -> None:
        response = engine.client.post("/v1/shutdown", headers=engine.headers)
        assert response.status_code == 202
        assert engine.shutdown_requests == [True]

    def test_unauthenticated_shutdown_rejected_and_hook_untouched(self, engine: _Engine) -> None:
        """Spec scenario: Unauthenticated shutdown rejected."""
        response = engine.client.post("/v1/shutdown")
        assert response.status_code == 401
        assert engine.shutdown_requests == []
        # the app keeps serving
        assert engine.client.get("/v1/health", headers=engine.headers).status_code == 200

    def test_shutdown_without_hook_is_503(self, engine: _Engine, tmp_path: Path) -> None:
        """An app built without a shutdown hook refuses loudly, not silently."""
        bare_app = create_app(tmp_path, engine.store, key_store={})
        client = TestClient(bare_app)
        response = client.post("/v1/shutdown", headers=engine.headers)
        assert response.status_code == 503


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

    def test_legacy_transcript_remote_font_import_is_removed(
        self, engine: _Engine, tmp_path: Path
    ) -> None:
        source_id = source_identity("https://example.com/legacy")
        edir = entry_dir(tmp_path / "library", source_id)
        edir.mkdir(parents=True)
        html_path = edir / "episode.html"
        remote_import = (
            "@import url('https://fonts.googleapis.com/css2?"
            "family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;"
            "0,8..60,700;1,8..60,400&family=JetBrains+Mono:wght@400;600&"
            "family=Oswald:wght@400;500;600&display=swap');"
        )
        clean_document = b"<html><style>\nbody { color: red; }</style></html>"
        legacy_document = clean_document.replace(
            b"<style>\n", f"<style>\n{remote_import}\n\n".encode(), 1
        )
        html_path.write_bytes(legacy_document)
        add_entry(
            tmp_path / "library",
            LibraryEntry(
                source_id=source_id,
                source="https://example.com/legacy",
                title="Legacy",
                html_path=str(html_path),
                created_at=time.time(),
            ),
        )

        response = engine.client.get(f"/v1/transcripts/{source_id}.html", headers=engine.headers)

        assert response.status_code == 200
        assert response.content == clean_document
        assert html_path.read_bytes() == legacy_document


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

    def test_named_provider_fields_roundtrip_and_become_selectable(
        self, engine: _Engine, tmp_path: Path
    ) -> None:
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["custom_providers"] = [
            {
                "name": "office-gateway",
                "base_url": "https://llm.corp.example/v1",
                "default_model": "corp-small",
                "max_tokens": 32768,
            }
        ]
        current["chapter_provider"] = "office-gateway"

        put = engine.client.put("/v1/settings", json=current, headers=engine.headers)

        assert put.status_code == 200
        assert load_settings(tmp_path)["custom_providers"] == current["custom_providers"]
        listing = engine.client.get("/v1/providers", headers=engine.headers).json()
        assert listing[-1] == {
            "id": "office-gateway",
            "default_model": "corp-small",
            "key_available": False,
        }

    @pytest.mark.parametrize("secret_field", ["api_key", "key"])
    def test_named_provider_secret_shaped_fields_rejected(
        self, engine: _Engine, tmp_path: Path, secret_field: str
    ) -> None:
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["custom_providers"] = [
            {
                "name": "office-gateway",
                "base_url": "https://llm.corp.example/v1",
                "default_model": "corp-small",
                "max_tokens": 32768,
                secret_field: "sk-must-not-persist",
            }
        ]

        put = engine.client.put("/v1/settings", json=current, headers=engine.headers)

        assert put.status_code == 422
        assert "sk-must-not-persist" not in put.text
        if (tmp_path / "settings.json").exists():
            assert "sk-must-not-persist" not in (tmp_path / "settings.json").read_text()

    def test_named_provider_url_credentials_rejected(self, engine: _Engine) -> None:
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["custom_providers"] = [
            {
                "name": "office-gateway",
                "base_url": "https://user:secret@llm.corp.example/v1",
                "default_model": "corp-small",
                "max_tokens": 32768,
            }
        ]

        put = engine.client.put("/v1/settings", json=current, headers=engine.headers)

        assert put.status_code == 400
        assert "secret" not in put.text

    @pytest.mark.parametrize("requires_confirmation", [True, False])
    def test_cannot_delete_provider_referenced_by_nonterminal_override(
        self, engine: _Engine, requires_confirmation: bool
    ) -> None:
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["custom_providers"] = [
            {
                "name": "office-gateway",
                "base_url": "https://llm.corp.example/v1",
                "default_model": "corp-small",
                "max_tokens": 32768,
            }
        ]
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )
        if not requires_confirmation:
            engine.runner_release.clear()
        submitted = engine.client.post(
            "/v1/jobs",
            json={
                "source": "https://example.com/queued",
                "requires_confirmation": requires_confirmation,
                "overrides": {"chapter_provider": "office-gateway"},
            },
            headers=engine.headers,
        )
        assert submitted.status_code == 201
        if not requires_confirmation:
            assert _wait_for(
                lambda: (
                    engine.client.get(
                        f"/v1/jobs/{submitted.json()['id']}", headers=engine.headers
                    ).json()["state"]
                    == "running"
                )
            )

        current["custom_providers"] = []
        put = engine.client.put("/v1/settings", json=current, headers=engine.headers)

        assert put.status_code == 409
        assert submitted.json()["id"] in put.json()["detail"]

    def test_provider_deletion_and_override_submission_are_serialized(
        self, engine: _Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor

        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        provider = {
            "name": "office-gateway",
            "base_url": "https://llm.corp.example/v1",
            "default_model": "corp-small",
            "max_tokens": 32768,
        }
        current["custom_providers"] = [provider]
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )
        submit_entered = threading.Event()
        release_submit = threading.Event()
        real_submit = engine.store.submit

        def held_submit(
            source: str,
            title: str | None,
            *,
            requires_confirmation: bool = False,
            overrides: JobOverrides | None = None,
        ) -> JobRecord:
            submit_entered.set()
            assert release_submit.wait(timeout=10)
            return real_submit(
                source,
                title,
                requires_confirmation=requires_confirmation,
                overrides=overrides,
            )

        monkeypatch.setattr(engine.store, "submit", held_submit)
        removal = {**current, "custom_providers": []}
        with ThreadPoolExecutor(max_workers=2) as pool:
            submitted = pool.submit(
                engine.client.post,
                "/v1/jobs",
                json={
                    "source": "https://example.com/race",
                    "requires_confirmation": True,
                    "overrides": {"chapter_provider": "office-gateway"},
                },
                headers=engine.headers,
            )
            assert submit_entered.wait(timeout=10)
            deleted = pool.submit(
                engine.client.put, "/v1/settings", json=removal, headers=engine.headers
            )
            release_submit.set()
            submit_response = submitted.result(timeout=10)
            delete_response = deleted.result(timeout=10)

        assert submit_response.status_code == 201
        assert delete_response.status_code == 409
        assert load_settings(engine.data_dir)["custom_providers"] == [provider]

    def test_diarize_roundtrip_and_default(self, engine: _Engine, tmp_path: Path) -> None:
        """diarization-worker spec: `diarize` defaults false, settable via
        PUT /v1/settings."""
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        assert current["diarize"] is False

        current["diarize"] = True
        put = engine.client.put("/v1/settings", json=current, headers=engine.headers)
        assert put.status_code == 200

        assert engine.client.get("/v1/settings", headers=engine.headers).json()["diarize"] is True
        assert load_settings(tmp_path)["diarize"] is True

    def test_put_without_diarize_keeps_current_value(self, engine: _Engine, tmp_path: Path) -> None:
        """A PUT from a pre-change client (no `diarize` field) must not reset
        the persisted value (established optional-field discipline)."""
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["diarize"] = True
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )

        del current["diarize"]
        put = engine.client.put("/v1/settings", json=current, headers=engine.headers)
        assert put.status_code == 200
        assert load_settings(tmp_path)["diarize"] is True

    def test_caption_cleanup_is_opt_in_and_old_puts_preserve_it(
        self, engine: _Engine, tmp_path: Path
    ) -> None:
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        assert current["caption_cleanup"] is False

        current["caption_cleanup"] = True
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )
        del current["caption_cleanup"]
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )
        assert load_settings(tmp_path)["caption_cleanup"] is True

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

    def test_put_settings_custom_provider_empty_url_400(
        self, engine: _Engine, tmp_path: Path
    ) -> None:
        """Selecting the custom provider without a base URL is rejected at PUT
        time: an empty URL would only fail later, at job dequeue."""
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["chapter_provider"] = "custom"
        current["custom_provider_url"] = ""
        put = engine.client.put("/v1/settings", json=current, headers=engine.headers)
        assert put.status_code == 400
        assert "base URL" in put.json()["detail"]
        # nothing was persisted
        assert load_settings(tmp_path)["chapter_provider"] == "anthropic"

    def test_put_settings_switch_to_custom_with_persisted_url_200(
        self, engine: _Engine, tmp_path: Path
    ) -> None:
        """A PUT omitting custom_provider_url may still switch to the custom
        provider when a valid URL is already persisted (effective value wins)."""
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["custom_provider_url"] = "https://llm.example.com/v1"
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )

        switch = {k: v for k, v in current.items() if k != "custom_provider_url"}
        switch["chapter_provider"] = "custom"
        put = engine.client.put("/v1/settings", json=switch, headers=engine.headers)
        assert put.status_code == 200
        assert load_settings(tmp_path)["chapter_provider"] == "custom"
        assert load_settings(tmp_path)["custom_provider_url"] == "https://llm.example.com/v1"

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

    def test_named_provider_key_is_accepted_and_write_only(self, engine: _Engine) -> None:
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["custom_providers"] = [
            {
                "name": "office-gateway",
                "base_url": "https://llm.corp.example/v1",
                "default_model": "corp-small",
                "max_tokens": 32768,
            }
        ]
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )

        response = engine.client.put(
            "/v1/keys",
            json={"provider": "office-gateway", "api_key": "sk-office-secret"},
            headers=engine.headers,
        )

        assert response.status_code == 204
        assert engine.key_store["office-gateway"] == "sk-office-secret"
        assert (
            "sk-office-secret"
            not in engine.client.get("/v1/providers", headers=engine.headers).text
        )

    def test_unknown_provider_can_be_cleared_but_not_set(self, engine: _Engine) -> None:
        cleared = engine.client.put(
            "/v1/keys", json={"provider": "removed", "api_key": ""}, headers=engine.headers
        )
        set_key = engine.client.put(
            "/v1/keys", json={"provider": "removed", "api_key": "secret"}, headers=engine.headers
        )

        assert cleared.status_code == 204
        assert set_key.status_code == 400

    def test_remove_then_readd_does_not_reactivate_old_key(self, engine: _Engine) -> None:
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        provider = {
            "name": "office-gateway",
            "base_url": "https://llm.corp.example/v1",
            "default_model": "corp-small",
            "max_tokens": 32768,
        }
        current["custom_providers"] = [provider]
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )
        assert (
            engine.client.put(
                "/v1/keys",
                json={"provider": "office-gateway", "api_key": "sk-old-secret"},
                headers=engine.headers,
            ).status_code
            == 204
        )
        current["custom_providers"] = []
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )
        assert "office-gateway" not in engine.key_store

        current["custom_providers"] = [{**provider, "base_url": "https://new.example/v1"}]
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )
        listing = engine.client.get("/v1/providers", headers=engine.headers).json()
        assert (
            next(item for item in listing if item["id"] == "office-gateway")["key_available"]
            is False
        )
        engine.key_test_handler = lambda request: httpx.Response(200, json=_completion("ok"))
        tested = engine.client.post(
            "/v1/keys/test", json={"provider": "office-gateway"}, headers=engine.headers
        )
        assert tested.json()["ok"] is False
        assert engine.key_test_requests == []

    def test_key_put_and_provider_removal_are_serialized(self, tmp_path: Path) -> None:
        from concurrent.futures import ThreadPoolExecutor

        class BlockingKeys(dict[str, str]):
            armed = False

            def __setitem__(self, name: str, value: str) -> None:
                if self.armed and name == "office-gateway":
                    write_entered.set()
                    assert release_write.wait(timeout=10)
                super().__setitem__(name, value)

        write_entered = threading.Event()
        release_write = threading.Event()
        keys = BlockingKeys()
        store = JobStore(tmp_path, lambda record, on_event: _RESULT)
        client = TestClient(create_app(tmp_path, store, key_store=keys))
        headers = {"Authorization": f"Bearer {load_engine_state(tmp_path)['token']}"}
        current = client.get("/v1/settings", headers=headers).json()
        provider = {
            "name": "office-gateway",
            "base_url": "https://llm.corp.example/v1",
            "default_model": "corp-small",
            "max_tokens": 32768,
        }
        current["custom_providers"] = [provider]
        assert client.put("/v1/settings", json=current, headers=headers).status_code == 200
        keys.armed = True
        removal = {**current, "custom_providers": []}

        with ThreadPoolExecutor(max_workers=2) as pool:
            key_put = pool.submit(
                client.put,
                "/v1/keys",
                json={"provider": "office-gateway", "api_key": "sk-racing"},
                headers=headers,
            )
            assert write_entered.wait(timeout=10)
            deleted = pool.submit(client.put, "/v1/settings", json=removal, headers=headers)
            assert not deleted.done()
            release_write.set()
            assert key_put.result(timeout=10).status_code == 204
            assert deleted.result(timeout=10).status_code == 200

        assert keys == {}
        current["custom_providers"] = [{**provider, "base_url": "https://new.example/v1"}]
        assert client.put("/v1/settings", json=current, headers=headers).status_code == 200
        listing = client.get("/v1/providers", headers=headers).json()
        assert (
            next(item for item in listing if item["id"] == "office-gateway")["key_available"]
            is False
        )

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
        assert engine.key_store == {}
        assert _resolve_chapter_key("anthropic", engine.key_store) == "sk-env-fallback"

    def test_keys_cannot_be_read_back(self, engine: _Engine) -> None:
        engine.client.put(
            "/v1/keys",
            json={"provider": "anthropic", "api_key": "sk-ant-pushed"},
            headers=engine.headers,
        )
        assert engine.client.get("/v1/keys", headers=engine.headers).status_code == 405

    def test_key_resolution_order_supplied_then_pushed_then_env(
        self, engine: _Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Key resolution order: supplied > pushed > env."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
        engine.key_test_handler = lambda request: httpx.Response(200, json=_completion("ok"))

        engine.client.put(
            "/v1/keys",
            json={"provider": "anthropic", "api_key": "sk-pushed"},
            headers=engine.headers,
        )
        engine.client.post(
            "/v1/keys/test",
            json={"provider": "anthropic", "api_key": "sk-supplied"},
            headers=engine.headers,
        )
        engine.client.post("/v1/keys/test", json={"provider": "anthropic"}, headers=engine.headers)
        engine.client.put(
            "/v1/keys", json={"provider": "anthropic", "api_key": ""}, headers=engine.headers
        )
        engine.client.post("/v1/keys/test", json={"provider": "anthropic"}, headers=engine.headers)

        used = [r.headers["authorization"] for r in engine.key_test_requests]
        assert used == ["Bearer sk-supplied", "Bearer sk-pushed", "Bearer sk-env"]

    def test_invalid_key_fails_with_sanitized_detail(
        self, engine: _Engine, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Spec scenario: Invalid key fails with sanitized detail — the
        provider's 401 body echoes the key; neither the key nor the body may
        appear in the response or the logs (K4)."""
        api_key = "sk-test-invalid-key-0123456789"
        provider_body = f"Incorrect API key provided: {api_key}"
        engine.key_test_handler = lambda request: httpx.Response(
            401, json={"error": {"message": provider_body}}
        )
        with caplog.at_level(logging.DEBUG):
            response = engine.client.post(
                "/v1/keys/test",
                json={"provider": "anthropic", "api_key": api_key},
                headers=engine.headers,
            )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "401" in body["detail"]
        assert api_key not in response.text
        assert "Incorrect API key" not in response.text
        assert api_key not in caplog.text
        assert "Incorrect API key" not in caplog.text

    def test_transport_failure_fails_with_self_authored_detail(self, engine: _Engine) -> None:
        """The httpx.HTTPError branch: a transport failure (connection refused,
        DNS, TLS) yields ok=False with a self-authored, exception-type-only
        detail — the transport error's own message never reaches the response
        (K4: it can embed URLs or proxy details)."""

        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        engine.key_test_handler = refuse
        response = engine.client.post(
            "/v1/keys/test",
            json={"provider": "anthropic", "api_key": "sk-x"},
            headers=engine.headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["detail"] == "connection to provider failed (ConnectError)"
        assert "connection refused" not in response.text

    def test_valid_key_tests_successfully(self, engine: _Engine) -> None:
        """Spec scenario: Valid key tests successfully (a real round-trip
        through the injected transport)."""
        engine.key_test_handler = lambda request: httpx.Response(200, json=_completion("ok"))
        response = engine.client.post(
            "/v1/keys/test",
            json={"provider": "anthropic", "api_key": "sk-valid"},
            headers=engine.headers,
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert len(engine.key_test_requests) == 1
        url = str(engine.key_test_requests[0].url)
        assert url == "https://api.anthropic.com/v1/chat/completions"

    def test_unknown_provider_400_without_outbound_call(self, engine: _Engine) -> None:
        """Spec scenario: Unknown provider rejected."""
        engine.key_test_handler = lambda request: httpx.Response(200, json=_completion("ok"))
        response = engine.client.post(
            "/v1/keys/test",
            json={"provider": "nonsense", "api_key": "sk-x"},
            headers=engine.headers,
        )
        assert response.status_code == 400
        assert engine.key_test_requests == []

    def test_no_key_available_fails_without_outbound_call(
        self, engine: _Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        engine.key_test_handler = lambda request: httpx.Response(200, json=_completion("ok"))
        response = engine.client.post(
            "/v1/keys/test", json={"provider": "anthropic"}, headers=engine.headers
        )
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert "anthropic" in response.json()["detail"]
        assert engine.key_test_requests == []

    def test_tested_key_is_not_stored(
        self, engine: _Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: Testing does not store — after a test, the key store
        is untouched, so subsequent jobs cannot use the tested key."""
        from podcast_reader.engine.process import _resolve_chapter_key

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        engine.key_test_handler = lambda request: httpx.Response(200, json=_completion("ok"))
        engine.client.post(
            "/v1/keys/test",
            json={"provider": "anthropic", "api_key": "sk-tested-only"},
            headers=engine.headers,
        )
        assert engine.key_store == {}
        assert _resolve_chapter_key("anthropic", engine.key_store) is None

    def test_custom_provider_resolves_url_from_settings(self, engine: _Engine) -> None:
        """Per P9: provider=custom uses custom_provider_url from the current
        settings for the round-trip."""
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["chapter_provider"] = "custom"
        current["custom_provider_url"] = "https://llm.example.com/v1"
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )
        engine.key_test_handler = lambda request: httpx.Response(200, json=_completion("ok"))
        response = engine.client.post(
            "/v1/keys/test",
            json={"provider": "custom", "api_key": "sk-custom"},
            headers=engine.headers,
        )
        assert response.json()["ok"] is True
        assert str(engine.key_test_requests[0].url) == "https://llm.example.com/v1/chat/completions"

    def test_named_provider_key_test_uses_its_endpoint_default_and_cap(
        self, engine: _Engine
    ) -> None:
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["custom_providers"] = [
            {
                "name": "office-gateway",
                "base_url": "https://llm.corp.example/v1",
                "default_model": "corp-small",
                "max_tokens": 32768,
            }
        ]
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )
        engine.key_test_handler = lambda request: httpx.Response(200, json=_completion("ok"))

        response = engine.client.post(
            "/v1/keys/test",
            json={"provider": "office-gateway", "api_key": "sk-office"},
            headers=engine.headers,
        )

        assert response.json()["ok"] is True
        request = engine.key_test_requests[0]
        assert str(request.url) == "https://llm.corp.example/v1/chat/completions"
        assert json.loads(request.content)["model"] == "corp-small"

    def test_custom_provider_without_url_400_no_outbound_call(self, engine: _Engine) -> None:
        """Per P9: empty custom_provider_url is a 400, not an outbound request."""
        engine.key_test_handler = lambda request: httpx.Response(200, json=_completion("ok"))
        response = engine.client.post(
            "/v1/keys/test",
            json={"provider": "custom", "api_key": "sk-custom"},
            headers=engine.headers,
        )
        assert response.status_code == 400
        assert "base URL" in response.json()["detail"]
        assert engine.key_test_requests == []

    def test_custom_provider_invalid_url_400_no_outbound_call(
        self, engine: _Engine, tmp_path: Path
    ) -> None:
        """Per P9: an invalid persisted URL (e.g. written by an older version)
        fails the test request with the validator's self-authored message."""
        settings = load_settings(tmp_path)
        settings["custom_provider_url"] = "http://evil.example.com"
        save_settings(tmp_path, settings)
        engine.key_test_handler = lambda request: httpx.Response(200, json=_completion("ok"))
        response = engine.client.post(
            "/v1/keys/test",
            json={"provider": "custom", "api_key": "sk-custom"},
            headers=engine.headers,
        )
        assert response.status_code == 400
        assert "must be https" in response.json()["detail"]
        assert engine.key_test_requests == []

    def test_model_follows_settings_only_for_the_active_provider(self, engine: _Engine) -> None:
        """The test mirrors what a job would send: the configured
        chapter_model applies to the configured provider; any other provider
        is tested against its registry default model."""
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["chapter_model"] = "my-anthropic-model"  # provider stays anthropic
        engine.client.put("/v1/settings", json=current, headers=engine.headers)
        engine.key_test_handler = lambda request: httpx.Response(200, json=_completion("ok"))

        engine.client.post(
            "/v1/keys/test",
            json={"provider": "anthropic", "api_key": "sk-a"},
            headers=engine.headers,
        )
        engine.client.post(
            "/v1/keys/test",
            json={"provider": "openai", "api_key": "sk-o"},
            headers=engine.headers,
        )
        models = [json.loads(r.content)["model"] for r in engine.key_test_requests]
        assert models == ["my-anthropic-model", PROVIDERS["openai"]["default_model"]]

    def test_keys_are_write_only_sweep(
        self, engine: _Engine, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Spec scenario: after a key is PUT, no endpoint response and no
        persisted file contains the key value."""
        current = engine.client.get("/v1/settings", headers=engine.headers).json()
        current["custom_providers"] = [
            {
                "name": name,
                "base_url": f"https://{name}.example/v1",
                "default_model": "sweep-model",
                "max_tokens": 4096,
            }
            for name in ("sweep-one", "sweep-two")
        ]
        current["chapter_provider"] = "sweep-one"
        assert (
            engine.client.put("/v1/settings", json=current, headers=engine.headers).status_code
            == 200
        )
        secrets = [
            "sk-test-write-only-0123456789abcdef",
            "sk-named-one-0123456789abcdef",
            "sk-named-two-fedcba9876543210",
        ]
        for provider, key in zip(("anthropic", "sweep-one", "sweep-two"), secrets, strict=True):
            put = engine.client.put(
                "/v1/keys",
                json={"provider": provider, "api_key": key},
                headers=engine.headers,
            )
            assert key not in put.text

        echoed = f"provider rejected {secrets[2]}"
        engine.key_test_handler = lambda request: httpx.Response(
            401, json={"error": {"message": echoed}}
        )
        with caplog.at_level(logging.DEBUG):
            failed_test = engine.client.post(
                "/v1/keys/test", json={"provider": "sweep-two"}, headers=engine.headers
            )
        assert failed_test.json()["ok"] is False
        for key in secrets:
            assert key not in failed_test.text
            assert key[:12] not in failed_test.text
            assert key not in caplog.text
            assert key[:12] not in caplog.text

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
        completed = engine.client.get(f"/v1/jobs/{job_id}", headers=engine.headers)
        assert completed.json()["state"] == "done"

        for method, path in _ROUTES:
            if method != "GET" or path == "/v1/events":
                continue  # SSE stream blocks forever under TestClient
            response = engine.client.request(method, path, headers=engine.headers)
            for key in secrets:
                assert key not in response.text, f"key leaked via {method} {path}"
                assert key[:12] not in response.text, f"key prefix leaked via {method} {path}"

        for path in tmp_path.rglob("*"):
            if path.is_file():
                persisted = path.read_text(errors="replace")
                for key in secrets:
                    assert key not in persisted, f"key leaked into {path}"
                    assert key[:12] not in persisted, f"key prefix leaked into {path}"


class TestProviders:
    """Spec: Provider listing endpoint (per P4) — ids, default models, and a
    key-availability boolean; never key material."""

    _ALL_IDS = ["anthropic", "openai", "xai", "openrouter", "deepseek", "custom"]

    def _clear_provider_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for spec in PROVIDERS.values():
            monkeypatch.delenv(spec["key_env"], raising=False)

    def test_lists_exactly_the_six_registry_ids(
        self, engine: _Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: Registry listed."""
        self._clear_provider_env(monkeypatch)
        response = engine.client.get("/v1/providers", headers=engine.headers)
        assert response.status_code == 200
        listing = response.json()
        assert [p["id"] for p in listing] == self._ALL_IDS
        by_id = {p["id"]: p for p in listing}
        for name, spec in PROVIDERS.items():
            assert by_id[name]["default_model"] == spec["default_model"]
            assert by_id[name]["key_available"] is False

    def test_key_available_reflects_pushed_or_env(
        self, engine: _Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clear_provider_env(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai")
        engine.client.put(
            "/v1/keys",
            json={"provider": "anthropic", "api_key": "sk-pushed-ant"},
            headers=engine.headers,
        )
        # a cleared pushed key ("") falls back to env — here: none, so False
        engine.client.put(
            "/v1/keys", json={"provider": "xai", "api_key": ""}, headers=engine.headers
        )

        by_id = {
            p["id"]: p["key_available"]
            for p in engine.client.get("/v1/providers", headers=engine.headers).json()
        }
        assert by_id == {
            "anthropic": True,  # pushed
            "openai": True,  # env
            "xai": False,  # cleared push, no env
            "openrouter": False,
            "deepseek": False,
            "custom": False,
        }

    def test_no_key_material_in_listing(
        self, engine: _Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: No key material in the listing — with keys pushed
        and env vars set, the response carries booleans only."""
        secrets = {}
        for name, spec in PROVIDERS.items():
            pushed = f"sk-pushed-{name}-0123456789abcdef"
            env_value = f"sk-env-{name}-fedcba9876543210"
            secrets[name] = (pushed, env_value)
            monkeypatch.setenv(spec["key_env"], env_value)
            put = engine.client.put(
                "/v1/keys", json={"provider": name, "api_key": pushed}, headers=engine.headers
            )
            assert put.status_code == 204

        response = engine.client.get("/v1/providers", headers=engine.headers)
        assert response.status_code == 200
        for pushed, env_value in secrets.values():
            assert pushed not in response.text
            assert env_value not in response.text
            # no prefixes/fragments either (anything key-derived is banned)
            assert pushed[:12] not in response.text
            assert env_value[:12] not in response.text
        assert all(p["key_available"] is True for p in response.json())


class TestPacks:
    """Spec: Pack status / installation / uninstall endpoints."""

    PACK_ID = "model-apitest"

    def _packs_by_id(self, engine: _Engine) -> dict[str, dict[str, object]]:
        response = engine.client.get("/v1/packs", headers=engine.headers)
        assert response.status_code == 200
        return {p["id"]: p for p in response.json()["packs"]}

    def _state(self, engine: _Engine, pack_id: str) -> str:
        return str(self._packs_by_id(engine)[pack_id]["state"])

    def test_fresh_install_shows_recommendations(self, engine: _Engine) -> None:
        """Spec scenario: Fresh install shows recommendations — Windows +
        NVIDIA reports the CUDA pack and large-v3 recommended, all packs
        not-installed."""
        response = engine.client.get("/v1/packs", headers=engine.headers)
        assert response.status_code == 200
        body = response.json()
        assert body["hardware"] == {
            "platform": "win32",
            "nvidia_gpu": True,
            "gpu_names": ["Test GPU 4090"],
        }
        packs = {p["id"]: p for p in body["packs"]}
        assert packs["cuda-runtime"]["recommended"] is True
        assert packs["model-large-v3"]["recommended"] is True
        assert packs["model-small"]["recommended"] is False
        assert packs["diarization"]["recommended"] is False
        assert packs["cuda-runtime"]["state"] == "not-installed"
        assert packs["model-large-v3"]["state"] == "not-installed"
        # unpublished entry is unavailable, not not-installed (per S5)
        assert packs["diarization"]["state"] == "unavailable"

    def test_install_endpoint_202_then_installed(self, engine: _Engine) -> None:
        response = engine.client.post(f"/v1/packs/{self.PACK_ID}/install", headers=engine.headers)
        assert response.status_code == 202
        assert _wait_for(lambda: self._state(engine, self.PACK_ID) == "installed")
        status = self._packs_by_id(engine)[self.PACK_ID]
        assert status["installed_version"] == "rev-api-1"
        assert status["error"] is None

    def test_packs_payload_carries_license_attributions(self, engine: _Engine) -> None:
        """Task 8.1: Settings renders the engine-sent license notices."""
        status = self._packs_by_id(engine)[self.PACK_ID]
        assert status["licenses"] == [{"name": "API Test License", "text": "API test attribution."}]

    def test_install_unknown_pack_404(self, engine: _Engine) -> None:
        response = engine.client.post("/v1/packs/nonsense/install", headers=engine.headers)
        assert response.status_code == 404

    def test_install_unpublished_pack_409_and_no_download(self, engine: _Engine) -> None:
        """Spec scenario: Unpublished pack is not installable (per S5)."""
        response = engine.client.post("/v1/packs/diarization/install", headers=engine.headers)
        assert response.status_code == 409
        assert "diarization" in response.json()["detail"]
        assert engine.pack_requests == []
        assert self._state(engine, "diarization") == "unavailable"

    def test_duplicate_install_idempotent_202(self, engine: _Engine) -> None:
        """Spec scenario: Duplicate install request is idempotent — 202 and
        no second download."""
        engine.pack_gate = threading.Event()
        first = engine.client.post(f"/v1/packs/{self.PACK_ID}/install", headers=engine.headers)
        assert first.status_code == 202
        assert _wait_for(lambda: self._state(engine, self.PACK_ID) == "installing")
        second = engine.client.post(f"/v1/packs/{self.PACK_ID}/install", headers=engine.headers)
        assert second.status_code == 202
        engine.pack_gate.set()
        assert _wait_for(lambda: self._state(engine, self.PACK_ID) == "installed")
        urls = [str(r.url) for r in engine.pack_requests]
        assert len(urls) == len(set(urls)), f"a file downloaded twice: {urls}"

    def test_installing_status_carries_progress(self, engine: _Engine) -> None:
        engine.pack_gate = threading.Event()
        engine.client.post(f"/v1/packs/{self.PACK_ID}/install", headers=engine.headers)
        assert _wait_for(lambda: self._state(engine, self.PACK_ID) == "installing")
        status = self._packs_by_id(engine)[self.PACK_ID]
        progress = status["progress"]
        assert isinstance(progress, dict)
        assert progress["total"] == sum(len(b) for b in _PACK_CONTENT.values())
        engine.pack_gate.set()

    def test_install_does_not_block_transcription_jobs(self, engine: _Engine) -> None:
        """Spec scenario: Install does not block transcription jobs."""
        engine.pack_gate = threading.Event()  # download held open
        engine.client.post(f"/v1/packs/{self.PACK_ID}/install", headers=engine.headers)
        assert _wait_for(lambda: self._state(engine, self.PACK_ID) == "installing")
        job = engine.client.post(
            "/v1/jobs", json={"source": "https://example.com/a"}, headers=engine.headers
        ).json()
        assert _wait_for(
            lambda: (
                engine.client.get(f"/v1/jobs/{job['id']}", headers=engine.headers).json()["state"]
                == "done"
            )
        )
        assert self._state(engine, self.PACK_ID) == "installing"  # still downloading
        engine.pack_gate.set()

    def test_uninstall_204_removes_pack(self, engine: _Engine, tmp_path: Path) -> None:
        """Spec scenario: Uninstall removes the pack."""
        engine.client.post(f"/v1/packs/{self.PACK_ID}/install", headers=engine.headers)
        assert _wait_for(lambda: self._state(engine, self.PACK_ID) == "installed")
        response = engine.client.delete(f"/v1/packs/{self.PACK_ID}", headers=engine.headers)
        assert response.status_code == 204
        assert self._state(engine, self.PACK_ID) == "not-installed"
        target = tmp_path / "models" / "apitest"
        assert not (target / "pack-manifest.json").exists()
        for name in _PACK_CONTENT:
            assert not (target / name).exists()

    def test_uninstall_unknown_404(self, engine: _Engine) -> None:
        response = engine.client.delete("/v1/packs/nonsense", headers=engine.headers)
        assert response.status_code == 404

    def test_uninstall_while_installing_409(self, engine: _Engine) -> None:
        """Spec scenario: Uninstall refused while installing."""
        engine.pack_gate = threading.Event()
        engine.client.post(f"/v1/packs/{self.PACK_ID}/install", headers=engine.headers)
        assert _wait_for(lambda: self._state(engine, self.PACK_ID) == "installing")
        response = engine.client.delete(f"/v1/packs/{self.PACK_ID}", headers=engine.headers)
        assert response.status_code == 409
        engine.pack_gate.set()
        assert _wait_for(lambda: self._state(engine, self.PACK_ID) == "installed")

    def test_uninstall_allowed_while_job_running(self, engine: _Engine) -> None:
        """Per S1: a running job is no reason to refuse an uninstall — the
        manifest-first ordering makes the race structurally safe."""
        engine.client.post(f"/v1/packs/{self.PACK_ID}/install", headers=engine.headers)
        assert _wait_for(lambda: self._state(engine, self.PACK_ID) == "installed")
        engine.runner_release.clear()  # a job is now running and held open
        job = engine.client.post(
            "/v1/jobs", json={"source": "https://example.com/a"}, headers=engine.headers
        ).json()
        assert _wait_for(
            lambda: (
                engine.client.get(f"/v1/jobs/{job['id']}", headers=engine.headers).json()["state"]
                == "running"
            )
        )
        response = engine.client.delete(f"/v1/packs/{self.PACK_ID}", headers=engine.headers)
        assert response.status_code == 204
        engine.runner_release.set()

    def test_packs_routes_503_without_manager(self, engine: _Engine, tmp_path: Path) -> None:
        """An app built without a pack manager refuses loudly, not silently."""
        bare_app = create_app(tmp_path, engine.store, key_store={})
        client = TestClient(bare_app)
        assert client.get("/v1/packs", headers=engine.headers).status_code == 503
        assert client.post("/v1/packs/x/install", headers=engine.headers).status_code == 503
        assert client.delete("/v1/packs/x", headers=engine.headers).status_code == 503


class TestPackEventsOnStream:
    """Spec: Pack progress on the event stream (per S6/Q5)."""

    def test_pack_and_job_events_interleave_with_correct_discriminators(
        self, live_engine: _Engine
    ) -> None:
        """Spec scenarios: Progress observable live + Job event consumers
        unaffected — pack events carry pack_id and MUST NOT carry job_id;
        job events keep carrying job_id (per Q5)."""
        events: list[dict[str, object]] = []
        with httpx.stream(
            "GET",
            f"{live_engine.base_url}/v1/events",
            headers=live_engine.headers,
            timeout=10,
        ) as stream:
            install = httpx.post(
                f"{live_engine.base_url}/v1/packs/model-apitest/install",
                headers=live_engine.headers,
                timeout=10,
            )
            assert install.status_code == 202
            job = httpx.post(
                f"{live_engine.base_url}/v1/jobs",
                json={"source": "https://example.com/a"},
                headers=live_engine.headers,
                timeout=10,
            ).json()

            def seen(kind: str) -> bool:
                return any(e["kind"] == kind for e in events)

            def pack_installed() -> bool:
                return any(
                    e["kind"] == "pack_state" and e["data"]["state"] == "installed"  # type: ignore[index]
                    for e in events
                )

            for line in stream.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line.removeprefix("data: ")))
                if seen("job_done") and pack_installed():
                    break

        pack_events = [e for e in events if str(e["kind"]).startswith("pack_")]
        job_events = [e for e in events if not str(e["kind"]).startswith("pack_")]
        assert any(e["kind"] == "pack_progress" for e in pack_events)
        assert pack_events and job_events
        for event in pack_events:
            data = event["data"]
            assert isinstance(data, dict)
            assert data["pack_id"] == "model-apitest"
            assert "job_id" not in data, f"pack event leaked job_id: {event}"
        for event in job_events:
            data = event["data"]
            assert isinstance(data, dict)
            assert data["job_id"] == job["id"]


class TestPairing:
    """Spec: Pairing-code exchange — mint bearer-authed, claim unauthenticated."""

    def _mint(self, engine: _Engine) -> str:
        response = engine.client.post("/v1/pair", headers=engine.headers)
        assert response.status_code == 200
        return str(response.json()["code"])

    def _claim(self, engine: _Engine, code: str, **kwargs: object) -> httpx.Response:
        return engine.client.post("/v1/pair/claim", json={"code": code}, **kwargs)  # type: ignore[arg-type]

    def test_mint_requires_the_bearer_token(self, engine: _Engine) -> None:
        """Spec scenario: Mint requires the bearer token — 401 and no code
        created (a subsequent claim with any code is rejected)."""
        assert engine.client.post("/v1/pair").status_code == 401
        assert self._claim(engine, "ABCDEF").status_code == 403

    def test_mint_returns_code_and_expiry(self, engine: _Engine) -> None:
        response = engine.client.post("/v1/pair", headers=engine.headers)
        assert response.status_code == 200
        body = response.json()
        assert len(body["code"]) == CODE_LENGTH
        assert all(c in CODE_ALPHABET for c in body["code"])
        assert body["expires_at"] == engine.pairing_now + CODE_TTL_S

    def test_valid_claim_returns_the_token_once(self, engine: _Engine) -> None:
        """Spec scenario: Valid claim returns the token once — the second
        claim with the same code responds 403 (single-use)."""
        code = self._mint(engine)
        first = self._claim(engine, code)
        assert first.status_code == 200
        assert first.json() == {"token": engine.token}
        assert self._claim(engine, code).status_code == 403

    def test_claim_needs_no_authorization_header(self, engine: _Engine) -> None:
        """Spec scenario: Claim is reachable without credentials — the auth
        middleware exempts exactly POST /v1/pair/claim."""
        code = self._mint(engine)
        response = engine.client.post("/v1/pair/claim", json={"code": code})
        assert response.status_code == 200

    @pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH"])
    def test_non_post_methods_on_claim_path_still_401(self, engine: _Engine, method: str) -> None:
        """Spec scenario (per U5): the exemption matches (method, path) — any
        other method on /v1/pair/claim without a token responds 401."""
        assert engine.client.request(method, "/v1/pair/claim").status_code == 401

    def test_rejections_are_uniform_403(self, engine: _Engine) -> None:
        """Spec scenarios: wrong, expired, exhausted, and absent codes all
        produce the same 403 — no oracle distinguishes the cases."""
        wrong_while_pending = self._claim(engine, "WRONG2")  # code pending
        self._mint(engine)
        engine.pairing_now += CODE_TTL_S  # expire it
        expired = self._claim(engine, "WRONG2")
        no_pending = self._claim(engine, "WRONG2")  # nothing pending anymore
        missing_code = engine.client.post("/v1/pair/claim", json={})
        responses = [wrong_while_pending, expired, no_pending, missing_code]
        assert [r.status_code for r in responses] == [403, 403, 403, 403]
        assert len({r.text for r in responses}) == 1

    def test_expired_code_rejected(self, engine: _Engine) -> None:
        """Spec scenario: Expired code rejected uniformly."""
        code = self._mint(engine)
        engine.pairing_now += CODE_TTL_S
        assert self._claim(engine, code).status_code == 403

    def test_attempt_budget_invalidates_the_code(self, engine: _Engine) -> None:
        """Spec scenario: Attempt budget invalidates the code — five wrong
        claims, then even the correct code responds 403."""
        code = self._mint(engine)
        for _ in range(5):
            assert self._claim(engine, "WRONG2").status_code == 403
        assert self._claim(engine, code).status_code == 403

    def test_new_mint_replaces_the_old_code(self, engine: _Engine) -> None:
        """Spec scenario: New mint replaces the old code."""
        old = self._mint(engine)
        new = self._mint(engine)
        assert self._claim(engine, old).status_code == 403
        assert self._claim(engine, new).status_code == 200

    def test_page_origin_and_content_type_gates_do_not_burn_the_budget(
        self, engine: _Engine
    ) -> None:
        """Spec scenario (per U3): gate rejections (http/https Origin, wrong
        content type) leave the pending code's attempt budget unchanged — a
        subsequent valid claim still succeeds."""
        code = self._mint(engine)
        for origin in ("https://evil.example", "http://evil.example"):
            for _ in range(3):
                response = self._claim(engine, code, headers={"Origin": origin})
                assert response.status_code == 403
        for _ in range(6):
            response = engine.client.post(
                "/v1/pair/claim",
                content=f'{{"code": "{code}"}}',
                headers={"Content-Type": "text/plain"},
            )
            assert response.status_code == 403
        assert self._claim(engine, code).status_code == 200

    def test_oversized_body_rejected_without_burning_the_budget(self, engine: _Engine) -> None:
        """Per V4: a Content-Length above the 4096-byte cap is rejected with
        the uniform 403 before the body is read — and, like the other gates,
        without reaching the pairing state, so the correct code still claims
        after six oversized attempts."""
        code = self._mint(engine)
        oversized = json.dumps({"code": code, "pad": "x" * 8192})
        assert len(oversized.encode()) > 4096
        for _ in range(6):
            response = engine.client.post(
                "/v1/pair/claim",
                content=oversized,
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 403
        assert self._claim(engine, code).status_code == 200

    def test_missing_content_length_rejected_without_burning_the_budget(
        self, engine: _Engine
    ) -> None:
        """Per V4: a chunked request (no Content-Length) gives the body read
        no bound, so it is rejected with the uniform 403 — again without
        burning the attempt budget."""
        code = self._mint(engine)
        payload = json.dumps({"code": code}).encode()
        for _ in range(6):
            response = engine.client.post(
                "/v1/pair/claim",
                content=iter([payload]),  # httpx sends chunked, no Content-Length
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 403
        assert self._claim(engine, code).status_code == 200

    def test_chrome_extension_origin_passes(self, engine: _Engine) -> None:
        """Spec (per U3): a chrome-extension:// Origin is NOT rejected."""
        code = self._mint(engine)
        response = self._claim(
            engine, code, headers={"Origin": "chrome-extension://abcdefghijklmnop"}
        )
        assert response.status_code == 200
        assert response.json() == {"token": engine.token}

    def test_non_json_content_type_rejected(self, engine: _Engine) -> None:
        """Spec (per U3): claim requires Content-Type application/json."""
        code = self._mint(engine)
        response = engine.client.post(
            "/v1/pair/claim",
            content=f'{{"code": "{code}"}}',
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 403

    def test_codes_never_persisted_or_logged(
        self, engine: _Engine, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Spec scenario: Codes never persisted — no engine file (journal,
        settings, discovery, ...) and no log record contains a minted code."""
        with caplog.at_level(logging.DEBUG):
            code = self._mint(engine)
            self._claim(engine, "WRONG2")
            assert self._claim(engine, code).status_code == 200
        assert code not in caplog.text
        for path in engine.data_dir.rglob("*"):
            if path.is_file():
                assert code not in path.read_text(errors="replace"), path


class TestWebPairingSession:
    """The browser claim -> verify -> HttpOnly-session boundary."""

    _WEB_HEADERS = {
        "Origin": "https://web.test",
        "Sec-Fetch-Site": "same-origin",
    }

    def _mint(self, engine: _Engine) -> str:
        response = engine.client.post("/v1/pair", headers=engine.headers)
        assert response.status_code == 200
        return str(response.json()["code"])

    def _web_claim(
        self, engine: _Engine, code: str, *, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return cast(
            "httpx.Response",
            engine.web_client.post(
                "/web/api/pair/claim",
                json={"code": code},
                headers=headers if headers is not None else self._WEB_HEADERS,
            ),
        )

    def _create_session(self, engine: _Engine) -> httpx.Response:
        return cast(
            "httpx.Response",
            engine.web_client.post(
                "/web/api/session",
                json={},
                headers={**engine.headers, **self._WEB_HEADERS},
            ),
        )

    def test_web_claim_shares_pairing_state_and_returns_candidate_bearer(
        self, engine: _Engine
    ) -> None:
        code = self._mint(engine)
        response = self._web_claim(engine, code)
        assert response.status_code == 200
        assert response.json() == {"token": engine.token}
        assert response.headers["cache-control"] == "no-store"
        assert "access-control-allow-origin" not in response.headers
        assert engine.client.get("/v1/health", headers=engine.headers).status_code == 200
        assert engine.client.post("/v1/pair/claim", json={"code": code}).status_code == 403

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Origin": "http://web.test", "Sec-Fetch-Site": "same-origin"},
            {"Origin": "https://evil.test", "Sec-Fetch-Site": "same-origin"},
            {"Origin": "https://web.test", "Sec-Fetch-Site": "cross-site"},
        ],
    )
    def test_web_claim_origin_gates_fail_uniformly_without_burning_code(
        self, engine: _Engine, headers: dict[str, str]
    ) -> None:
        code = self._mint(engine)
        for _ in range(6):
            response = self._web_claim(engine, code, headers=headers)
            assert response.status_code == 403
            assert response.json() == {"detail": "web request rejected"}
        assert self._web_claim(engine, code).status_code == 200

    def test_default_https_port_is_normalized_but_nondefault_mismatch_rejects(
        self, engine: _Engine
    ) -> None:
        code = self._mint(engine)
        accepted = self._web_claim(
            engine,
            code,
            headers={
                "Host": "web.test:443",
                "Origin": "https://web.test",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert accepted.status_code == 200

        code = self._mint(engine)
        rejected = self._web_claim(
            engine,
            code,
            headers={
                "Host": "web.test:8443",
                "Origin": "https://web.test",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert rejected.status_code == 403
        assert self._web_claim(engine, code).status_code == 200

    def test_web_claim_requires_bounded_json_without_burning_code(self, engine: _Engine) -> None:
        code = self._mint(engine)
        response = engine.web_client.post(
            "/web/api/pair/claim",
            content=json.dumps({"code": code}),
            headers={**self._WEB_HEADERS, "Content-Type": "text/plain"},
        )
        assert response.status_code == 403
        oversized = json.dumps({"code": code, "padding": "x" * 4096})
        response = engine.web_client.post(
            "/web/api/pair/claim",
            content=oversized,
            headers={**self._WEB_HEADERS, "Content-Type": "application/json"},
        )
        assert response.status_code == 403
        assert self._web_claim(engine, code).status_code == 200

    def test_pathological_numeric_content_length_fails_closed_without_burning_code(
        self, engine: _Engine
    ) -> None:
        code = self._mint(engine)
        response = engine.web_client.post(
            "/web/api/pair/claim",
            content=json.dumps({"code": code}),
            headers={
                **self._WEB_HEADERS,
                "Content-Type": "application/json",
                "Content-Length": "9" * 5000,
            },
        )
        assert response.status_code == 403
        assert response.json() == {"detail": "web request rejected"}
        assert self._web_claim(engine, code).status_code == 200

    def test_session_requires_verified_bearer_and_sets_scoped_cookie(self, engine: _Engine) -> None:
        assert (
            engine.web_client.post(
                "/web/api/session", json={}, headers=self._WEB_HEADERS
            ).status_code
            == 401
        )

        response = self._create_session(engine)

        assert response.status_code == 204
        assert response.headers["cache-control"] == "no-store"
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"__Secure-podcast_reader_web={SESSION_PREFIX}.")
        assert "HttpOnly" in cookie
        assert "Max-Age=15552000" in cookie
        assert "Path=/web/" in cookie
        assert "SameSite=strict" in cookie
        assert "Secure" in cookie
        assert engine.token not in cookie

    def test_cookie_never_authorizes_v1(self, engine: _Engine) -> None:
        assert self._create_session(engine).status_code == 204
        assert engine.web_client.get("/v1/health").status_code == 401

    def test_logout_requires_valid_cookie_and_clears_it(self, engine: _Engine) -> None:
        assert (
            engine.web_client.post(
                "/web/api/logout", json={}, headers=self._WEB_HEADERS
            ).status_code
            == 401
        )
        assert self._create_session(engine).status_code == 204

        response = engine.web_client.post("/web/api/logout", json={}, headers=self._WEB_HEADERS)

        assert response.status_code == 204
        cookie = response.headers["set-cookie"]
        assert cookie.startswith("__Secure-podcast_reader_web=")
        assert "Max-Age=0" in cookie
        assert "Path=/web/" in cookie
        assert response.headers["cache-control"] == "no-store"

    def test_expired_cookie_rejected(self, engine: _Engine) -> None:
        assert self._create_session(engine).status_code == 204
        engine.web_session_now += SESSION_LIFETIME_S
        response = engine.web_client.post("/web/api/logout", json={}, headers=self._WEB_HEADERS)
        assert response.status_code == 401

    def test_session_and_logout_apply_same_origin_gate(self, engine: _Engine) -> None:
        assert self._create_session(engine).status_code == 204
        hostile = {"Origin": "https://evil.test", "Sec-Fetch-Site": "cross-site"}
        session = engine.web_client.post(
            "/web/api/session", json={}, headers={**engine.headers, **hostile}
        )
        logout = engine.web_client.post("/web/api/logout", json={}, headers=hostile)
        assert session.status_code == logout.status_code == 403
        assert session.text == logout.text

    def test_session_credential_never_persists_or_echoes(
        self, engine: _Engine, caplog: pytest.LogCaptureFixture
    ) -> None:
        created = self._create_session(engine)
        credential = created.cookies.get("__Secure-podcast_reader_web")
        assert credential is not None and credential.startswith(f"{SESSION_PREFIX}.")

        with caplog.at_level(logging.DEBUG):
            rejected = engine.web_client.post(
                "/web/api/session",
                json={"unexpected": credential},
                headers={**engine.headers, **self._WEB_HEADERS},
            )
        assert rejected.status_code == 422
        self._assert_security_headers(rejected)
        assert credential not in rejected.text
        assert credential not in caplog.text
        for path in engine.data_dir.rglob("*"):
            if path.is_file():
                assert credential not in path.read_text(errors="replace"), path

    def test_security_headers_cover_success_gate_and_framework_errors(
        self, engine: _Engine
    ) -> None:
        code = self._mint(engine)
        success = self._web_claim(engine, code)
        gated = engine.web_client.post("/web/api/pair/claim", json={"code": code})
        missing = engine.web_client.get("/web/api/not-a-route", headers=engine.headers)
        wrong_method = engine.web_client.get("/web/api/session", headers=engine.headers)

        assert success.status_code == 200
        assert gated.status_code == 403
        assert missing.status_code == 404
        assert wrong_method.status_code == 405
        for response in (success, gated, missing, wrong_method):
            self._assert_security_headers(response)

    def test_unhandled_web_failure_is_generic_hardened_and_unlogged(
        self, engine: _Engine, caplog: pytest.LogCaptureFixture
    ) -> None:
        secret = "prws1.must-never-escape-the-web-error"

        @engine.app.get("/web/api/_injected_failure")
        def injected_failure() -> None:
            raise RuntimeError(secret)

        client = TestClient(
            engine.app,
            base_url="https://web.test",
            raise_server_exceptions=False,
        )
        with caplog.at_level(logging.DEBUG):
            response = client.get("/web/api/_injected_failure", headers=engine.headers)

        assert response.status_code == 500
        assert response.json() == {"detail": "internal server error"}
        self._assert_security_headers(response)
        assert secret not in response.text
        assert secret not in caplog.text

    def test_non_web_unhandled_failure_retains_framework_behavior(self, engine: _Engine) -> None:
        @engine.app.get("/v1/_injected_failure")
        def injected_failure() -> None:
            raise RuntimeError("non-web failure")

        client = TestClient(engine.app, raise_server_exceptions=False)
        response = client.get("/v1/_injected_failure", headers=engine.headers)
        assert response.status_code == 500
        assert response.text == "Internal Server Error"
        assert "referrer-policy" not in response.headers
        assert "x-content-type-options" not in response.headers

    @staticmethod
    def _assert_security_headers(response: httpx.Response) -> None:
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/web/api/pair/claim"),
            ("GET", "/web/api/session"),
            ("GET", "/web/api/logout"),
            ("GET", "/web/api/search"),
        ],
    )
    def test_web_auth_exemptions_are_exact(self, engine: _Engine, method: str, path: str) -> None:
        assert engine.web_client.request(method, path).status_code == 401


class TestWebReadRoutes:
    _WEB_HEADERS = {
        "Origin": "https://web.test",
        "Sec-Fetch-Site": "same-origin",
    }

    def _session(self, engine: _Engine) -> None:
        response = engine.web_client.post(
            "/web/api/session",
            json={},
            headers={**engine.headers, **self._WEB_HEADERS},
        )
        assert response.status_code == 204

    def _seed(
        self,
        engine: _Engine,
        *,
        source: str = "https://example.com/private?token=source-secret",
        chapters: list[dict[str, object]] | None = None,
    ) -> tuple[str, str]:
        source_id = source_identity(source)
        library_dir = engine.data_dir / "library"
        edir = entry_dir(library_dir, source_id)
        edir.mkdir(parents=True)
        html = build_html(
            [{"start": 0.0, "end": 2.0, "text": "A private transcript."}],
            "Private episode",
            chapters=chapters,
        )
        html_path = edir / "episode.html"
        html_path.write_text(html)
        add_entry(
            library_dir,
            LibraryEntry(
                source_id=source_id,
                source=source,
                title="Private episode",
                html_path=str(html_path),
                created_at=1_700_000_123.0,
            ),
        )
        return source_id, html

    def test_public_shell_and_assets_are_data_free_and_hardened(self, engine: _Engine) -> None:
        source_id, _html = self._seed(engine)
        shell = engine.web_client.get("/web/")
        script = engine.web_client.get("/web/assets/app.js")
        stylesheet = engine.web_client.get("/web/assets/app.css")

        assert shell.status_code == script.status_code == stylesheet.status_code == 200
        assert shell.headers["content-security-policy"].startswith("default-src 'none'")
        assert script.headers["content-type"].startswith("text/javascript")
        assert stylesheet.headers["content-type"].startswith("text/css")
        combined = shell.text + script.text + stylesheet.text
        assert source_id not in combined
        assert engine.token not in combined
        assert "source-secret" not in combined
        assert "innerHTML" not in script.text
        assert "serviceWorker" not in script.text
        for response in (shell, script, stylesheet):
            assert response.headers["referrer-policy"] == "no-referrer"
            assert response.headers["x-content-type-options"] == "nosniff"

    def test_library_requires_cookie_and_returns_only_minimized_projection(
        self, engine: _Engine
    ) -> None:
        source_id, _html = self._seed(engine)
        assert engine.web_client.get("/web/api/library").status_code == 401
        self._session(engine)

        response = engine.web_client.get("/web/api/library")

        assert response.status_code == 200
        assert response.json() == [
            {
                "source_id": source_id,
                "title": "Private episode",
                "created_at": 1_700_000_123.0,
            }
        ]
        assert "source-secret" not in response.text
        assert "html_path" not in response.text
        assert response.headers["cache-control"] == "no-store"

    def test_search_requires_trusted_post_and_session_and_minimizes_results(
        self, engine: _Engine
    ) -> None:
        source_id, _html = self._seed(engine)
        path = "/web/api/search"
        assert (
            engine.web_client.post(
                path, json={"query": "private"}, headers=self._WEB_HEADERS
            ).status_code
            == 401
        )
        self._session(engine)
        assert engine.web_client.post(path, json={"query": "private"}).status_code == 403

        response = engine.web_client.post(
            path, json={"query": "private"}, headers=self._WEB_HEADERS
        )

        assert response.status_code == 200
        assert response.json() == {
            "results": [
                {
                    "source_id": source_id,
                    "title": "Private episode",
                    "excerpt": "A private transcript.",
                }
            ],
            "has_more": False,
            "partial": False,
        }
        assert "source-secret" not in response.text
        assert "html_path" not in response.text

    def test_search_validation_never_echoes_query(self, engine: _Engine) -> None:
        self._session(engine)
        secret = "search-private-canary-" * 8

        response = engine.web_client.post(
            "/web/api/search", json={"query": secret}, headers=self._WEB_HEADERS
        )

        assert response.status_code == 422
        assert secret not in response.text
        assert secret[:16] not in response.text

    def test_search_busy_is_detail_free_and_retryable(self, engine: _Engine) -> None:
        from podcast_reader.engine.app import _WEB_SEARCH_LOCK

        self._session(engine)
        assert _WEB_SEARCH_LOCK.acquire(blocking=False)
        try:
            response = engine.web_client.post(
                "/web/api/search", json={"query": "private"}, headers=self._WEB_HEADERS
            )
        finally:
            _WEB_SEARCH_LOCK.release()

        assert response.status_code == 429
        assert response.json() == {"detail": "search busy"}
        assert response.headers["retry-after"] == "1"

    def test_search_failure_releases_single_flight_lock(
        self, engine: _Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._seed(engine)
        self._session(engine)

        def fail_search(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("private-search-failure-canary")

        with monkeypatch.context() as context:
            context.setattr("podcast_reader.engine.app.search_library", fail_search)
            failed = engine.web_client.post(
                "/web/api/search", json={"query": "private"}, headers=self._WEB_HEADERS
            )
        recovered = engine.web_client.post(
            "/web/api/search", json={"query": "private"}, headers=self._WEB_HEADERS
        )

        assert failed.status_code == 500
        assert "private-search-failure-canary" not in failed.text
        assert recovered.status_code == 200

    @pytest.mark.parametrize(
        "chapters",
        [
            None,
            [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "title": "Opening",
                    "abstract": "The opening.",
                    "type": "content",
                    "key_points": [],
                }
            ],
        ],
    )
    def test_transcript_requires_cookie_and_is_byte_identical_with_exact_script_csp(
        self, engine: _Engine, chapters: list[dict[str, object]] | None
    ) -> None:
        source_id, html = self._seed(engine, chapters=chapters)
        path = f"/web/api/transcripts/{source_id}.html"
        assert engine.web_client.get(path).status_code == 401
        self._session(engine)

        response = engine.web_client.get(path)

        assert response.status_code == 200
        assert response.content == html.encode()
        csp = response.headers["content-security-policy"]
        scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
        assert scripts
        for script in scripts:
            digest = hashlib.sha256(script.encode()).digest()
            assert f"'sha256-{base64.b64encode(digest).decode()}'" in csp
        assert "frame-ancestors 'self'" in csp
        assert "connect-src 'none'" in csp
        assert response.headers["cache-control"] == "no-store"

    def test_unknown_invalid_and_missing_artifact_are_404_after_cookie(
        self, engine: _Engine
    ) -> None:
        self._session(engine)
        unknown = "a" * 64
        invalid = "not-a-source-id"
        assert engine.web_client.get(f"/web/api/transcripts/{unknown}.html").status_code == 404
        assert engine.web_client.get(f"/web/api/transcripts/{invalid}.html").status_code == 404

        source_id, _html = self._seed(engine)
        entry = get_entry(engine.data_dir / "library", source_id)
        assert entry is not None
        Path(entry["html_path"]).unlink()
        assert engine.web_client.get(f"/web/api/transcripts/{source_id}.html").status_code == 404

    def test_legacy_transcript_import_is_removed_before_csp_is_built(self, engine: _Engine) -> None:
        source_id, html = self._seed(engine)
        remote_import = (
            "@import url('https://fonts.googleapis.com/css2?"
            "family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;"
            "0,8..60,700;1,8..60,400&family=JetBrains+Mono:wght@400;600&"
            "family=Oswald:wght@400;500;600&display=swap');"
        )
        entry = get_entry(engine.data_dir / "library", source_id)
        assert entry is not None
        html_path = Path(entry["html_path"])
        legacy_document = html.encode().replace(
            b"<style>\n", f"<style>\n{remote_import}\n\n".encode(), 1
        )
        html_path.write_bytes(legacy_document)
        self._session(engine)

        response = engine.web_client.get(f"/web/api/transcripts/{source_id}.html")

        assert response.status_code == 200
        assert response.content == html.encode()
        assert html_path.read_bytes() == legacy_document
        assert "font-src 'none'" in response.headers["content-security-policy"]


def _cookie_line(domain: str, value: str = "secret-cookie-value") -> str:
    return f"{domain}\tTRUE\t/\tTRUE\t1900000000\tsession\t{value}"


class TestCookieRoutes:
    """Spec: Cookie jar endpoints with metadata-only readback."""

    def test_put_valid_jar_stored_owner_only(self, engine: _Engine) -> None:
        """Spec scenario: a well-formed jar for example.com lands at
        <data_dir>/cookies/example.com.txt with mode 0600, exact content."""
        jar = _cookie_line(".example.com") + "\n"
        response = engine.client.put(
            "/v1/cookies", json={"domain": "example.com", "jar": jar}, headers=engine.headers
        )
        assert response.status_code == 204
        path = engine.data_dir / "cookies" / "example.com.txt"
        assert path.read_text() == jar
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_put_foreign_domain_400_stores_nothing(self, engine: _Engine) -> None:
        """Spec scenario: foreign-domain cookies rejected — 400, nothing
        stored, and the detail carries no cookie values."""
        response = engine.client.put(
            "/v1/cookies",
            json={"domain": "example.com", "jar": _cookie_line("other.org")},
            headers=engine.headers,
        )
        assert response.status_code == 400
        assert not (engine.data_dir / "cookies" / "example.com.txt").exists()
        assert "secret-cookie-value" not in response.text

    def test_put_malformed_jar_400(self, engine: _Engine) -> None:
        """Spec scenario: a body that does not parse as Netscape lines is 400."""
        response = engine.client.put(
            "/v1/cookies",
            json={"domain": "example.com", "jar": "not a cookie jar"},
            headers=engine.headers,
        )
        assert response.status_code == 400

    def test_listing_exposes_no_cookie_values(self, engine: _Engine) -> None:
        """Spec scenario: GET /v1/cookies returns domains and timestamps only."""
        engine.client.put(
            "/v1/cookies",
            json={"domain": "example.com", "jar": _cookie_line("example.com")},
            headers=engine.headers,
        )
        response = engine.client.get("/v1/cookies", headers=engine.headers)
        assert response.status_code == 200
        (entry,) = response.json()
        assert set(entry) == {"domain", "created_at"}
        assert entry["domain"] == "example.com"
        assert "secret-cookie-value" not in response.text
        assert "session" not in response.text

    def test_delete_removes_the_jar(self, engine: _Engine) -> None:
        """Spec scenario: DELETE removes the jar and the domain leaves the
        listing."""
        engine.client.put(
            "/v1/cookies",
            json={"domain": "example.com", "jar": _cookie_line("example.com")},
            headers=engine.headers,
        )
        response = engine.client.delete("/v1/cookies/example.com", headers=engine.headers)
        assert response.status_code == 204
        assert engine.client.get("/v1/cookies", headers=engine.headers).json() == []
        assert not (engine.data_dir / "cookies" / "example.com.txt").exists()

    def test_delete_absent_jar_404(self, engine: _Engine) -> None:
        response = engine.client.delete("/v1/cookies/example.com", headers=engine.headers)
        assert response.status_code == 404

    def test_jar_content_never_in_responses_or_logs(
        self, engine: _Engine, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Task 2.5 sweep: jar content appears in no API response and no log."""
        jar = _cookie_line("example.com", value="super-secret-token")
        with caplog.at_level(logging.DEBUG):
            responses = [
                engine.client.put(
                    "/v1/cookies",
                    json={"domain": "example.com", "jar": jar},
                    headers=engine.headers,
                ),
                engine.client.get("/v1/cookies", headers=engine.headers),
                engine.client.get("/v1/settings", headers=engine.headers),
                engine.client.get("/v1/health", headers=engine.headers),
                engine.client.delete("/v1/cookies/example.com", headers=engine.headers),
            ]
        for response in responses:
            assert "super-secret-token" not in response.text
        assert "super-secret-token" not in caplog.text

    def test_jar_content_on_disk_only_in_the_jar_file(self, engine: _Engine) -> None:
        """Task 2.5 sweep: after a PUT and a job submission, the jar bytes
        exist in exactly one place on disk — the jar file itself; journal,
        settings, and every other engine file stay clean."""
        jar = _cookie_line("example.com", value="sweep-secret-value")
        engine.client.put(
            "/v1/cookies", json={"domain": "example.com", "jar": jar}, headers=engine.headers
        )
        engine.client.post(
            "/v1/jobs", json={"source": "https://example.com/a"}, headers=engine.headers
        )
        jar_file = engine.data_dir / "cookies" / "example.com.txt"
        for path in engine.data_dir.rglob("*"):
            if path.is_file() and path != jar_file:
                assert "sweep-secret-value" not in path.read_text(errors="replace"), path


class TestMediaRoutes:
    """media-playback: GET /v1/media/{id}/info and GET /v1/media/{id} (task 4)."""

    def _client(
        self, tmp_path: Path, entries: dict[str, LibraryEntry]
    ) -> tuple[TestClient, dict[str, str]]:
        from podcast_reader.engine.media import MediaManager

        store = JobStore(tmp_path, lambda record, on_event: _RESULT)
        manager = MediaManager(
            data_dir=tmp_path,
            bus=store.bus,
            cache_max_bytes=5 * 1024**3,
            get_entry=lambda sid: entries.get(sid),
        )
        app = create_app(tmp_path, store, media_manager=manager)
        token = load_engine_state(tmp_path)["token"]
        return TestClient(app), {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _entry(source_id: str, source: str) -> LibraryEntry:
        return LibraryEntry(
            source_id=source_id, source=source, title="t", html_path="/x.html", created_at=0.0
        )

    def test_info_requires_bearer(self, tmp_path: Path) -> None:
        sid = hashlib.sha256(b"yt").hexdigest()
        client, _ = self._client(tmp_path, {})
        assert client.get(f"/v1/media/{sid}/info").status_code == 401

    def test_info_youtube(self, tmp_path: Path) -> None:
        sid = hashlib.sha256(b"yt").hexdigest()
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        client, headers = self._client(tmp_path, {sid: self._entry(sid, url)})
        body = client.get(f"/v1/media/{sid}/info", headers=headers).json()
        assert body["kind"] == "youtube"
        assert body["youtube_id"] == "dQw4w9WgXcQ"
        assert body["status"] == "ready"

    def test_info_unknown_id_unavailable(self, tmp_path: Path) -> None:
        sid = hashlib.sha256(b"missing").hexdigest()
        client, headers = self._client(tmp_path, {})
        body = client.get(f"/v1/media/{sid}/info", headers=headers).json()
        assert body["kind"] == "unavailable"
        assert body["status"] == "unavailable"

    def test_invalid_source_id_rejected(self, tmp_path: Path) -> None:
        client, headers = self._client(tmp_path, {})
        assert client.get("/v1/media/not-a-hex-id/info", headers=headers).status_code == 404

    def test_bytes_served_for_local_file_with_range(self, tmp_path: Path) -> None:
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"0123456789" * 100)
        sid = hashlib.sha256(b"local").hexdigest()
        client, headers = self._client(tmp_path, {sid: self._entry(sid, str(media))})
        full = client.get(f"/v1/media/{sid}", headers=headers)
        assert full.status_code == 200
        assert full.content == media.read_bytes()
        ranged = client.get(f"/v1/media/{sid}", headers={**headers, "Range": "bytes=0-9"})
        assert ranged.status_code == 206
        assert ranged.content == b"0123456789"

    def test_bytes_404_when_not_ready(self, tmp_path: Path) -> None:
        sid = hashlib.sha256(b"yt2").hexdigest()
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        client, headers = self._client(tmp_path, {sid: self._entry(sid, url)})
        assert client.get(f"/v1/media/{sid}", headers=headers).status_code == 404
