"""Tests for podcast_reader.engine.process."""

from __future__ import annotations

import json
import os
import socket
import stat
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import httpx
import pytest

from podcast_reader.engine.app import create_app
from podcast_reader.engine.jobs import JobStore
from podcast_reader.engine.library import entry_dir, list_entries, source_identity
from podcast_reader.engine.process import (
    READY_SENTINEL,
    bind_engine_socket,
    bind_socket_option,
    make_pipeline_runner,
    popen_kwargs,
    remove_discovery,
    serve_engine,
    write_discovery,
)
from podcast_reader.engine.settings import (
    engine_version,
    load_engine_state,
    load_settings,
    save_settings,
    token_fingerprint,
)
from podcast_reader.tools import live_children, run_child
from podcast_reader.types import PipelineResult, new_job_record

if TYPE_CHECKING:
    from collections.abc import Callable

    import uvicorn

    from podcast_reader.engine.settings import EngineState


def _noop(event: object) -> None:
    """Discard pipeline events."""


def _fake_run_pipeline(request: object, on_event: object) -> PipelineResult:
    """Stand-in pipeline: writes minimal artifacts into the staging dir."""
    out = Path(request["output_dir"])  # type: ignore[index]
    (out / "ep.json").write_text('{"segments": []}')
    (out / "ep.html").write_text("<html>done</html>")
    return PipelineResult(
        json_path=str(out / "ep.json"),
        chapters_path=None,
        html_path=str(out / "ep.html"),
        title="Episode",
    )


