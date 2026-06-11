"""Worker restart tests for podcast_reader.engine.jobs (cubic D7 + races).

``start_worker()`` after ``shutdown()`` on the *same* JobStore instance must
process jobs again, a restart racing a slow shutdown must not revive the old
worker, and the backlog must reach the successor untouched and in FIFO order.
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

    def test_shutdown_during_job_leaves_backlog_for_restarted_worker(self, tmp_path: Path) -> None:
        """shutdown() during a running job must not poison the queue for the
        next worker (D7; originally a stale-sentinel scenario, now sentinel-free).

        Sequence: job A runs (blocking), job B is queued, shutdown() stops the
        worker after A. A restarted worker must keep processing new jobs.
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

    def test_restart_during_slow_shutdown_does_not_revive_old_worker(self, tmp_path: Path) -> None:
        """start_worker() racing a shutdown() blocked on a slow job must not
        clear the old worker's stop signal (cubic P1 on the D7 fix).

        With a shared, clearable stop event the old worker — released after
        the restart — would see stop cleared, drain the backlog, and loop
        forever alongside the new worker. Per-generation stop events keep the
        old worker's signal set regardless of restarts.
        """
        release = threading.Event()
        started = threading.Event()
        run_order: list[str] = []

        def blocking(
            record: JobRecord, on_event: Callable[[PipelineEvent], None]
        ) -> PipelineResult:
            run_order.append(record["source"])
            if record["source"].endswith("/first"):
                started.set()
                assert release.wait(timeout=10)
            return _RESULT

        store = JobStore(tmp_path, blocking)
        store.start_worker()
        first = store.submit("https://example.com/first", None)
        second = store.submit("https://example.com/second", None)  # backlog
        third = store.submit("https://example.com/third", None)  # backlog
        assert started.wait(timeout=10)

        old_stop = store._stop
        old_worker = store._worker
        assert old_worker is not None
        shutdown_thread = threading.Thread(target=store.shutdown)
        shutdown_thread.start()  # blocks joining the worker mid-job
        assert _wait_for(old_stop.is_set)

        # Restart while the old worker is still finishing its job.
        store.start_worker()
        assert old_stop.is_set(), "restart must not clear the old worker's stop signal"
        new_worker = store._worker
        assert new_worker is not None
        assert new_worker is not old_worker

        release.set()
        shutdown_thread.join(timeout=10)
        assert not shutdown_thread.is_alive()

        # The new worker processes the backlog; the old worker exits.
        assert _wait_for(lambda: store.get(third["id"])["state"] == "done")
        assert store.get(first["id"])["state"] == "done"
        assert store.get(second["id"])["state"] == "done"
        assert _wait_for(lambda: not old_worker.is_alive()), "old worker must exit"
        assert new_worker.is_alive()
        # FIFO survives the hand-back: a job the exiting worker dequeued goes
        # back to the FRONT of the queue, not the tail (cubic P2).
        assert run_order == [
            "https://example.com/first",
            "https://example.com/second",
            "https://example.com/third",
        ]
        store.shutdown()
