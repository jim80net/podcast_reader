"""Worker restart tests for podcast_reader.engine.jobs (cubic D7).

Kept separate from test_jobs.py: ``start_worker()`` after ``shutdown()`` on
the *same* JobStore instance must process jobs again — the stop event has to
be reset and a stale wake-up sentinel must not kill the fresh worker.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from podcast_reader.engine.jobs import JobStore
from podcast_reader.types import PipelineEvent, PipelineResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from podcast_reader.types import JobRecord

_RESULT = PipelineResult(
    json_path="/lib/aaa/a.json",
    chapters_path=None,
    html_path="/lib/aaa/a.html",
    title="Title",
)


def _ok_runner(record: JobRecord, on_event: Callable[[PipelineEvent], None]) -> PipelineResult:
    on_event(PipelineEvent(kind="job_done", step=None, message="Done", data={}))
    return _RESULT


def _wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestWorkerRestart:
    def test_start_worker_after_shutdown_processes_jobs(self, tmp_path: Path) -> None:
        """shutdown() then start_worker() on the same instance must run jobs (D7)."""
        store = JobStore(tmp_path, _ok_runner)
        store.start_worker()
        first = store.submit("https://example.com/first", None)
        assert _wait_for(lambda: store.get(first["id"])["state"] == "done")
        store.shutdown()

        store.start_worker()
        second = store.submit("https://example.com/second", None)
        assert _wait_for(lambda: store.get(second["id"])["state"] == "done")
        store.shutdown()

    def test_stale_shutdown_sentinel_does_not_kill_restarted_worker(self, tmp_path: Path) -> None:
        """A None wake-up sentinel left over from shutdown() must be ignored
        by the next worker, not treated as an exit signal (D7).

        Sequence: job A runs (blocking), job B is queued, shutdown() sets stop
        and enqueues the sentinel. The exiting worker dequeues B (not the
        sentinel) and returns, leaving None at the head of the queue for the
        restarted worker.
        """
        release = threading.Event()
        started = threading.Event()

        def blocking(
            record: JobRecord, on_event: Callable[[PipelineEvent], None]
        ) -> PipelineResult:
            started.set()
            assert release.wait(timeout=10)
            return _RESULT

        store = JobStore(tmp_path, blocking)
        store.start_worker()
        first = store.submit("https://example.com/first", None)
        store.submit("https://example.com/second", None)  # stays queued
        assert started.wait(timeout=10)

        shutdown_thread = threading.Thread(target=store.shutdown)
        shutdown_thread.start()
        assert _wait_for(store._stop.is_set)
        release.set()
        shutdown_thread.join(timeout=10)
        assert not shutdown_thread.is_alive()
        assert store.get(first["id"])["state"] == "done"

        release.set()
        started.clear()
        store.start_worker()
        third = store.submit("https://example.com/third", None)
        assert _wait_for(lambda: store.get(third["id"])["state"] == "done")
        store.shutdown()