def _wait_for(predicate: Callable[[], bool], timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class TestBindAndDiscovery:
    def test_bind_persists_port_and_writes_discovery(self, tmp_path: Path) -> None:
        state = load_engine_state(tmp_path)
        sock = bind_engine_socket(tmp_path, state)
        try:
            port = sock.getsockname()[1]
            assert port != 0
            assert state["port"] == port
            assert load_engine_state(tmp_path)["port"] == port

            discovery = tmp_path / "engine.json"
            write_discovery(discovery, state, sock)
            info = json.loads(discovery.read_text())
            assert info == {
                "port": port,
                "pid": os.getpid(),
                "token_fingerprint": token_fingerprint(state["token"]),
                "version": engine_version(),
            }
            assert stat.S_IMODE(discovery.stat().st_mode) == 0o600
            assert list(tmp_path.glob("*.tmp")) == []
        finally:
            sock.close()

        # second start reuses the persisted port
        reused: EngineState = load_engine_state(tmp_path)
        sock2 = bind_engine_socket(tmp_path, reused)
        try:
            assert sock2.getsockname()[1] == port
        finally:
            sock2.close()

    def test_bind_falls_back_when_persisted_port_taken(self, tmp_path: Path) -> None:
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        taken_port = blocker.getsockname()[1]
        try:
            state = load_engine_state(tmp_path)
            state["port"] = taken_port
            sock = bind_engine_socket(tmp_path, state)
            try:
                new_port = sock.getsockname()[1]
                assert new_port != taken_port
                assert load_engine_state(tmp_path)["port"] == new_port
            finally:
                sock.close()
        finally:
            blocker.close()

    def test_bind_socket_option_is_platform_selected(self) -> None:
        """POSIX uses SO_REUSEADDR (TIME_WAIT rebinding; live listeners still
        EADDRINUSE). Windows must not: SO_REUSEADDR there allows binding an
        actively-bound port, defeating the fallback — SO_EXCLUSIVEADDRUSE instead."""
        assert bind_socket_option("linux") == socket.SO_REUSEADDR
        assert bind_socket_option("darwin") == socket.SO_REUSEADDR
        assert bind_socket_option("win32") != socket.SO_REUSEADDR
        assert bind_socket_option("win32") == ~4  # winsock SO_EXCLUSIVEADDRUSE

    def test_discovery_removed_on_close(self, tmp_path: Path) -> None:
        state = load_engine_state(tmp_path)
        sock = bind_engine_socket(tmp_path, state)
        discovery = tmp_path / "engine.json"
        write_discovery(discovery, state, sock)
        sock.close()
        assert discovery.exists()
        remove_discovery(discovery)
        assert not discovery.exists()
        remove_discovery(discovery)  # idempotent

    def test_sentinel_printed_after_discovery(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = load_engine_state(tmp_path)
        sock = bind_engine_socket(tmp_path, state)
        discovery = tmp_path / "engine.json"
        printed: list[tuple[tuple[object, ...], bool]] = []
        monkeypatch.setattr(
            "builtins.print",
            lambda *args, **kwargs: printed.append((args, discovery.exists())),
        )
        write_discovery(discovery, state, sock)
        sock.close()
        # exactly one sentinel line, printed only once the file existed
        assert printed == [((READY_SENTINEL,), True)]


class TestChildManagement:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
    def test_popen_kwargs_posix(self) -> None:
        assert popen_kwargs() == {"start_new_session": True}

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
    def test_children_spawned_in_new_session(self, tmp_path: Path) -> None:
        """Tool call sites go through run_child, which spawns each child in its
        own session and registers it for shutdown reaping."""
        from podcast_reader.transcribe import transcribe
        from podcast_reader.ytdlp import fetch_title

        def _proc(stdout: str) -> MagicMock:
            proc = MagicMock(pid=12345)
            proc.communicate.return_value = (stdout, "")
            proc.wait.return_value = 0
            return proc

        with patch("podcast_reader.tools.subprocess.Popen", return_value=_proc("Title\n")) as popen:
            fetch_title("https://x.com/user/status/1")
        assert popen.call_args.kwargs["start_new_session"] is True

        with patch("podcast_reader.tools.subprocess.Popen", return_value=_proc("")) as popen:
            transcribe(
                audio_path=tmp_path / "a.mp3",
                output_dir=tmp_path,
                model="tiny",
                lang="en",
                device="cpu",
            )
        assert popen.call_args.kwargs["start_new_session"] is True

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
    def test_engine_shutdown_terminates_children(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: engine shutdown terminates a running child subprocess."""
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))

        def fake_runner(record: object, on_event: Callable[[object], None]) -> PipelineResult:
            run_child([sys.executable, "-c", "import time; time.sleep(60)"])
            return PipelineResult(json_path="", chapters_path=None, html_path="", title="t")

        monkeypatch.setattr(
            "podcast_reader.engine.process.make_pipeline_runner",
            lambda base, key_store=None: fake_runner,
        )
        discovery = tmp_path / "discovery.json"
        servers: list[uvicorn.Server] = []
        thread = threading.Thread(
            target=serve_engine,
            kwargs={"discovery_file": discovery, "on_server": servers.append},
            daemon=True,
        )
        thread.start()
        assert _wait_for(discovery.exists), "discovery file never appeared"

        info = json.loads(discovery.read_text())
        token = load_engine_state(tmp_path)["token"]
        headers = {"Authorization": f"Bearer {token}"}
        url = f"http://127.0.0.1:{info['port']}/v1/jobs"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                body = {"source": "https://example.com/a"}
                httpx.post(url, json=body, headers=headers, timeout=2)
                break
            except httpx.TransportError:
                time.sleep(0.05)

        assert _wait_for(lambda: len(live_children()) == 1), "child never registered"
        child_pid = live_children()[0]

        servers[0].should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive()
        with pytest.raises(ProcessLookupError):
            os.killpg(child_pid, 0)  # the child's process group is gone


class TestPipelineRunner:
    def test_runner_commits_artifacts_to_library(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        source = "https://example.com/episode"

        with patch(
            "podcast_reader.engine.process.run_pipeline", side_effect=_fake_run_pipeline
        ) as mock_run:
            runner = make_pipeline_runner(tmp_path)
            record = new_job_record(job_id="j1", source=source, title="Episode")
            result = runner(record, lambda e: None)

        settings = load_settings(tmp_path)
        library_dir = Path(settings["library_dir"])
        source_id = source_identity(source)
        edir = entry_dir(library_dir, source_id)

        # pipeline ran inside the entry's staging dir (settings snapshot applied)
        request = mock_run.call_args.args[0]
        assert Path(request["output_dir"]) == edir / "staging"
        assert request["whisper_model"] == settings["whisper_model"]

        # committed artifacts live in the entry dir; staging keeps its cache copy
        assert (edir / "ep.json").read_text() == '{"segments": []}'
        assert (edir / "ep.html").read_text() == "<html>done</html>"
        assert (edir / "staging" / "ep.json").exists()
        assert result["html_path"] == str(edir / "ep.html")
        assert result["json_path"] == str(edir / "ep.json")

        # and the library index gained the entry
        entries = list_entries(library_dir)
        assert [e["source_id"] for e in entries] == [source_id]
        assert entries[0]["html_path"] == str(edir / "ep.html")
        assert entries[0]["title"] == "Episode"

    def test_runner_falls_back_to_provider_env_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: Headless env var still works — no pushed key means the
        configured provider's env var is injected at job dequeue."""
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")

        with patch(
            "podcast_reader.engine.process.run_pipeline", side_effect=_fake_run_pipeline
        ) as mock_run:
            runner = make_pipeline_runner(tmp_path)
            runner(new_job_record(job_id="j1", source="https://example.com/e", title=None), _noop)

        request = mock_run.call_args.args[0]
        assert request["chapter_provider"] == "anthropic"
        assert request["chapter_api_key"] == "sk-ant-env"
        assert request["model"] is None  # chapter_model "" -> provider default

    def test_runner_provider_comes_from_settings_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: Provider change applies to the next job (snapshot at dequeue)."""
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-env")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
        settings = load_settings(tmp_path)
        settings["chapter_provider"] = "deepseek"
        save_settings(tmp_path, settings)

        with patch(
            "podcast_reader.engine.process.run_pipeline", side_effect=_fake_run_pipeline
        ) as mock_run:
            runner = make_pipeline_runner(tmp_path)
            runner(new_job_record(job_id="j1", source="https://example.com/e", title=None), _noop)

        request = mock_run.call_args.args[0]
        assert request["chapter_provider"] == "deepseek"
        assert request["chapter_api_key"] == "sk-ds-env"

    def test_pushed_key_wins_over_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: Pushed key wins over env."""
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")

        with patch(
            "podcast_reader.engine.process.run_pipeline", side_effect=_fake_run_pipeline
        ) as mock_run:
            runner = make_pipeline_runner(tmp_path, key_store={"anthropic": "sk-ant-pushed"})
            runner(new_job_record(job_id="j1", source="https://example.com/e", title=None), _noop)

        assert mock_run.call_args.args[0]["chapter_api_key"] == "sk-ant-pushed"

    def test_restart_loses_pushed_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: Keys do not survive restart — a fresh key store plus no
        env var injects no key, and the pipeline's missing-key path then skips
        chapters with ``chapters_skipped`` (covered by the pipeline tests)."""
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        before_restart = {"anthropic": "sk-ant-pushed"}  # key pushed into the old process
        fresh_store: dict[str, str] = {}  # what a restarted serve_engine creates
        assert before_restart != fresh_store

        with patch(
            "podcast_reader.engine.process.run_pipeline", side_effect=_fake_run_pipeline
        ) as mock_run:
            runner = make_pipeline_runner(tmp_path, key_store=fresh_store)
            runner(new_job_record(job_id="j1", source="https://example.com/e", title=None), _noop)

        assert mock_run.call_args.args[0]["chapter_api_key"] is None

    def test_missing_local_file_fails_with_structured_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A nonexistent local source must fail code="not_found", not "internal"."""
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        store = JobStore(tmp_path, make_pipeline_runner(tmp_path))
        record = store.submit(str(tmp_path / "missing.mp3"), None)
        store.start_worker()
        assert _wait_for(lambda: store.get(record["id"])["state"] == "failed")
        error = store.get(record["id"])["error"]
        assert error is not None
        assert error["code"] == "not_found"
        assert "File not found" in error["message"]
        store.shutdown()


class TestServeKeyStoreWiring:
    def test_serve_engine_shares_one_key_store_between_runner_and_app(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Per K7: serve_engine creates the key store and passes the same dict
        object to both make_pipeline_runner and create_app (the runner closure
        is constructed before the app, so app state cannot host it)."""
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        captured: dict[str, object] = {}

        real_make = make_pipeline_runner
        real_create = create_app

        def spy_make(base: Path, key_store: dict[str, str] | None = None) -> object:
            captured["runner_store"] = key_store
            return real_make(base, key_store=key_store)

        def spy_create(
            data_dir: Path,
            store: object,
            *,
            key_store: dict[str, str] | None = None,
            on_shutdown: Callable[[], None] | None = None,
        ) -> object:
            captured["app_store"] = key_store
            return real_create(  # type: ignore[arg-type]
                data_dir, store, key_store=key_store, on_shutdown=on_shutdown
            )

        monkeypatch.setattr("podcast_reader.engine.process.make_pipeline_runner", spy_make)
        monkeypatch.setattr("podcast_reader.engine.process.create_app", spy_create)
        monkeypatch.setattr(
            "uvicorn.Server.run", lambda self, sockets=None: None
        )  # serve and return immediately

        serve_engine(discovery_file=tmp_path / "discovery.json")

        assert isinstance(captured["runner_store"], dict)
        assert captured["runner_store"] is captured["app_store"]


class TestShutdownEndpointLifecycle:
    """Spec: Graceful shutdown endpoint — endpoint-triggered exit runs the
    full serve_engine cleanup path, bounded even under a live SSE subscriber."""

    def test_shutdown_endpoint_exits_bounded_with_live_sse_and_interrupts_job(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenarios: Shutdown stops the engine cleanly; Open SSE stream
        cannot block shutdown (per P1); Shutdown mid-job interrupts
        recoverably (per P2).

        A job is mid-run and an SSE subscriber is attached when
        POST /v1/shutdown is called: the process must still exit within the
        bounded graceful-shutdown window, the finally cleanup must run
        (discovery file removed), and the job — whose runner fails when
        kill_children reaps its child — must be journaled ``interrupted``.
        """
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        release = threading.Event()
        started = threading.Event()

        def fake_runner(record: object, on_event: Callable[[object], None]) -> PipelineResult:
            started.set()
            assert release.wait(timeout=30)
            raise RuntimeError("child terminated by shutdown")

        monkeypatch.setattr(
            "podcast_reader.engine.process.make_pipeline_runner",
            lambda base, key_store=None: fake_runner,
        )
        # The "children reaped" moment is what makes the in-flight job fail;
        # begin_shutdown runs before it, so the failure lands as interrupted.
        monkeypatch.setattr("podcast_reader.engine.process.kill_children", release.set)

        discovery = tmp_path / "discovery.json"
        servers: list[uvicorn.Server] = []
        thread = threading.Thread(
            target=serve_engine,
            kwargs={"discovery_file": discovery, "on_server": servers.append},
            daemon=True,
        )
        thread.start()
        assert _wait_for(discovery.exists), "discovery file never appeared"
        # serve_engine bounds graceful shutdown regardless of supervisor (per P1)
        assert servers[0].config.timeout_graceful_shutdown == 3

        info = json.loads(discovery.read_text())
        token = load_engine_state(tmp_path)["token"]
        headers = {"Authorization": f"Bearer {token}"}
        base_url = f"http://127.0.0.1:{info['port']}"

        job_id: str | None = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                submitted = httpx.post(
                    f"{base_url}/v1/jobs",
                    json={"source": "https://example.com/a"},
                    headers=headers,
                    timeout=2,
                )
                job_id = submitted.json()["id"]
                break
            except httpx.TransportError:
                time.sleep(0.05)
        assert job_id is not None
        assert started.wait(timeout=10), "job never started running"

        with httpx.stream("GET", f"{base_url}/v1/events", headers=headers, timeout=30) as stream:
            assert stream.status_code == 200  # live subscriber attached
            response = httpx.post(f"{base_url}/v1/shutdown", headers=headers, timeout=10)
            assert response.status_code == 202
            begun = time.monotonic()
            thread.join(timeout=20)
            assert not thread.is_alive(), "engine did not exit after POST /v1/shutdown"
            assert time.monotonic() - begun < 15  # bounded despite the open stream

        # the finally cleanup ran
        assert not discovery.exists(), "discovery file must be removed on shutdown"
        # per P2: the job that failed while stopping is journaled interrupted
        journal = {r["id"]: r for r in json.loads((tmp_path / "jobs.json").read_text())}
        assert journal[job_id]["state"] == "interrupted"


class TestServeSmoke:
    def test_serve_smoke(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        discovery = tmp_path / "discovery.json"
        servers: list[uvicorn.Server] = []

        thread = threading.Thread(
            target=serve_engine,
            kwargs={"discovery_file": discovery, "on_server": servers.append},
            daemon=True,
        )
        thread.start()
        assert _wait_for(discovery.exists), "discovery file never appeared"

        info = json.loads(discovery.read_text())
        token = load_engine_state(tmp_path)["token"]
        url = f"http://127.0.0.1:{info['port']}/v1/health"
        headers = {"Authorization": f"Bearer {token}"}

        response: httpx.Response | None = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                response = httpx.get(url, headers=headers, timeout=2)
                break
            except httpx.TransportError:
                time.sleep(0.05)
        assert response is not None
        assert response.status_code == 200
        assert response.json()["token_fingerprint"] == info["token_fingerprint"]

        # health without the token is rejected even over the live socket
        assert httpx.get(url, timeout=2).status_code == 401

        servers[0].should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive()
        assert not discovery.exists(), "clean shutdown must remove the discovery file"

    def test_serve_cleans_up_when_discovery_write_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A discovery-write failure must still close the socket and stop the worker."""
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        shutdowns: list[bool] = []
        orig_shutdown = JobStore.shutdown

        def spy_shutdown(store: JobStore) -> None:
            shutdowns.append(True)
            orig_shutdown(store)

        monkeypatch.setattr(JobStore, "shutdown", spy_shutdown)
        monkeypatch.setattr(
            "podcast_reader.engine.process.write_discovery",
            MagicMock(side_effect=OSError("disk full")),
        )
        with pytest.raises(OSError, match="disk full"):
            serve_engine(discovery_file=tmp_path / "discovery.json")
        assert shutdowns == [True]
        # the engine socket was closed: its port is immediately bindable again
        port = load_engine_state(tmp_path)["port"]
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
        finally:
            probe.close()
